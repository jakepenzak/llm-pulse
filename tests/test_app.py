"""Tests for the FastAPI application endpoints."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

import litellm_pulse.app as app_module
from litellm_pulse.app import app


@pytest.fixture
def client_setup():
    """Set up global state for testing without triggering lifespan."""
    original_raw = app_module._raw_metrics.copy()
    original_prev = app_module._previous_raw.copy()
    original_last_scrape = app_module._last_scrape
    original_last_error = app_module._last_error
    original_db = app_module._db
    original_history = app_module._history
    original_raw_model = app_module._raw_model_metrics.copy()
    original_prev_model = app_module._previous_raw_model_metrics.copy()

    app_module._raw_metrics = {
        "litellm_proxy_total_requests_metric_total": 100.0,
        "litellm_proxy_failed_requests_metric_total": 5.0,
        "litellm_total_tokens_metric_total": 50000.0,
        "litellm_input_tokens_metric_total": 30000.0,
        "litellm_output_tokens_metric_total": 20000.0,
        "litellm_output_reasoning_tokens_metric_total": 0.0,
        "litellm_spend_metric_total": 2.50,
        "litellm_in_flight_requests": 3.0,
        "litellm_cache_hits_metric_total": 40.0,
        "litellm_cache_misses_metric_total": 60.0,
        "litellm_cached_tokens_metric_total": 15000.0,
        "litellm_input_cached_tokens_metric_total": 8000.0,
        "litellm_input_cache_creation_tokens_metric_total": 2000.0,
    }
    app_module._raw_model_metrics = {
        "requests": {"gpt-4o": 80.0, "claude-sonnet": 20.0},
        "tokens": {"gpt-4o": 40000.0, "claude-sonnet": 10000.0},
        "cost": {"gpt-4o": 2.0, "claude-sonnet": 0.5},
    }
    app_module._last_scrape = datetime.now(UTC)
    app_module._last_error = None
    app_module._db = None  # Disable DB so we don't need a real one

    yield

    app_module._raw_metrics = original_raw
    app_module._previous_raw = original_prev
    app_module._last_scrape = original_last_scrape
    app_module._last_error = original_last_error
    app_module._db = original_db
    app_module._history = original_history
    app_module._raw_model_metrics = original_raw_model
    app_module._previous_raw_model_metrics = original_prev_model


@pytest.fixture
async def async_client(client_setup):
    """Create an async HTTP client that talks to the app directly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_ok(self, async_client):
        response = await async_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestRootAndMetrics:
    @pytest.mark.asyncio
    async def test_root_returns_summary(self, async_client):
        response = await async_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["requests"] == 100.0
        assert data["cost"] == 2.50
        assert data["tokens"] == 50000.0
        assert data["source"] == app_module.METRICS_URL

    @pytest.mark.asyncio
    async def test_metrics_endpoint_matches_root(self, async_client):
        response = await async_client.get("/api/v1/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["requests"] == 100.0
        assert data["failed_requests"] == 5.0

    @pytest.mark.asyncio
    async def test_includes_last_scrape(self, async_client):
        response = await async_client.get("/api/v1/metrics")
        data = response.json()
        assert data["last_scrape"] is not None

    @pytest.mark.asyncio
    async def test_db_disabled_returns_zero_aggregates(self, async_client):
        response = await async_client.get("/api/v1/metrics")
        data = response.json()
        assert data["requests_daily"] == 0.0
        assert data["cost_monthly"] == 0.0


class TestIndividualMetric:
    @pytest.mark.asyncio
    async def test_get_known_metric(self, async_client):
        response = await async_client.get("/api/v1/metrics/requests")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "requests"
        assert data["value"] == 100.0

    @pytest.mark.asyncio
    async def test_get_cost_metric(self, async_client):
        response = await async_client.get("/api/v1/metrics/cost")
        assert response.status_code == 200
        data = response.json()
        assert data["value"] == 2.50

    @pytest.mark.asyncio
    async def test_get_daily_aggregate_metric(self, async_client):
        response = await async_client.get("/api/v1/metrics/cost_daily")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "cost_daily"

    @pytest.mark.asyncio
    async def test_get_monthly_aggregate_metric(self, async_client):
        response = await async_client.get("/api/v1/metrics/tokens_monthly")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "tokens_monthly"

    @pytest.mark.asyncio
    async def test_unknown_metric_returns_404(self, async_client):
        response = await async_client.get("/api/v1/metrics/nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert "available" in data
        assert "requests" in data["available"]


class TestHistoryEndpoint:
    @pytest.mark.asyncio
    async def test_history_no_db_no_memory(self, async_client):
        app_module._history = None
        app_module._db = None
        response = await async_client.get("/api/v1/history")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["source"] == "disabled"


class TestRawEndpoint:
    @pytest.mark.asyncio
    async def test_raw_returns_parsed_metrics(self, async_client):
        response = await async_client.get("/raw")
        assert response.status_code == 200
        data = response.json()
        assert "litellm_proxy_total_requests_metric_total" in data
        assert data["litellm_spend_metric_total"] == 2.50


class TestErrorState:
    @pytest.mark.asyncio
    async def test_error_included_when_set(self, async_client):
        app_module._last_error = "Connection refused"
        response = await async_client.get("/api/v1/metrics")
        data = response.json()
        assert data["error"] == "Connection refused"


class TestTimezoneFormatting:
    @pytest.mark.asyncio
    async def test_last_scrape_uses_configured_tz(self, async_client):
        # _last_scrape is set to UTC in the fixture; verify the offset matches _TZ
        response = await async_client.get("/api/v1/metrics")
        data = response.json()
        ts = data["last_scrape"]
        # Default _TZ is UTC at module load unless LITELLM_PULSE_TIMEZONE is set
        expected_offset = datetime.now(app_module._TZ).strftime("%z")
        # Convert +0000 -> +00:00 format as isoformat produces
        expected = f"{expected_offset[:3]}:{expected_offset[3:]}" if expected_offset else "+00:00"
        assert ts.endswith(expected)

    @pytest.mark.asyncio
    async def test_last_scrape_converts_to_non_utc_tz(self, async_client):
        original_tz = app_module._TZ
        try:
            app_module._TZ = ZoneInfo("America/New_York")
            response = await async_client.get("/api/v1/metrics")
            data = response.json()
            ts = data["last_scrape"]
            # In June (EDT) offset is -04:00; in Jan (EST) it's -05:00
            assert ts.endswith("-04:00") or ts.endswith("-05:00")
        finally:
            app_module._TZ = original_tz

    @pytest.mark.asyncio
    async def test_get_metric_last_scrape_uses_tz(self, async_client):
        original_tz = app_module._TZ
        try:
            app_module._TZ = ZoneInfo("America/New_York")
            response = await async_client.get("/api/v1/metrics/cost")
            data = response.json()
            ts = data["last_scrape"]
            assert ts.endswith("-04:00") or ts.endswith("-05:00")
        finally:
            app_module._TZ = original_tz


class TestWindowBoundariesWithTimezone:
    def test_start_of_day_respects_tz(self, monkeypatch):
        from litellm_pulse import app as app_mod

        original_tz = app_mod._TZ
        try:
            # Use America/New_York. If it's 04:00 UTC, that's 00:00 EDT (previous day).
            # We mock datetime.now to a fixed value to make the test deterministic.
            app_mod._TZ = ZoneInfo("America/New_York")

            fixed_utc = datetime(2025, 6, 21, 4, 0, 0, tzinfo=UTC)

            class FakeDatetime:
                @classmethod
                def now(cls, tz=None):
                    if tz is None:
                        return fixed_utc.replace(tzinfo=None)
                    return fixed_utc.astimezone(tz)

            monkeypatch.setattr(app_mod, "datetime", FakeDatetime)

            start = app_mod._start_of_day()
            # 00:00 EDT on June 21 = 04:00 UTC on June 21
            assert start == int(datetime(2025, 6, 21, 4, 0, 0, tzinfo=UTC).timestamp())
        finally:
            app_mod._TZ = original_tz

    def test_start_of_month_respects_tz(self, monkeypatch):
        from litellm_pulse import app as app_mod

        original_tz = app_mod._TZ
        try:
            app_mod._TZ = ZoneInfo("America/New_York")

            # 02:00 UTC on July 1 = 22:00 EDT on June 30 -> "today" is still June 30 in NY
            fixed_utc = datetime(2025, 7, 1, 2, 0, 0, tzinfo=UTC)

            class FakeDatetime:
                @classmethod
                def now(cls, tz=None):
                    if tz is None:
                        return fixed_utc.replace(tzinfo=None)
                    return fixed_utc.astimezone(tz)

            monkeypatch.setattr(app_mod, "datetime", FakeDatetime)

            start = app_mod._start_of_month()
            # Start of June in NY = 00:00 EDT June 1 = 04:00 UTC June 1
            assert start == int(datetime(2025, 6, 1, 4, 0, 0, tzinfo=UTC).timestamp())
        finally:
            app_mod._TZ = original_tz


class TestHistoryTimezoneInMemory:
    @pytest.mark.asyncio
    async def test_in_memory_history_converts_timestamp(self, async_client):
        from collections import deque

        original_tz = app_module._TZ
        original_history = app_module._history
        try:
            app_module._TZ = ZoneInfo("America/New_York")
            app_module._history = deque(
                [
                    {
                        "ts": int(datetime(2025, 6, 21, 12, 0, 0, tzinfo=UTC).timestamp()),
                        "is_reset": False,
                        "requests": 100.0,
                        "requests_delta": 10.0,
                    }
                ],
                maxlen=168,
            )
            app_module._db = None

            response = await async_client.get("/api/v1/history")
            data = response.json()
            assert data["source"] == "memory"
            assert data["count"] == 1
            ts = data["snapshots"][0]["timestamp"]
            # UTC 12:00 in June (EDT) is 08:00 local
            assert "08:00:00" in ts
            assert "-04:00" in ts
            # ts field should not leak
            assert "ts" not in data["snapshots"][0]
        finally:
            app_module._TZ = original_tz
            app_module._history = original_history


class TestModelsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_models_from_memory(self, async_client):
        response = await async_client.get("/api/v1/models")
        assert response.status_code == 200
        data = response.json()
        model_names = [m["model"] for m in data["models"]]
        assert "gpt-4o" in model_names
        assert "claude-sonnet" in model_names

    @pytest.mark.asyncio
    async def test_includes_per_model_metrics(self, async_client):
        response = await async_client.get("/api/v1/models")
        data = response.json()
        gpt4o = next(m for m in data["models"] if m["model"] == "gpt-4o")
        assert gpt4o["requests"] == 80.0
        assert gpt4o["tokens"] == 40000.0
        assert gpt4o["cost"] == 2.0

    @pytest.mark.asyncio
    async def test_no_db_returns_zero_aggregates(self, async_client):
        response = await async_client.get("/api/v1/models")
        data = response.json()
        gpt4o = next(m for m in data["models"] if m["model"] == "gpt-4o")
        assert "requests_daily" not in gpt4o or gpt4o.get("requests_daily", 0.0) == 0.0

    @pytest.mark.asyncio
    async def test_empty_model_metrics(self, async_client):
        app_module._raw_model_metrics = {}
        response = await async_client.get("/api/v1/models")
        data = response.json()
        assert data["models"] == []
        assert data["last_scrape"] is not None

    @pytest.mark.asyncio
    async def test_models_with_db_aggregates(self, async_client, tmp_path):
        import time

        from litellm_pulse.db import (
            open_db,
            store_model_snapshots,
        )

        db_path = str(tmp_path / "test_models_endpoint.db")
        conn = open_db(db_path)
        app_module._db = conn

        ts = int(time.time())
        raw = {"requests": {"gpt-4o": 100.0}, "cost": {"gpt-4o": 5.0}}
        deltas = {"requests": {"gpt-4o": 10.0}, "cost": {"gpt-4o": 0.5}}
        store_model_snapshots(conn, ts, raw, deltas, is_reset=False)

        try:
            response = await async_client.get("/api/v1/models")
            data = response.json()
            gpt4o = next(m for m in data["models"] if m["model"] == "gpt-4o")
            assert gpt4o["requests"] == 100.0
            assert gpt4o["requests_daily"] == 10.0
            assert gpt4o["cost_daily"] == 0.5
        finally:
            conn.close()
            app_module._db = None
