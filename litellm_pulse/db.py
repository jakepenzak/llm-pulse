"""SQLite-backed time-series storage for scrape snapshots."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, tzinfo
from pathlib import Path

logger = logging.getLogger("litellm-pulse")

# All friendly metric names — defines the columns in the scrapes table.
METRIC_KEYS = [
    "requests",
    "failed_requests",
    "tokens",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost",
    "in_flight_requests",
    "cache_hits",
    "cache_misses",
    "cached_tokens",
    "input_cached_tokens",
    "input_cache_creation_tokens",
]


def open_db(path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database with WAL mode and the scrapes table.

    Args:
        path: Filesystem path to the .db file. Parent directories are created.

    Returns:
        A sqlite3 Connection with row factory set, WAL mode enabled.
    """
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    columns = [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "ts INTEGER NOT NULL",
        "is_reset INTEGER DEFAULT 0",
    ]
    for key in METRIC_KEYS:
        columns.append(f"raw_{key} REAL DEFAULT 0")
    for key in METRIC_KEYS:
        columns.append(f"delta_{key} REAL DEFAULT 0")

    conn.execute(f"CREATE TABLE IF NOT EXISTS scrapes ({', '.join(columns)})")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scrapes_ts ON scrapes(ts)")

    _migrate_scrapes_columns(conn)

    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_snapshots ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts INTEGER NOT NULL, "
        "model TEXT NOT NULL, "
        "metric TEXT NOT NULL, "
        "raw REAL DEFAULT 0, "
        "delta REAL DEFAULT 0, "
        "is_reset INTEGER DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_snapshots_ts ON model_snapshots(ts)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_snapshots_model_metric "
        "ON model_snapshots(model, metric)"
    )

    conn.commit()

    logger.info("SQLite database opened at %s", db_path)
    return conn


