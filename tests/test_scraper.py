"""Tests for the scraper auth mechanism, model tracking, and coverage gaps."""

import asyncio
import time
from collections import deque
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from litellm_pulse.config import Settings
from litellm_pulse.scraper import Scraper

SAMPLE_METRICS = """\
# HELP litellm_proxy_total_requests_metric_total Total requests to proxy
# TYPE litellm_proxy_total_requests_metric_total counter
litellm_proxy_total_requests_metric_total 100.0
# HELP litellm_proxy_failed_requests_metric_total Failed requests
# TYPE litellm_proxy_failed_requests_metric_total counter
litellm_proxy_failed_requests_metric_total 5.0
# HELP litellm_total_tokens_metric_total Total tokens
# TYPE litellm_total_tokens_metric_total counter
litellm_total_tokens_metric_total{model="gpt-4o"} 40000.0
litellm_total_tokens_metric_total{model="claude-sonnet"} 10000.0
# HELP litellm_input_tokens_metric_total Input tokens
# TYPE litellm_input_tokens_metric_total counter
litellm_input_tokens_metric_total 30000.0
# HELP litellm_output_tokens_metric_total Output tokens
# TYPE litellm_output_tokens_metric_total counter
litellm_output_tokens_metric_total 20000.0
# HELP litellm_output_reasoning_tokens_metric_total Reasoning tokens
# TYPE litellm_output_reasoning_tokens_metric_total counter
litellm_output_reasoning_tokens_metric_total 0.0
# HELP litellm_spend_metric_total Spend
# TYPE litellm_spend_metric_total counter
litellm_spend_metric_total{model="gpt-4o"} 2.0
litellm_spend_metric_total{model="claude-sonnet"} 0.5
# HELP litellm_in_flight_requests In-flight requests
# TYPE litellm_in_flight_requests gauge
litellm_in_flight_requests 3.0
# HELP litellm_cache_hits_metric_total Cache hits
# TYPE litellm_cache_hits_metric_total counter
litellm_cache_hits_metric_total 40.0
# HELP litellm_cache_misses_metric_total Cache misses
# TYPE litellm_cache_misses_metric_total counter
litellm_cache_misses_metric_total 60.0
# HELP litellm_cached_tokens_metric_total Cached tokens
# TYPE litellm_cached_tokens_metric_total counter
litellm_cached_tokens_metric_total 15000.0
# HELP litellm_input_cached_tokens_metric_total Input cached tokens
# TYPE litellm_input_cached_tokens_metric_total counter
litellm_input_cached_tokens_metric_total 8000.0
# HELP litellm_input_cache_creation_tokens_metric_total Input cache creation tokens
# TYPE litellm_input_cache_creation_tokens_metric_total counter
litellm_input_cache_creation_tokens_metric_total 2000.0
# HELP litellm_deployment_total_requests_total Per-model deployment requests
# TYPE litellm_deployment_total_requests_total counter
litellm_deployment_total_requests_total{model="gpt-4o"} 80.0
litellm_deployment_total_requests_total{model="claude-sonnet"} 20.0
"""

TEST_URL = "http://test-metrics:4000/metrics/"


@pytest.fixture
def scraper():
    settings = Settings(
        metrics_url=TEST_URL,
        scrape_interval=3600,
    )
    return Scraper(settings)


class TestBuildAuthHeaders:
    def test_with_key_set(self):
        settings = Settings(metrics_api_key="sk-test-key")
        s = Scraper(settings)
        headers = s.build_auth_headers()
        assert headers == {"Authorization": "Bearer sk-test-key"}

    def test_with_empty_key(self):
        settings = Settings(metrics_api_key="")
        s = Scraper(settings)
        headers = s.build_auth_headers()
        assert headers is None

    def test_with_whitespace_only_key(self):
        settings = Settings(metrics_api_key="   ")
        s = Scraper(settings)
        headers = s.build_auth_headers()
        assert headers is None


