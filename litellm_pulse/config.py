"""Configuration for LiteLLM Pulse — settings, constants, and CLI."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("litellm-pulse")

# ---------------------------------------------------------------------------
# Default metric mappings — LiteLLM Prometheus metric names.
# Each can be overridden via env var LITELLM_PULSE_METRIC_<FRIENDLY_NAME>.
# ---------------------------------------------------------------------------

DEFAULT_METRIC_MAP: dict[str, str] = {
    "requests": "litellm_proxy_total_requests_metric_total",
    "failed_requests": "litellm_proxy_failed_requests_metric_total",
    "tokens": "litellm_total_tokens_metric_total",
    "input_tokens": "litellm_input_tokens_metric_total",
    "output_tokens": "litellm_output_tokens_metric_total",
    "reasoning_tokens": "litellm_output_reasoning_tokens_metric_total",
    "cost": "litellm_spend_metric_total",
    "in_flight_requests": "litellm_in_flight_requests",
    "cache_hits": "litellm_cache_hits_metric_total",
    "cache_misses": "litellm_cache_misses_metric_total",
    "cached_tokens": "litellm_cached_tokens_metric_total",
    "input_cached_tokens": "litellm_input_cached_tokens_metric_total",
    "input_cache_creation_tokens": "litellm_input_cache_creation_tokens_metric_total",
}

# Per-model tracking: maps Prometheus metric names to friendly names for
# metrics that carry a ``model`` label. Includes everything from
# DEFAULT_METRIC_MAP plus deployment-specific metrics.
_MODEL_EXTRA_METRICS: dict[str, str] = {
    "litellm_deployment_total_requests_total": "deployment_requests",
    "litellm_deployment_success_responses_total": "deployment_success",
    "litellm_deployment_failure_responses_total": "deployment_failures",
}

# Gauge metrics — deltas and window aggregates are meaningless for these.
GAUGE_METRICS: frozenset[str] = frozenset({"in_flight_requests"})


def _build_metric_overrides() -> dict[str, str]:
    """Read LITELLM_PULSE_METRIC_* env vars as friendly→prom overrides."""
    overrides: dict[str, str] = {}
    for friendly in DEFAULT_METRIC_MAP:
        env_key = f"LITELLM_PULSE_METRIC_{friendly.upper()}"
        val = os.environ.get(env_key)
        if val:
            overrides[friendly] = val
    return overrides


def _resolve_timezone(tz_name: str) -> tuple[str, tzinfo]:
    """Resolve an IANA timezone name, falling back to UTC on invalid input."""
    try:
        return tz_name, ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r — falling back to UTC", tz_name)
        return "UTC", UTC
    except Exception:
        logger.exception("Failed to load timezone %r — falling back to UTC", tz_name)
        return "UTC", UTC


@dataclass(frozen=True)
class Settings:
    """All user-configurable settings for LiteLLM Pulse."""

    metrics_url: str = "http://litellm:4000/metrics/"
    scrape_interval: int = 60
    port: int = 8000
    host: str = "0.0.0.0"
    verify_ssl: bool = False
    scrape_timeout: float = 30.0
    log_level: str = "INFO"
    db_path: str = "./data/litellm_pulse.db"
    db_retention_days: int = 90
    history_size: int = 168
    metrics_api_key: str = ""

    tz_name: str = "UTC"
    tz: tzinfo = field(default_factory=lambda: UTC)

    metric_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def metric_map(self) -> dict[str, str]:
        """Effective friendly→prometheus mapping (defaults + overrides)."""
        return {**DEFAULT_METRIC_MAP, **self.metric_overrides}

    @property
    def prom_to_friendly(self) -> dict[str, str]:
        """Reverse mapping with per-model extras applied."""
        m = {v: k for k, v in self.metric_map.items()}
        m.update(_MODEL_EXTRA_METRICS)
        return m

    @property
    def metric_keys(self) -> tuple[str, ...]:
        """Tuple of friendly metric names — stable iteration order."""
        return tuple(self.metric_map.keys())

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from environment variables."""
        tz_name, tz = _resolve_timezone(os.environ.get("LITELLM_PULSE_TIMEZONE", "UTC"))
        return cls(
            metrics_url=os.environ.get("LITELLM_PULSE_METRICS_URL", cls.metrics_url),
            scrape_interval=int(os.environ.get("LITELLM_PULSE_SCRAPE_INTERVAL", "60")),
            port=int(os.environ.get("LITELLM_PULSE_PORT", "8000")),
            host=os.environ.get("LITELLM_PULSE_HOST", cls.host),
            verify_ssl=os.environ.get("LITELLM_PULSE_VERIFY_SSL", "false").lower() == "true",
            scrape_timeout=float(os.environ.get("LITELLM_PULSE_SCRAPE_TIMEOUT", "30")),
            log_level=os.environ.get("LITELLM_PULSE_LOG_LEVEL", "info").upper(),
            db_path=os.environ.get("LITELLM_PULSE_DB_PATH", cls.db_path),
            db_retention_days=int(os.environ.get("LITELLM_PULSE_DB_RETENTION_DAYS", "90")),
            history_size=int(os.environ.get("LITELLM_PULSE_HISTORY_SIZE", "168")),
            metrics_api_key=os.environ.get("LITELLM_PULSE_METRICS_API_KEY", ""),
            tz_name=tz_name,
            tz=tz,
            metric_overrides=_build_metric_overrides(),
        )


