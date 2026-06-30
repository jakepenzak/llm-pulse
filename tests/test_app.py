"""Tests for the FastAPI application endpoints and app compat module."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from litellm_pulse.api import app
from litellm_pulse.config import Settings
from litellm_pulse.scraper import Scraper

TEST_METRICS_URL = "http://test-metrics:4000/metrics/"
TEST_TZ = UTC


def _make_test_settings(**overrides) -> Settings:
    kwargs = {"metrics_url": TEST_METRICS_URL, "tz": TEST_TZ}
    kwargs.update(overrides)
    return Settings(**kwargs)


def _make_test_scraper(**overrides) -> Scraper:
    s = _make_test_settings(**overrides)
    scraper = Scraper(s)
    scraper.raw_metrics = {
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
    scraper.raw_model_metrics = {
        "requests": {"gpt-4o": 80.0, "claude-sonnet": 20.0},
        "tokens": {"gpt-4o": 40000.0, "claude-sonnet": 10000.0},
        "cost": {"gpt-4o": 2.0, "claude-sonnet": 0.5},
    }
    scraper.last_scrape = datetime.now(UTC)
    scraper.last_error = None
    scraper.db = None
    return scraper


@pytest.fixture
def scraper():
    return _make_test_scraper()


@pytest.fixture(autouse=True)
def set_app_scraper(scraper):
    app.state.scraper = scraper
    yield


@pytest.fixture
async def async_client():
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
    async def test_root_returns_html(self, async_client):
        response = await async_client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, async_client):
        response = await async_client.get("/api/v1/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["requests"] == 100.0
        assert data["failed_requests"] == 5.0
        assert data["cost"] == 2.50
        assert data["tokens"] == 50000.0
        assert data["source"] == TEST_METRICS_URL

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
    async def test_history_no_db_no_memory(self, async_client, scraper):
        scraper.history = None
        scraper.db = None
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
    async def test_error_included_when_set(self, async_client, scraper):
        scraper.last_error = "Connection refused"
        response = await async_client.get("/api/v1/metrics")
        data = response.json()
        assert data["error"] == "Connection refused"


class TestTimezoneFormatting:
    @pytest.mark.asyncio
    async def test_last_scrape_uses_configured_tz(self, async_client, scraper):
        response = await async_client.get("/api/v1/metrics")
        data = response.json()
        ts = data["last_scrape"]
        expected_offset = datetime.now(scraper.settings.tz).strftime("%z")
        expected = f"{expected_offset[:3]}:{expected_offset[3:]}" if expected_offset else "+00:00"
        assert ts.endswith(expected)

    @pytest.mark.asyncio
    async def test_last_scrape_converts_to_non_utc_tz(self, async_client):
        ny_scraper = _make_test_scraper(tz=ZoneInfo("America/New_York"), tz_name="America/New_York")
        app.state.scraper = ny_scraper
        response = await async_client.get("/api/v1/metrics")
        data = response.json()
        ts = data["last_scrape"]
        assert ts.endswith("-04:00") or ts.endswith("-05:00")

    @pytest.mark.asyncio
    async def test_get_metric_last_scrape_uses_tz(self, async_client):
        ny_scraper = _make_test_scraper(tz=ZoneInfo("America/New_York"), tz_name="America/New_York")
        app.state.scraper = ny_scraper
        response = await async_client.get("/api/v1/metrics/cost")
        data = response.json()
        ts = data["last_scrape"]
        assert ts.endswith("-04:00") or ts.endswith("-05:00")


class TestWindowBoundariesWithTimezone:
    def test_start_of_day_respects_tz(self, monkeypatch):
        from litellm_pulse import scraper as scraper_mod

        scraper = _make_test_scraper(tz=ZoneInfo("America/New_York"), tz_name="America/New_York")

        fixed_utc = datetime(2025, 6, 21, 4, 0, 0, tzinfo=UTC)

        class FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_utc.replace(tzinfo=None)
                return fixed_utc.astimezone(tz)

        monkeypatch.setattr(scraper_mod, "datetime", FakeDatetime)

        start = scraper._start_of_day()
        assert start == int(datetime(2025, 6, 21, 4, 0, 0, tzinfo=UTC).timestamp())

    def test_start_of_month_respects_tz(self, monkeypatch):
        from litellm_pulse import scraper as scraper_mod

        scraper = _make_test_scraper(tz=ZoneInfo("America/New_York"), tz_name="America/New_York")

        fixed_utc = datetime(2025, 7, 1, 2, 0, 0, tzinfo=UTC)

        class FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_utc.replace(tzinfo=None)
                return fixed_utc.astimezone(tz)

        monkeypatch.setattr(scraper_mod, "datetime", FakeDatetime)

        start = scraper._start_of_month()
        assert start == int(datetime(2025, 6, 1, 4, 0, 0, tzinfo=UTC).timestamp())


class TestHistoryTimezoneInMemory:
    @pytest.mark.asyncio
    async def test_in_memory_history_converts_timestamp(self, async_client):
        from collections import deque

        ny_scraper = _make_test_scraper(tz=ZoneInfo("America/New_York"), tz_name="America/New_York")
        ny_scraper.history = deque(
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
        ny_scraper.db = None
        app.state.scraper = ny_scraper

        response = await async_client.get("/api/v1/history")
        data = response.json()
        assert data["source"] == "memory"
        assert data["count"] == 1
        ts = data["snapshots"][0]["timestamp"]
        assert "08:00:00" in ts
        assert "-04:00" in ts
        assert "ts" not in data["snapshots"][0]


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
    async def test_empty_model_metrics(self, async_client, scraper):
        scraper.raw_model_metrics = {}
        response = await async_client.get("/api/v1/models")
        data = response.json()
        assert data["models"] == []
        assert data["last_scrape"] is not None

    @pytest.mark.asyncio
    async def test_models_with_db_aggregates(self, async_client, tmp_path, scraper):
        import time

        from litellm_pulse.db import open_db, store_model_snapshots

        db_path = str(tmp_path / "test_models_endpoint.db")
        conn = open_db(db_path)
        scraper.db = conn

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
            scraper.db = None


class TestCLI:
    def test_build_arg_parser_has_all_flags(self):
        from litellm_pulse.config import Settings, build_arg_parser

        env_settings = Settings.from_env()
        parser = build_arg_parser(env_settings)
        actions = {a.dest for a in parser._actions if a.dest != "help"}
        expected = {
            "metrics_url",
            "scrape_interval",
            "port",
            "host",
            "verify_ssl",
            "scrape_timeout",
            "log_level",
            "db_path",
            "db_retention_days",
            "history_size",
            "metrics_api_key",
            "timezone",
        }
        assert actions == expected

    def test_log_level_lowercase_accepted(self):
        from litellm_pulse.config import Settings, build_arg_parser

        settings = Settings()
        parser = build_arg_parser(settings)
        args = parser.parse_args(["--log-level", "info"])
        assert args.log_level == "INFO"

    def test_log_level_uppercase_accepted(self):
        from litellm_pulse.config import Settings, build_arg_parser

        settings = Settings()
        parser = build_arg_parser(settings)
        args = parser.parse_args(["--log-level", "INFO"])
        assert args.log_level == "INFO"

    def test_invalid_log_level_rejected(self):
        from litellm_pulse.config import Settings, build_arg_parser

        settings = Settings()
        parser = build_arg_parser(settings)
        with pytest.raises(SystemExit):
            parser.parse_args(["--log-level", "invalid"])

    def test_settings_from_env_respects_env(self, monkeypatch):
        monkeypatch.setenv("LITELLM_PULSE_PORT", "9999")
        from litellm_pulse.config import Settings

        s = Settings.from_env()
        assert s.port == 9999
        monkeypatch.delenv("LITELLM_PULSE_PORT", raising=False)

    def test_main_applies_cli_values(self, monkeypatch):
        monkeypatch.setenv("LITELLM_PULSE_PORT", "9999")
        from litellm_pulse.config import Settings, settings_from_args

        env_settings = Settings.from_env()
        from litellm_pulse.config import build_arg_parser

        parser = build_arg_parser(env_settings)
        args = parser.parse_args(["--port", "7777", "--log-level", "debug"])
        settings = settings_from_args(args)
        assert settings.port == 7777
        assert settings.log_level == "DEBUG"
        monkeypatch.delenv("LITELLM_PULSE_PORT", raising=False)


class TestAppModuleCompat:
    def test_app_module_imports_version_and_main(self):
        from litellm_pulse.app import __version__, main

        assert __version__ is not None
        assert callable(main)

    def test_app_module_exports(self):
        from litellm_pulse.app import __all__

        assert "__version__" in __all__
        assert "main" in __all__