class TestScraperAuthIntegration:
    async def _wait_for_request(self, httpx_mock, timeout=5.0):
        deadline = time.monotonic() + timeout
        while len(httpx_mock.get_requests()) < 1:
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_scraper_loop_sends_auth_header(self, httpx_mock):
        settings = Settings(
            metrics_url=TEST_URL,
            scrape_interval=3600,
            metrics_api_key="sk-test-key",
        )
        s = Scraper(settings)

        httpx_mock.add_response(url=TEST_URL, text=SAMPLE_METRICS)

        task = asyncio.create_task(s.scraper_loop())
        await self._wait_for_request(httpx_mock)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        requests = httpx_mock.get_requests()
        assert len(requests) >= 1
        assert requests[0].headers["Authorization"] == "Bearer sk-test-key"

    @pytest.mark.asyncio
    async def test_scraper_loop_no_auth_by_default(self, httpx_mock):
        settings = Settings(
            metrics_url=TEST_URL,
            scrape_interval=3600,
        )
        s = Scraper(settings)

        httpx_mock.add_response(url=TEST_URL, text=SAMPLE_METRICS)

        task = asyncio.create_task(s.scraper_loop())
        await self._wait_for_request(httpx_mock)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        requests = httpx_mock.get_requests()
        assert len(requests) >= 1
        assert "Authorization" not in requests[0].headers

    @pytest.mark.asyncio
    async def test_scrape_handles_401(self, httpx_mock):
        settings = Settings(
            metrics_url=TEST_URL,
            scrape_interval=3600,
        )
        s = Scraper(settings)

        httpx_mock.add_response(
            url=TEST_URL,
            status_code=401,
            text="Unauthorized",
        )

        task = asyncio.create_task(s.scraper_loop())
        await self._wait_for_request(httpx_mock)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        assert s.last_error is not None
        assert "401" in s.last_error


class TestScraperModelTracking:
    async def _wait_for_request(self, httpx_mock, timeout=5.0):
        deadline = time.monotonic() + timeout
        while len(httpx_mock.get_requests()) < 1:
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_model_metrics_parsed(self, httpx_mock):
        settings = Settings(
            metrics_url=TEST_URL,
            scrape_interval=3600,
        )
        s = Scraper(settings)

        httpx_mock.add_response(url=TEST_URL, text=SAMPLE_METRICS)

        task = asyncio.create_task(s.scraper_loop())
        await self._wait_for_request(httpx_mock)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        model_metrics = s.raw_model_metrics
        assert "tokens" in model_metrics
        assert model_metrics["tokens"]["gpt-4o"] == 40000.0
        assert model_metrics["tokens"]["claude-sonnet"] == 10000.0
        assert model_metrics["cost"]["gpt-4o"] == 2.0
        assert model_metrics["cost"]["claude-sonnet"] == 0.5

    @pytest.mark.asyncio
    async def test_deployment_metrics_mapped(self, httpx_mock):
        settings = Settings(
            metrics_url=TEST_URL,
            scrape_interval=3600,
        )
        s = Scraper(settings)

        httpx_mock.add_response(url=TEST_URL, text=SAMPLE_METRICS)

        task = asyncio.create_task(s.scraper_loop())
        await self._wait_for_request(httpx_mock)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        model_metrics = s.raw_model_metrics
        assert "deployment_requests" in model_metrics
        assert model_metrics["deployment_requests"]["gpt-4o"] == 80.0
        assert model_metrics["deployment_requests"]["claude-sonnet"] == 20.0

    @pytest.mark.asyncio
    async def test_model_metrics_no_labels(self, httpx_mock):
        settings = Settings(
            metrics_url=TEST_URL,
            scrape_interval=3600,
        )
        s = Scraper(settings)

        unlabeled = "litellm_proxy_total_requests_metric_total 100\n"
        httpx_mock.add_response(url=TEST_URL, text=unlabeled)

        task = asyncio.create_task(s.scraper_loop())
        await self._wait_for_request(httpx_mock)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        assert s.raw_model_metrics == {}

    @pytest.mark.asyncio
    async def test_model_deltas_computed(self, httpx_mock):
        settings = Settings(
            metrics_url=TEST_URL,
            scrape_interval=3600,
        )
        s = Scraper(settings)
        s.previous_raw_model_metrics = {
            "tokens": {"gpt-4o": 30000.0},
            "cost": {"gpt-4o": 1.5},
        }

        httpx_mock.add_response(url=TEST_URL, text=SAMPLE_METRICS)

        task = asyncio.create_task(s.scraper_loop())
        await self._wait_for_request(httpx_mock)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        prev_model = s.previous_raw_model_metrics
        assert prev_model["tokens"]["gpt-4o"] == 40000.0
        assert prev_model["cost"]["gpt-4o"] == 2.0


# ---------------------------------------------------------------------------
# Coverage: open_db / close_db
# ---------------------------------------------------------------------------