def _migrate_scrapes_columns(conn: sqlite3.Connection) -> None:
    """Add missing raw_* and delta_* columns to an existing scrapes table.

    Handles databases created before new metrics were added.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(scrapes)").fetchall()}
    for key in METRIC_KEYS:
        for prefix in ("raw_", "delta_"):
            col = f"{prefix}{key}"
            if col not in existing:
                conn.execute(f"ALTER TABLE scrapes ADD COLUMN {col} REAL DEFAULT 0")
                logger.info("Migrated scrapes table: added column %s", col)


def store_snapshot(
    conn: sqlite3.Connection,
    ts: int,
    raw: dict[str, float],
    deltas: dict[str, float],
    is_reset: bool,
) -> None:
    """Store one scrape snapshot with raw cumulative values and precomputed deltas.

    Args:
        conn: SQLite connection.
        ts: Unix epoch timestamp (seconds).
        raw: Mapping of friendly metric names to raw cumulative values.
        deltas: Mapping of friendly metric names to delta values.
        is_reset: Whether a counter reset was detected this scrape.
    """
    raw_cols = ", ".join(f"raw_{k}" for k in METRIC_KEYS)
    delta_cols = ", ".join(f"delta_{k}" for k in METRIC_KEYS)
    value_placeholders = ", ".join("?" for _ in METRIC_KEYS)

    params: list = [ts, 1 if is_reset else 0]
    params.extend(raw.get(k, 0.0) for k in METRIC_KEYS)
    params.extend(deltas.get(k, 0.0) for k in METRIC_KEYS)

    conn.execute(
        f"INSERT INTO scrapes (ts, is_reset, {raw_cols}, {delta_cols}) "
        f"VALUES (?, ?, {value_placeholders}, {value_placeholders})",
        params,
    )
    conn.commit()


def get_latest(conn: sqlite3.Connection) -> dict[str, float] | None:
    """Return the raw cumulative values from the most recent scrape, or None.

    Args:
        conn: SQLite connection.

    Returns:
        Dict mapping friendly metric names to raw cumulative values, or None
        if the database is empty.
    """
    row = conn.execute("SELECT * FROM scrapes ORDER BY ts DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {k: row[f"raw_{k}"] for k in METRIC_KEYS}


def get_latest_ts(conn: sqlite3.Connection) -> int | None:
    """Return the Unix timestamp of the most recent scrape, or None if empty."""
    row = conn.execute("SELECT ts FROM scrapes ORDER BY ts DESC LIMIT 1").fetchone()
    return row["ts"] if row else None


def get_window_aggregate(conn: sqlite3.Connection, start_ts: int) -> dict[str, float]:
    """Return SUM of deltas for all scrapes at or after ``start_ts``.

    Args:
        conn: SQLite connection.
        start_ts: Unix epoch timestamp (seconds) — start of the aggregation window.

    Returns:
        Dict mapping friendly metric names to summed deltas over the window.
    """
    cols = ", ".join(f"COALESCE(SUM(delta_{k}), 0) AS {k}" for k in METRIC_KEYS)
    row = conn.execute(f"SELECT {cols} FROM scrapes WHERE ts >= ?", (start_ts,)).fetchone()
    return {k: row[k] for k in METRIC_KEYS}


def get_history(conn: sqlite3.Connection, limit: int = 168, tz: tzinfo = UTC) -> list[dict]:
    """Return the most recent ``limit`` scrape snapshots as a list of dicts.

    Args:
        conn: SQLite connection.
        limit: Maximum number of snapshots to return.
        tz: Timezone to use when formatting the ``timestamp`` field (the DB
            always stores UTC Unix timestamps; conversion happens here).

    Returns:
        List of dicts, each with ``timestamp`` (ISO string in ``tz``),
        ``is_reset``, raw values, and delta values.
    """
    rows = conn.execute(
        f"SELECT ts, is_reset, {', '.join(f'raw_{k}' for k in METRIC_KEYS)}, "
        f"{', '.join(f'delta_{k}' for k in METRIC_KEYS)} "
        f"FROM scrapes ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()

    results = []
    for row in rows:
        entry: dict[str, float | int | str] = {
            "timestamp": datetime.fromtimestamp(row["ts"], tz=tz).isoformat(),
            "is_reset": bool(row["is_reset"]),
        }
        for k in METRIC_KEYS:
            entry[k] = row[f"raw_{k}"]
            entry[f"{k}_delta"] = row[f"delta_{k}"]
        results.append(entry)

    results.reverse()
    return results


def purge_old(conn: sqlite3.Connection, retention_days: int) -> int:
    """Delete scrapes older than ``retention_days`` days.

    Args:
        conn: SQLite connection.
        retention_days: Number of days of data to retain.

    Returns:
        Number of rows deleted.
    """
    cursor = conn.execute(
        "DELETE FROM scrapes WHERE ts < strftime('%s', 'now', ?)",
        (f"-{retention_days} days",),
    )
    model_cursor = conn.execute(
        "DELETE FROM model_snapshots WHERE ts < strftime('%s', 'now', ?)",
        (f"-{retention_days} days",),
    )
    conn.commit()
    deleted = cursor.rowcount + model_cursor.rowcount
    if deleted:
        logger.info("Purged %d old rows (older than %d days)", deleted, retention_days)
    return deleted


# ---------------------------------------------------------------------------
# Per-model snapshot storage
# ---------------------------------------------------------------------------


def store_model_snapshots(
    conn: sqlite3.Connection,
    ts: int,
    raw: dict[str, dict[str, float]],
    deltas: dict[str, dict[str, float]],
    is_reset: bool,
) -> None:
    """Store per-model metric snapshots for one scrape.

    Args:
        conn: SQLite connection.
        ts: Unix epoch timestamp (seconds).
        raw: Nested dict ``{metric: {model: value}}`` of raw cumulative values.
        deltas: Nested dict ``{metric: {model: delta}}`` of delta values.
        is_reset: Whether a counter reset was detected this scrape.
    """
    reset_val = 1 if is_reset else 0
    rows = []
    for metric, models in raw.items():
        for model, value in models.items():
            delta = deltas.get(metric, {}).get(model, 0.0)
            rows.append((ts, model, metric, value, delta, reset_val))

    if rows:
        conn.executemany(
            "INSERT INTO model_snapshots (ts, model, metric, raw, delta, is_reset) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def get_latest_model_metrics(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Return the raw cumulative values from the most recent model scrape.

    Args:
        conn: SQLite connection.

    Returns:
        Nested dict ``{metric: {model: value}}``, or empty dict if no data.
    """
    row = conn.execute("SELECT MAX(ts) as max_ts FROM model_snapshots").fetchone()
    if row["max_ts"] is None:
        return {}

    rows = conn.execute(
        "SELECT model, metric, raw FROM model_snapshots WHERE ts = ?",
        (row["max_ts"],),
    ).fetchall()

    result: dict[str, dict[str, float]] = {}
    for r in rows:
        result.setdefault(r["metric"], {})[r["model"]] = r["raw"]
    return result


def get_model_window_aggregate(
    conn: sqlite3.Connection, start_ts: int
) -> dict[str, dict[str, float]]:
    """Return SUM of per-model deltas for all scrapes at or after ``start_ts``.

    Args:
        conn: SQLite connection.
        start_ts: Unix epoch timestamp — start of the aggregation window.

    Returns:
        Nested dict ``{model: {metric: summed_delta}}``.
    """
    rows = conn.execute(
        "SELECT model, metric, COALESCE(SUM(delta), 0) as total "
        "FROM model_snapshots WHERE ts >= ? "
        "GROUP BY model, metric",
        (start_ts,),
    ).fetchall()

    result: dict[str, dict[str, float]] = {}
    for r in rows:
        result.setdefault(r["model"], {})[r["metric"]] = r["total"]
    return result
