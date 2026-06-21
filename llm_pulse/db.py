"""SQLite-backed time-series storage for scrape snapshots."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("llm-pulse")

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
    conn.commit()

    logger.info("SQLite database opened at %s", db_path)
    return conn


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
    raw_vals = ", ".join(str(raw.get(k, 0.0)) for k in METRIC_KEYS)
    delta_vals = ", ".join(str(deltas.get(k, 0.0)) for k in METRIC_KEYS)

    conn.execute(
        f"INSERT INTO scrapes (ts, is_reset, {raw_cols}, {delta_cols}) "
        f"VALUES (?, ?, {raw_vals}, {delta_vals})",
        (ts, 1 if is_reset else 0),
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
    row = conn.execute(
        f"SELECT {cols} FROM scrapes WHERE ts >= ?", (start_ts,)
    ).fetchone()
    return {k: row[k] for k in METRIC_KEYS}


def get_history(conn: sqlite3.Connection, limit: int = 168) -> list[dict]:
    """Return the most recent ``limit`` scrape snapshots as a list of dicts.

    Args:
        conn: SQLite connection.
        limit: Maximum number of snapshots to return.

    Returns:
        List of dicts, each with ``ts``, ``is_reset``, raw values, and delta values.
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
            "timestamp": datetime.fromtimestamp(row["ts"], tz=timezone.utc).isoformat(),
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
    conn.commit()
    deleted = cursor.rowcount
    if deleted:
        logger.info(
            "Purged %d old scrapes (older than %d days)", deleted, retention_days
        )
    return deleted