class TestScraperDbLifecycle:
    def test_open_db_creates_connection(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        settings = Settings(metrics_url=TEST_URL, db_path=db_path)
        s = Scraper(settings)
        s.open_db()
        assert s.db is not None
        s.close_db()
        assert s.db is None

    def test_open_db_recovers_previous_raw(self, tmp_path):
        from litellm_pulse.db import open_db, store_snapshot

        db_path = str(tmp_path / "test.db")
        conn = open_db(db_path)
        raw = {k: 0.0 for k in Settings.from_env().metric_map}
        raw["requests"] = 42.0
        raw["cost"] = 3.14
        store_snapshot(conn, 1000, raw, {k: 0.0 for k in raw}, False)
        conn.close()

        settings = Settings(metrics_url=TEST_URL, db_path=db_path)
        s = Scraper(settings)
        s.open_db()
        prom_key = settings.metric_map["requests"]
        assert s.previous_raw.get(prom_key) == 42.0
        s.close_db()

    def test_open_db_recovers_model_metrics(self, tmp_path):
        from litellm_pulse.db import open_db, store_model_snapshots

        db_path = str(tmp_path / "test.db")
        conn = open_db(db_path)
        store_model_snapshots(
            conn,
            1000,
            {"requests": {"gpt-4o": 80.0}},
            {"requests": {"gpt-4o": 10.0}},
            False,
        )
        conn.close()

        settings = Settings(metrics_url=TEST_URL, db_path=db_path)
        s = Scraper(settings)
        s.open_db()
        assert s.previous_raw_model_metrics["requests"]["gpt-4o"] == 80.0
        s.close_db()

    def test_open_db_empty_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        settings = Settings(metrics_url=TEST_URL, db_path=db_path)
        s = Scraper(settings)
        s.open_db()
        assert s.previous_raw == {}
        assert s.previous_raw_model_metrics == {}
        s.close_db()

    def test_open_db_failure_continues_without_persistence(self):
        settings = Settings(metrics_url=TEST_URL, db_path="/nonexistent/path/db.db")
        with patch("litellm_pulse.scraper.open_db", side_effect=OSError("disk full")):
            s = Scraper(settings)
            s.open_db()
        assert s.db is None
        assert s.previous_raw == {}

    def test_close_db_noop_when_none(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        s.close_db()
        assert s.db is None


# ---------------------------------------------------------------------------
# Coverage: scrape() method
# ---------------------------------------------------------------------------


class TestScrapeMethod:
    def _make_mock_client(self, text=SAMPLE_METRICS, status=200):
        mock_resp = MagicMock()
        mock_resp.text = text
        mock_resp.status_code = status
        mock_resp.raise_for_status = MagicMock()
        if status >= 400:
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=mock_resp
            )
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        return mock_client

    @pytest.mark.asyncio
    async def test_first_scrape_zeroes_deltas(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        assert s.previous_raw == {}
        await s.scrape(self._make_mock_client())
        assert s.last_scrape is not None
        assert s.last_error is None
        assert s.previous_raw != {}

    @pytest.mark.asyncio
    async def test_first_scrape_sets_model_deltas_to_zero(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        assert s.previous_raw_model_metrics == {}
        await s.scrape(self._make_mock_client())
        assert s.raw_model_metrics != {}
        assert s.previous_raw_model_metrics != {}

    @pytest.mark.asyncio
    async def test_scrape_stores_to_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        s = Scraper(Settings(metrics_url=TEST_URL, db_path=db_path))
        s.open_db()
        await s.scrape(self._make_mock_client())
        from litellm_pulse.db import get_latest

        latest = get_latest(s.db)
        assert latest is not None
        s.close_db()

    @pytest.mark.asyncio
    async def test_scrape_stores_model_snapshots_to_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        s = Scraper(Settings(metrics_url=TEST_URL, db_path=db_path))
        s.open_db()
        await s.scrape(self._make_mock_client())
        from litellm_pulse.db import get_latest_model_metrics

        latest = get_latest_model_metrics(s.db)
        assert latest != {}
        s.close_db()

    @pytest.mark.asyncio
    async def test_scrape_appends_to_history(self):
        s = Scraper(Settings(metrics_url=TEST_URL, history_size=10))
        assert s.history is not None
        await s.scrape(self._make_mock_client())
        assert len(s.history) == 1
        entry = s.history[0]
        assert "requests" in entry
        assert "requests_delta" in entry
        assert "ts" in entry
        assert "is_reset" in entry

    @pytest.mark.asyncio
    async def test_scrape_no_history_when_disabled(self):
        s = Scraper(Settings(metrics_url=TEST_URL, history_size=0))
        assert s.history is None
        await s.scrape(self._make_mock_client())
        assert s.history is None

    @pytest.mark.asyncio
    async def test_scrape_detects_counter_reset(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        prom_key = s.settings.metric_map["requests"]
        s.previous_raw = {prom_key: 10000.0}
        await s.scrape(self._make_mock_client())
        assert s.last_error is None

    @pytest.mark.asyncio
    async def test_scrape_handles_timeout(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        mock = AsyncMock()
        mock.get.side_effect = httpx.TimeoutException("timed out")
        await s.scrape(mock)
        assert s.last_error is not None
        assert "timed out" in s.last_error

    @pytest.mark.asyncio
    async def test_scrape_handles_http_status_error(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        mock = self._make_mock_client(status=503)
        await s.scrape(mock)
        assert s.last_error is not None
        assert "503" in s.last_error

    @pytest.mark.asyncio
    async def test_scrape_handles_request_error(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        mock = AsyncMock()
        mock.get.side_effect = httpx.RequestError("connection refused")
        await s.scrape(mock)
        assert s.last_error is not None
        assert "connection refused" in s.last_error

    @pytest.mark.asyncio
    async def test_scrape_handles_unexpected_error(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        mock = AsyncMock()
        mock.get.side_effect = RuntimeError("something broke")
        await s.scrape(mock)
        assert s.last_error is not None
        assert "something broke" in s.last_error

    @pytest.mark.asyncio
    async def test_scrape_detects_reset_with_model_metrics(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        prom_key = s.settings.metric_map["requests"]
        s.previous_raw = {prom_key: 10000.0}
        s.previous_raw_model_metrics = {
            "tokens": {"gpt-4o": 50000.0},
            "cost": {"gpt-4o": 10.0},
        }
        await s.scrape(self._make_mock_client())
        assert s.previous_raw_model_metrics != {}

    @pytest.mark.asyncio
    async def test_scrape_with_no_model_metrics(self):
        s = Scraper(Settings(metrics_url=TEST_URL, db_path="/tmp/test.db"))
        s.open_db()
        no_models = "litellm_proxy_total_requests_metric_total 100.0\n"
        await s.scrape(self._make_mock_client(text=no_models))
        s.close_db()

    @pytest.mark.asyncio
    async def test_scrape_non_200_triggers_last_error(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        mock = self._make_mock_client(status=500)
        await s.scrape(mock)
        assert s.last_error is not None
        assert "500" in s.last_error


# ---------------------------------------------------------------------------
# Coverage: summary() with DB
# ---------------------------------------------------------------------------


class TestSummaryWithDb:
    def test_summary_includes_db_aggregates(self, tmp_path):
        import time

        from litellm_pulse.db import open_db, store_snapshot

        db_path = str(tmp_path / "test.db")
        conn = open_db(db_path)
        ts = int(time.time())
        raw = {k: 0.0 for k in Settings.from_env().metric_map}
        raw["requests"] = 100.0
        raw["cost"] = 5.0
        deltas = {k: 0.0 for k in raw}
        deltas["requests"] = 10.0
        deltas["cost"] = 0.5
        store_snapshot(conn, ts, raw, deltas, False)
        conn.close()

        s = Scraper(Settings(metrics_url=TEST_URL, db_path=db_path))
        s.open_db()
        data = s.summary()
        assert data["requests_daily"] >= 0.0
        assert data["cost_daily"] >= 0.0
        assert data["requests_weekly"] >= 0.0
        assert data["requests_monthly"] >= 0.0
        assert "source" in data
        s.close_db()


# ---------------------------------------------------------------------------
# Coverage: metric() with suffix
# ---------------------------------------------------------------------------


class TestMetricWithSuffix:
    def test_metric_with_daily_suffix(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        result = s.metric("cost_daily")
        assert result is not None
        assert result["name"] == "cost_daily"
        assert result["value"] == 0.0

    def test_metric_with_weekly_suffix(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        s.previous_raw = {s.settings.metric_map["cost"]: 5.0}
        s.raw_metrics = {s.settings.metric_map["cost"]: 5.0}
        result = s.metric("cost_weekly")
        assert result is not None
        assert result["name"] == "cost_weekly"

    def test_metric_with_monthly_suffix(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        result = s.metric("cost_monthly")
        assert result is not None
        assert result["name"] == "cost_monthly"

    def test_metric_with_suffix_unknown_base(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        result = s.metric("nonexistent_daily")
        assert result is None

    def test_metric_with_no_last_scrape(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        s.last_scrape = None
        result = s.metric("cost")
        assert result is not None
        assert result["last_scrape"] is None

    def test_metric_with_suffix_no_last_scrape(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        s.last_scrape = None
        result = s.metric("cost_daily")
        assert result is not None
        assert result["last_scrape"] is None


# ---------------------------------------------------------------------------
# Coverage: history_snapshots() with DB
# ---------------------------------------------------------------------------


class TestHistorySnapshotsWithDb:
    def test_history_from_db(self, tmp_path):
        from litellm_pulse.db import open_db, store_snapshot

        db_path = str(tmp_path / "test.db")
        conn = open_db(db_path)
        raw = {k: 0.0 for k in Settings.from_env().metric_map}
        raw["requests"] = 42.0
        deltas = {k: 0.0 for k in raw}
        deltas["requests"] = 5.0
        store_snapshot(conn, 1000, raw, deltas, False)
        conn.close()

        s = Scraper(Settings(metrics_url=TEST_URL, db_path=db_path))
        s.open_db()
        result = s.history_snapshots(limit=10)
        assert result["source"] == "sqlite"
        assert result["count"] >= 1
        assert len(result["snapshots"]) >= 1
        s.close_db()

    def test_history_clamps_limit(self):
        s = Scraper(Settings(metrics_url=TEST_URL, history_size=10))
        s.history = deque(maxlen=10)
        result = s.history_snapshots(limit=99999)
        clamped_max = 10 * 12
        assert result["count"] <= clamped_max

    def test_history_from_memory_ring_buffer(self):
        s = Scraper(Settings(metrics_url=TEST_URL, history_size=10))
        s.history = deque(
            [{"ts": 1000, "is_reset": False, "requests": 10.0, "requests_delta": 2.0}],
            maxlen=10,
        )
        result = s.history_snapshots(limit=5)
        assert result["source"] == "memory"
        assert result["count"] == 1
        assert "ts" not in result["snapshots"][0]

    def test_history_disabled(self):
        s = Scraper(Settings(metrics_url=TEST_URL, history_size=0))
        s.history = None
        s.db = None
        result = s.history_snapshots(limit=10)
        assert result["source"] == "disabled"
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# Coverage: purge_loop()
# ---------------------------------------------------------------------------


class TestPurgeLoop:
    async def _fake_sleep(self, _):
        pass

    @pytest.mark.asyncio
    async def test_purge_loop_runs(self, tmp_path):
        from litellm_pulse.db import open_db, store_snapshot

        db_path = str(tmp_path / "test.db")
        conn = open_db(db_path)
        raw = {k: 0.0 for k in Settings.from_env().metric_map}
        deltas = {k: 0.0 for k in raw}
        store_snapshot(conn, 1000, raw, deltas, False)
        conn.close()

        s = Scraper(Settings(metrics_url=TEST_URL, db_path=db_path))
        s.open_db()

        with patch("litellm_pulse.scraper.asyncio.sleep", side_effect=self._fake_sleep):
            task = asyncio.create_task(s.purge_loop())
            await asyncio.sleep(0.01)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        s.close_db()

    @pytest.mark.asyncio
    async def test_purge_loop_skips_when_no_db(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        s.db = None

        with patch("litellm_pulse.scraper.asyncio.sleep", side_effect=self._fake_sleep):
            task = asyncio.create_task(s.purge_loop())
            await asyncio.sleep(0.01)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_purge_loop_handles_error(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        s = Scraper(Settings(metrics_url=TEST_URL, db_path=db_path))
        s.open_db()

        with (
            patch("litellm_pulse.scraper.purge_old", side_effect=RuntimeError("purge failed")),
            patch("litellm_pulse.scraper.asyncio.sleep", side_effect=self._fake_sleep),
        ):
            task = asyncio.create_task(s.purge_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        s.close_db()


# ---------------------------------------------------------------------------
# Coverage: model_summary() with DB
# ---------------------------------------------------------------------------


class TestModelSummaryWithDb:
    def test_model_summary_from_db(self, tmp_path):
        from litellm_pulse.db import open_db, store_model_snapshots

        db_path = str(tmp_path / "test.db")
        conn = open_db(db_path)
        store_model_snapshots(
            conn,
            1000,
            {"requests": {"gpt-4o": 80.0}, "cost": {"gpt-4o": 2.0}},
            {"requests": {"gpt-4o": 10.0}, "cost": {"gpt-4o": 0.5}},
            False,
        )
        conn.close()

        s = Scraper(Settings(metrics_url=TEST_URL, db_path=db_path))
        s.open_db()
        result = s.model_summary()
        assert len(result["models"]) >= 1
        model_names = [m["model"] for m in result["models"]]
        assert "gpt-4o" in model_names
        s.close_db()

    def test_model_summary_no_last_scrape(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        s.last_scrape = None
        result = s.model_summary()
        assert result["last_scrape"] is None


# ---------------------------------------------------------------------------
# Coverage: _map_model_metrics drops unknown metrics
# ---------------------------------------------------------------------------


class TestMapModelMetrics:
    def test_drops_unknown_prometheus_metrics(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        labeled = {
            "litellm_total_tokens_metric_total": {"gpt-4o": 100.0},
            "unknown_histogram_bucket": {"gpt-4o": 5.0},
            "unknown_histogram_count": {"gpt-4o": 10.0},
        }
        result = s._map_model_metrics(labeled)
        assert "tokens" in result
        assert "unknown_histogram_bucket" not in result
        assert "unknown_histogram_count" not in result


# ---------------------------------------------------------------------------
# Coverage: _compute_deltas / _compute_model_deltas on reset
# ---------------------------------------------------------------------------


class TestComputeDeltasOnReset:
    def test_deltas_on_reset_use_current_value(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        prev = {s.settings.metric_map["requests"]: 10000.0}
        curr = {s.settings.metric_map["requests"]: 100.0}
        deltas = s._compute_deltas(prev, curr, is_reset=True)
        assert deltas["requests"] == 100.0

    def test_model_deltas_on_reset_use_current_value(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        prev = {"requests": {"gpt-4o": 10000.0}}
        curr = {"requests": {"gpt-4o": 100.0}}
        deltas = s._compute_model_deltas(prev, curr, is_reset=True)
        assert deltas["requests"]["gpt-4o"] == 100.0


# ---------------------------------------------------------------------------
# Coverage: _format_ts
# ---------------------------------------------------------------------------


class TestFormatTimestamp:
    def test_format_ts(self):
        from datetime import UTC, datetime

        s = Scraper(Settings(metrics_url=TEST_URL))
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC).timestamp()
        result = s._format_ts(ts)
        assert "2025-01-01" in result

    def test_summary_includes_last_error(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        s.last_error = "test error"
        data = s.summary()
        assert data["error"] == "test error"


# ---------------------------------------------------------------------------
# Coverage: _detect_reset
# ---------------------------------------------------------------------------


class TestDetectReset:
    def test_detect_reset_when_value_drops_more_than_50_percent(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        prom = s.settings.metric_map["requests"]
        prev = {prom: 1000.0}
        curr = {prom: 400.0}
        assert s._detect_reset(prev, curr) is True

    def test_no_reset_when_value_increases(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        prom = s.settings.metric_map["requests"]
        prev = {prom: 100.0}
        curr = {prom: 200.0}
        assert s._detect_reset(prev, curr) is False

    def test_no_reset_when_previous_is_zero(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        prom = s.settings.metric_map["requests"]
        prev = {prom: 0.0}
        curr = {prom: 100.0}
        assert s._detect_reset(prev, curr) is False

    def test_no_reset_when_small_decrease(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        prom = s.settings.metric_map["requests"]
        prev = {prom: 100.0}
        curr = {prom: 90.0}
        assert s._detect_reset(prev, curr) is False


# ---------------------------------------------------------------------------
# Coverage: health(), available_metrics()
# ---------------------------------------------------------------------------


class TestHealthAndAvailable:
    def test_health_returns_starting_when_no_scrape(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        assert s.health() == {"status": "starting"}

    def test_health_returns_ok_after_scrape(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        from datetime import UTC, datetime

        s.last_scrape = datetime.now(UTC)
        assert s.health() == {"status": "ok"}

    def test_available_metrics(self):
        s = Scraper(Settings(metrics_url=TEST_URL))
        metrics = s.available_metrics()
        assert "requests" in metrics
        assert "cost" in metrics
        assert "tokens" in metrics