def build_arg_parser(settings: Settings) -> argparse.ArgumentParser:
    """Build the CLI argument parser with defaults from current Settings."""
    parser = argparse.ArgumentParser(
        prog="litellm-pulse",
        description="A lightweight metrics exporter for LiteLLM with SQLite time-series storage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every option also has an equivalent LITELLM_PULSE_* environment variable. "
        "CLI arguments take precedence over environment variables.",
    )
    parser.add_argument(
        "--metrics-url",
        default=settings.metrics_url,
        help="Prometheus metrics endpoint to scrape (env: LITELLM_PULSE_METRICS_URL)",
    )
    parser.add_argument(
        "--scrape-interval",
        type=int,
        default=settings.scrape_interval,
        help="Seconds between scrapes (env: LITELLM_PULSE_SCRAPE_INTERVAL)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.port,
        help="Port to serve the API on (env: LITELLM_PULSE_PORT)",
    )
    parser.add_argument(
        "--host",
        default=settings.host,
        help="Address to bind to (env: LITELLM_PULSE_HOST)",
    )
    parser.add_argument(
        "--verify-ssl",
        action=argparse.BooleanOptionalAction,
        default=settings.verify_ssl,
        help="Verify TLS certificates when scraping (env: LITELLM_PULSE_VERIFY_SSL)",
    )
    parser.add_argument(
        "--scrape-timeout",
        type=float,
        default=settings.scrape_timeout,
        help="Request timeout in seconds (env: LITELLM_PULSE_SCRAPE_TIMEOUT)",
    )
    parser.add_argument(
        "--log-level",
        default=settings.log_level,
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (env: LITELLM_PULSE_LOG_LEVEL)",
    )
    parser.add_argument(
        "--db-path",
        default=settings.db_path,
        help="Path to the SQLite database file (env: LITELLM_PULSE_DB_PATH)",
    )
    parser.add_argument(
        "--db-retention-days",
        type=int,
        default=settings.db_retention_days,
        help="Auto-purge data older than N days (env: LITELLM_PULSE_DB_RETENTION_DAYS)",
    )
    parser.add_argument(
        "--history-size",
        type=int,
        default=settings.history_size,
        help="Max snapshots in the in-memory ring buffer (env: LITELLM_PULSE_HISTORY_SIZE)",
    )
    parser.add_argument(
        "--metrics-api-key",
        default=settings.metrics_api_key,
        help="LiteLLM API key for authenticated /metrics endpoints "
        "(env: LITELLM_PULSE_METRICS_API_KEY)",
    )
    parser.add_argument(
        "--timezone",
        default=settings.tz_name,
        help="Timezone for API timestamps and day/week/month boundaries "
        "(env: LITELLM_PULSE_TIMEZONE)",
    )
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    """Build Settings from parsed CLI args (overriding env-derived defaults)."""
    env_settings = Settings.from_env()
    tz_name, tz = _resolve_timezone(args.timezone)
    overrides = dict(env_settings.metric_overrides)
    for friendly in DEFAULT_METRIC_MAP:
        arg_value = getattr(args, friendly, None)
        if arg_value is not None and arg_value != DEFAULT_METRIC_MAP[friendly]:
            overrides[friendly] = arg_value
    return Settings(
        metrics_url=args.metrics_url,
        scrape_interval=args.scrape_interval,
        port=args.port,
        host=args.host,
        verify_ssl=args.verify_ssl,
        scrape_timeout=args.scrape_timeout,
        log_level=args.log_level,
        db_path=args.db_path,
        db_retention_days=args.db_retention_days,
        history_size=args.history_size,
        metrics_api_key=args.metrics_api_key,
        tz_name=tz_name,
        tz=tz,
        metric_overrides=overrides,
    )


def main(argv: list[str] | None = None):
    """CLI entry point — parses args, constructs app, runs uvicorn."""
    import uvicorn

    from .api import build_app

    env_settings = Settings.from_env()
    parser = build_arg_parser(env_settings)
    args = parser.parse_args(argv)
    settings = settings_from_args(args)

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    app = build_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main(sys.argv[1:])
