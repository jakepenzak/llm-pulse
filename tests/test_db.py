"""Tests for the SQLite time-series storage module."""

import sqlite3
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from litellm_pulse.db import (
    METRIC_KEYS,
    get_history,
    get_latest,
    get_latest_model_metrics,
    get_latest_ts,
    get_model_window_aggregate,
    get_window_aggregate,
    open_db,
    purge_old,
    store_model_snapshots,
    store_snapshot,
)


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-style SQLite DB in a temp directory."""
    db_path = str(tmp_path / "test.db")
    conn = open_db(db_path)
    yield conn
    conn.close()


def _make_raw(**overrides) -> dict[str, float]:
    raw = {k: 0.0 for k in METRIC_KEYS}
    raw.update(overrides)
    return raw


def _make_deltas(**overrides) -> dict[str, float]:
    deltas = {k: 0.0 for k in METRIC_KEYS}
    deltas.update(overrides)
    return deltas


class TestOpenDb:
    def test_creates_table(self, db):
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = [t["name"] for t in tables]
        assert "scrapes" in names

    def test_creates_index(self, db):
        indexes = db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        names = [i["name"] for i in indexes]
        assert "idx_scrapes_ts" in names

    def test_creates_parent_dir(self, tmp_path):
        nested = str(tmp_path / "nested" / "deep" / "test.db")
        conn = open_db(nested)
        conn.close()
        assert (tmp_path / "nested" / "deep" / "test.db").exists()

    def test_wal_mode(self, db):
        mode = db.execute("PRAGMA journal_mode").fetchone()
        assert mode[0].lower() == "wal"


class TestModelSnapshotsTable:
    def test_creates_model_snapshots_table(self, db):
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = [t["name"] for t in tables]
        assert "model_snapshots" in names

    def test_creates_model_snapshots_indexes(self, db):
        indexes = db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        names = [i["name"] for i in indexes]
        assert "idx_model_snapshots_ts" in names
        assert "idx_model_snapshots_model_metric" in names


class TestColumnMigration:
    def test_adds_missing_columns_to_existing_db(self, tmp_path):
        from litellm_pulse.db import _migrate_scrapes_columns

        db_path = str(tmp_path / "old.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE scrapes ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, "
            "is_reset INTEGER DEFAULT 0, "
            "raw_requests REAL DEFAULT 0, "
            "delta_requests REAL DEFAULT 0)"
        )
        conn.commit()

        _migrate_scrapes_columns(conn)

        cols = {row["name"] for row in conn.execute("PRAGMA table_info(scrapes)").fetchall()}
        assert "raw_cache_hits" in cols
        assert "delta_cache_hits" in cols
        assert "raw_cached_tokens" in cols
        assert "delta_cached_tokens" in cols
        conn.close()

    def test_idempotent_on_new_db(self, db):
        from litellm_pulse.db import _migrate_scrapes_columns

        _migrate_scrapes_columns(db)

        cols = {row["name"] for row in db.execute("PRAGMA table_info(scrapes)").fetchall()}
        assert "raw_cache_hits" in cols
        assert "delta_cache_misses" in cols


class TestStoreAndGetLatest:
    def test_store_and_get_latest(self, db):
        ts = int(time.time())
        raw = _make_raw(requests=100.0, cost=5.0)
        deltas = _make_deltas(requests=10.0, cost=0.5)
        store_snapshot(db, ts, raw, deltas, is_reset=False)

        result = get_latest(db)
        assert result is not None
        assert result["requests"] == 100.0
        assert result["cost"] == 5.0

    def test_get_latest_returns_none_on_empty(self, db):
        assert get_latest(db) is None

    def test_get_latest_returns_most_recent(self, db):
        store_snapshot(db, 1000, _make_raw(requests=10), _make_deltas(), False)
        store_snapshot(db, 2000, _make_raw(requests=20), _make_deltas(), False)
        store_snapshot(db, 1500, _make_raw(requests=15), _make_deltas(), False)

        result = get_latest(db)
        assert result is not None
        assert result["requests"] == 20.0

    def test_get_latest_ts(self, db):
        store_snapshot(db, 12345, _make_raw(), _make_deltas(), False)
        assert get_latest_ts(db) == 12345

    def test_get_latest_ts_empty(self, db):
        assert get_latest_ts(db) is None

    def test_store_marks_is_reset(self, db):
        store_snapshot(db, 1000, _make_raw(), _make_deltas(), is_reset=True)
        row = db.execute("SELECT is_reset FROM scrapes WHERE ts = 1000").fetchone()
        assert row["is_reset"] == 1

    def test_store_marks_not_reset(self, db):
        store_snapshot(db, 1000, _make_raw(), _make_deltas(), is_reset=False)
        row = db.execute("SELECT is_reset FROM scrapes WHERE ts = 1000").fetchone()
        assert row["is_reset"] == 0


class TestWindowAggregate:
    def test_sum_deltas_in_window(self, db):
        store_snapshot(db, 1000, _make_raw(), _make_deltas(requests=10, cost=1), False)
        store_snapshot(db, 2000, _make_raw(), _make_deltas(requests=20, cost=2), False)
        store_snapshot(db, 3000, _make_raw(), _make_deltas(requests=30, cost=3), False)

        result = get_window_aggregate(db, start_ts=1000)
        assert result["requests"] == 60.0
        assert result["cost"] == 6.0

    def test_excludes_before_window(self, db):
        store_snapshot(db, 500, _make_raw(), _make_deltas(requests=100), False)
        store_snapshot(db, 1000, _make_raw(), _make_deltas(requests=10), False)
        store_snapshot(db, 2000, _make_raw(), _make_deltas(requests=20), False)

        result = get_window_aggregate(db, start_ts=1000)
        assert result["requests"] == 30.0

    def test_first_scrape_baseline_does_not_inflate(self, db):
        store_snapshot(db, 1000, _make_raw(requests=1000, cost=50), _make_deltas(), False)
        store_snapshot(
            db,
            1060,
            _make_raw(requests=1010, cost=50.5),
            _make_deltas(requests=10, cost=0.5),
            False,
        )
        store_snapshot(
            db,
            1120,
            _make_raw(requests=1025, cost=51.2),
            _make_deltas(requests=15, cost=0.7),
            False,
        )

        result = get_window_aggregate(db, start_ts=1000)
        assert result["requests"] == 25.0
        assert result["cost"] == 1.2

    def test_empty_db_returns_zeros(self, db):
        result = get_window_aggregate(db, start_ts=1000)
        for key in METRIC_KEYS:
            assert result[key] == 0.0


class TestGetHistory:
    def test_returns_chronological_order(self, db):
        store_snapshot(db, 3000, _make_raw(requests=30), _make_deltas(), False)
        store_snapshot(db, 1000, _make_raw(requests=10), _make_deltas(), False)
        store_snapshot(db, 2000, _make_raw(requests=20), _make_deltas(), False)

        history = get_history(db, limit=10)
        assert len(history) == 3
        assert history[0]["timestamp"] < history[1]["timestamp"] < history[2]["timestamp"]

    def test_respects_limit(self, db):
        for i in range(10):
            store_snapshot(db, 1000 + i, _make_raw(requests=i), _make_deltas(), False)

        history = get_history(db, limit=5)
        assert len(history) == 5
        # Should return the 5 most recent
        assert history[-1]["requests"] == 9.0

    def test_includes_reset_flag(self, db):
        store_snapshot(db, 1000, _make_raw(), _make_deltas(), is_reset=True)
        store_snapshot(db, 2000, _make_raw(), _make_deltas(), is_reset=False)

        history = get_history(db, limit=10)
        assert history[0]["is_reset"] is True
        assert history[1]["is_reset"] is False

    def test_includes_raw_and_delta_values(self, db):
        store_snapshot(
            db,
            1000,
            _make_raw(requests=100, cost=5.0),
            _make_deltas(requests=10, cost=0.5),
            False,
        )
        history = get_history(db, limit=1)
        entry = history[0]
        assert entry["requests"] == 100.0
        assert entry["requests_delta"] == 10.0
        assert entry["cost"] == 5.0
        assert entry["cost_delta"] == 0.5


class TestPurgeOld:
    def test_deletes_old_entries(self, db):
        # Use SQLite's strftime to ensure consistent time handling
        db.execute("DELETE FROM scrapes")
        db.execute(
            "INSERT INTO scrapes (ts, is_reset) VALUES "
            "(strftime('%s', 'now', '-10 days'), 0), "
            "(strftime('%s', 'now', '-1 day'), 0)"
        )
        db.commit()

        deleted = purge_old(db, retention_days=7)
        assert deleted == 1

        remaining = db.execute("SELECT COUNT(*) as c FROM scrapes").fetchone()
        assert remaining["c"] == 1

    def test_keeps_recent_entries(self, db):
        db.execute("DELETE FROM scrapes")
        db.execute(
            "INSERT INTO scrapes (ts, is_reset) VALUES "
            "(strftime('%s', 'now', '-1 day'), 0), "
            "(strftime('%s', 'now', '-1 hour'), 0)"
        )
        db.commit()

        deleted = purge_old(db, retention_days=7)
        assert deleted == 0

    def test_empty_db(self, db):
        deleted = purge_old(db, retention_days=30)
        assert deleted == 0


class TestGetHistoryTimezone:
    def test_default_tz_is_utc(self, db):
        ts = int(datetime(2025, 6, 21, 12, 0, 0, tzinfo=UTC).timestamp())
        store_snapshot(db, ts, _make_raw(requests=1), _make_deltas(), False)

        history = get_history(db, limit=1)
        assert history[0]["timestamp"].endswith("+00:00")

    def test_custom_tz_converts_timestamp(self, db):
        ts = int(datetime(2025, 6, 21, 12, 0, 0, tzinfo=UTC).timestamp())
        store_snapshot(db, ts, _make_raw(requests=1), _make_deltas(), False)

        ny = ZoneInfo("America/New_York")
        history = get_history(db, limit=1, tz=ny)
        # UTC 12:00 in June (EDT, UTC-4) is 08:00 local
        assert "08:00:00" in history[0]["timestamp"]
        assert "-04:00" in history[0]["timestamp"]


class TestModelSnapshots:
    def test_store_and_get_latest(self, db):
        raw = {"requests": {"gpt-4o": 100.0, "claude": 50.0}, "cost": {"gpt-4o": 1.5}}
        deltas = {"requests": {"gpt-4o": 10.0, "claude": 5.0}, "cost": {"gpt-4o": 0.2}}
        store_model_snapshots(db, 1000, raw, deltas, is_reset=False)

        result = get_latest_model_metrics(db)
        assert result["requests"]["gpt-4o"] == 100.0
        assert result["requests"]["claude"] == 50.0
        assert result["cost"]["gpt-4o"] == 1.5

    def test_get_latest_empty_db(self, db):
        assert get_latest_model_metrics(db) == {}

    def test_get_latest_returns_most_recent(self, db):
        store_model_snapshots(
            db, 1000, {"requests": {"gpt-4o": 10}}, {"requests": {"gpt-4o": 1}}, False
        )
        store_model_snapshots(
            db, 2000, {"requests": {"gpt-4o": 20}}, {"requests": {"gpt-4o": 5}}, False
        )

        result = get_latest_model_metrics(db)
        assert result["requests"]["gpt-4o"] == 20.0

    def test_window_aggregate(self, db):
        raw1 = {"requests": {"gpt-4o": 100}, "cost": {"gpt-4o": 1.0}}
        deltas1 = {"requests": {"gpt-4o": 10}, "cost": {"gpt-4o": 0.5}}
        raw2 = {"requests": {"gpt-4o": 120, "claude": 50}, "cost": {"gpt-4o": 1.5, "claude": 0.3}}
        deltas2 = {"requests": {"gpt-4o": 20, "claude": 50}, "cost": {"gpt-4o": 0.5, "claude": 0.3}}

        store_model_snapshots(db, 1000, raw1, deltas1, False)
        store_model_snapshots(db, 2000, raw2, deltas2, False)

        result = get_model_window_aggregate(db, start_ts=1000)
        assert result["gpt-4o"]["requests"] == 30.0
        assert result["gpt-4o"]["cost"] == 1.0
        assert result["claude"]["requests"] == 50.0
        assert result["claude"]["cost"] == 0.3

    def test_window_aggregate_excludes_before(self, db):
        store_model_snapshots(
            db, 500, {"requests": {"gpt-4o": 10}}, {"requests": {"gpt-4o": 100}}, False
        )
        store_model_snapshots(
            db, 2000, {"requests": {"gpt-4o": 20}}, {"requests": {"gpt-4o": 10}}, False
        )

        result = get_model_window_aggregate(db, start_ts=1000)
        assert result["gpt-4o"]["requests"] == 10.0

    def test_window_aggregate_empty_db(self, db):
        result = get_model_window_aggregate(db, start_ts=1000)
        assert result == {}

    def test_purge_deletes_model_snapshots(self, db):
        store_model_snapshots(
            db,
            int(__import__("time").time()) - 86400 * 10,
            {"requests": {"gpt-4o": 10}},
            {"requests": {"gpt-4o": 1}},
            False,
        )
        store_model_snapshots(
            db,
            int(__import__("time").time()) - 3600,
            {"requests": {"gpt-4o": 20}},
            {"requests": {"gpt-4o": 2}},
            False,
        )

        deleted = purge_old(db, retention_days=7)
        assert deleted >= 1
        result = get_latest_model_metrics(db)
        assert result["requests"]["gpt-4o"] == 20.0

    def test_store_empty_noop(self, db):
        store_model_snapshots(db, 1000, {}, {}, False)
        assert get_latest_model_metrics(db) == {}
