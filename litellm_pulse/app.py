"""LiteLLM Pulse — a lightweight LiteLLM metrics exporter with SQLite time-series storage."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .db import (
    METRIC_KEYS,
    get_history,
    get_latest,
    get_window_aggregate,
    open_db,
    purge_old,
    store_snapshot,
)
from .parser import parse_prometheus_text

logger = logging.getLogger("litellm-pulse")

# ---------------------------------------------------------------------------
# Configuration (all env-var driven, prefixed with LITELLM_PULSE_)
# ---------------------------------------------------------------------------

METRICS_URL = os.environ.get("LITELLM_PULSE_METRICS_URL", "http://litellm:4000/metrics/")
SCRAPE_INTERVAL = int(os.environ.get("LITELLM_PULSE_SCRAPE_INTERVAL", "60"))
PORT = int(os.environ.get("LITELLM_PULSE_PORT", "8000"))
HOST = os.environ.get("LITELLM_PULSE_HOST", "0.0.0.0")
VERIFY_SSL = os.environ.get("LITELLM_PULSE_VERIFY_SSL", "false").lower() == "true"
SCRAPE_TIMEOUT = float(os.environ.get("LITELLM_PULSE_SCRAPE_TIMEOUT", "30"))
LOG_LEVEL = os.environ.get("LITELLM_PULSE_LOG_LEVEL", "info").upper()
DB_PATH = os.environ.get("LITELLM_PULSE_DB_PATH", "./data/litellm_pulse.db")
DB_RETENTION_DAYS = int(os.environ.get("LITELLM_PULSE_DB_RETENTION_DAYS", "90"))
HISTORY_SIZE = int(os.environ.get("LITELLM_PULSE_HISTORY_SIZE", "168"))
METRICS_API_KEY = os.environ.get("LITELLM_PULSE_METRICS_API_KEY", "")

# Timezone for API output and window boundaries. DB always stores UTC.
_TZ: tzinfo = UTC
_tz_name = os.environ.get("LITELLM_PULSE_TIMEZONE", "UTC")
try:
    _TZ = ZoneInfo(_tz_name)
except ZoneInfoNotFoundError:
    logger.warning("Unknown timezone %r — falling back to UTC", _tz_name)
except Exception:
    logger.exception("Failed to load timezone %r — falling back to UTC", _tz_name)

# Default metric mappings — LiteLLM Prometheus metric names.
# Each can be overridden via env var LITELLM_PULSE_METRIC_<FRIENDLY_NAME>.
DEFAULT_METRIC_MAP = {
    "requests": "litellm_proxy_total_requests_metric_total",
    "failed_requests": "litellm_proxy_failed_requests_metric_total",
    "tokens": "litellm_total_tokens_metric_total",
    "input_tokens": "litellm_input_tokens_metric_total",
    "output_tokens": "litellm_output_tokens_metric_total",
    "reasoning_tokens": "litellm_output_reasoning_tokens_metric_total",
    "cost": "litellm_spend_metric_total",
    "in_flight_requests": "litellm_in_flight_requests",
}

METRIC_MAP: dict[str, str] = {}
for _friendly, _prom in DEFAULT_METRIC_MAP.items():
    METRIC_MAP[_friendly] = os.environ.get(f"LITELLM_PULSE_METRIC_{_friendly.upper()}", _prom)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_raw_metrics: dict[str, float] = {}
_previous_raw: dict[str, float] = {}
_last_scrape: datetime | None = None
_last_error: str | None = None
_history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE) if HISTORY_SIZE > 0 else None
_db: Any = None  # sqlite3.Connection


# ---------------------------------------------------------------------------
# Reset detection & delta computation
# ---------------------------------------------------------------------------


def _detect_reset(prev: dict[str, float], curr: dict[str, float]) -> bool:
    """Return True if any tracked counter appears to have reset (dropped >50%)."""
    if not prev:
        return False
    for key in METRIC_MAP:
        prom_name = METRIC_MAP[key]
        old_val = prev.get(prom_name)
        new_val = curr.get(prom_name)
        if old_val is not None and new_val is not None and old_val > 0 and new_val < old_val * 0.5:
            return True
    return False


def _compute_deltas(
    prev: dict[str, float], curr: dict[str, float], is_reset: bool
) -> dict[str, float]:
    """Compute per-metric deltas. On reset, delta is the current value (from 0)."""
    deltas: dict[str, float] = {}
    for friendly, prom_name in METRIC_MAP.items():
        curr_val = curr.get(prom_name, 0.0)
        if is_reset:
            deltas[friendly] = curr_val
        else:
            deltas[friendly] = curr_val - prev.get(prom_name, 0.0)
    return deltas


# ---------------------------------------------------------------------------
# Window boundaries
# ---------------------------------------------------------------------------


def _format_ts(ts: int | float) -> str:
    """Format a UTC Unix timestamp as an ISO 8601 string in the configured timezone."""
    return datetime.fromtimestamp(ts, tz=_TZ).isoformat()


def _start_of_day() -> int:
    now = datetime.now(_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _start_of_week() -> int:
    now = datetime.now(_TZ)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_of_day - timedelta(days=start_of_day.weekday())
    return int(start.timestamp())


def _start_of_month() -> int:
    now = datetime.now(_TZ)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


async def _scrape(client: httpx.AsyncClient) -> None:
    global _raw_metrics, _previous_raw, _last_scrape, _last_error

    try:
        resp = await client.get(METRICS_URL, timeout=SCRAPE_TIMEOUT)
        resp.raise_for_status()
        _raw_metrics = parse_prometheus_text(resp.text)
        now = datetime.now(UTC)
        _last_scrape = now
        _last_error = None

        is_reset = _detect_reset(_previous_raw, _raw_metrics)
        deltas = _compute_deltas(_previous_raw, _raw_metrics, is_reset)

        if is_reset:
            logger.warning("Counter reset detected — treating as fresh LiteLLM session")

        if _db is not None:
            ts = int(now.timestamp())
            raw_by_friendly = {
                friendly: _raw_metrics.get(prom_name, 0.0)
                for friendly, prom_name in METRIC_MAP.items()
            }
            store_snapshot(_db, ts, raw_by_friendly, deltas, is_reset)

        if _history is not None:
            entry: dict[str, Any] = {
                "ts": int(now.timestamp()),
                "is_reset": is_reset,
            }
            for friendly, prom_name in METRIC_MAP.items():
                val = _raw_metrics.get(prom_name, 0.0)
                entry[friendly] = val
                entry[f"{friendly}_delta"] = deltas.get(friendly, 0.0)
            _history.append(entry)

        _previous_raw = dict(_raw_metrics)

        logger.debug(
            "Scraped %s — %d metric families, reset=%s",
            METRICS_URL,
            len(_raw_metrics),
            is_reset,
        )

    except Exception as exc:
        _last_error = str(exc)
        logger.warning("Scrape failed: %s", exc)


def _build_auth_headers() -> dict[str, str] | None:
    if METRICS_API_KEY and METRICS_API_KEY.strip():
        return {"Authorization": f"Bearer {METRICS_API_KEY.strip()}"}
    return None


async def _scraper_loop() -> None:
    async with httpx.AsyncClient(verify=VERIFY_SSL, headers=_build_auth_headers()) as client:
        while True:
            await _scrape(client)
            await asyncio.sleep(SCRAPE_INTERVAL)


async def _purge_loop() -> None:
    while True:
        await asyncio.sleep(3600)  # Run hourly
        if _db is not None:
            try:
                purge_old(_db, DB_RETENTION_DAYS)
            except Exception as exc:
                logger.warning("Purge failed: %s", exc)


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _db, _previous_raw
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    try:
        _db = open_db(DB_PATH)
        latest = get_latest(_db)
        if latest:
            _previous_raw = {METRIC_MAP[k]: latest.get(k, 0.0) for k in METRIC_KEYS}
            logger.info("Recovered state from DB — %d metrics loaded", len(_previous_raw))
        else:
            logger.info("DB empty — starting fresh")
    except Exception as exc:
        logger.error("Failed to open DB: %s — continuing without persistence", exc)
        _db = None

    scrape_task = asyncio.create_task(_scraper_loop())
    purge_task = asyncio.create_task(_purge_loop())
    logger.info(
        "LiteLLM Pulse started — scraping %s every %ds, DB: %s, timezone: %s, auth: %s",
        METRICS_URL,
        SCRAPE_INTERVAL,
        DB_PATH if _db else "disabled",
        str(_TZ),
        "enabled" if METRICS_API_KEY and METRICS_API_KEY.strip() else "disabled",
    )
    yield
    scrape_task.cancel()
    purge_task.cancel()
    with suppress(asyncio.CancelledError):
        await scrape_task
    with suppress(asyncio.CancelledError):
        await purge_task
    if _db is not None:
        _db.close()


app = FastAPI(
    title="LiteLLM Pulse",
    description="A lightweight metrics exporter for LiteLLM with SQLite time-series storage.",
    version="0.0.0",
    lifespan=lifespan,
)


def _summary() -> dict:
    data: dict[str, float | None | str] = {}

    for friendly, prom_name in METRIC_MAP.items():
        data[friendly] = _raw_metrics.get(prom_name, 0.0)

    if _db is not None:
        daily = get_window_aggregate(_db, _start_of_day())
        weekly = get_window_aggregate(_db, _start_of_week())
        monthly = get_window_aggregate(_db, _start_of_month())
        for friendly in METRIC_MAP:
            data[f"{friendly}_daily"] = daily.get(friendly, 0.0)
            data[f"{friendly}_weekly"] = weekly.get(friendly, 0.0)
            data[f"{friendly}_monthly"] = monthly.get(friendly, 0.0)
    else:
        for friendly in METRIC_MAP:
            data[f"{friendly}_daily"] = 0.0
            data[f"{friendly}_weekly"] = 0.0
            data[f"{friendly}_monthly"] = 0.0

    data["last_scrape"] = _format_ts(_last_scrape.timestamp()) if _last_scrape else None
    data["source"] = METRICS_URL
    if _last_error:
        data["error"] = _last_error
    return data


@app.get("/")
async def root():
    return _summary()


@app.get("/api/v1/metrics")
async def all_metrics():
    return _summary()


@app.get("/api/v1/metrics/{name}")
async def get_metric(name: str):
    valid_names = set(METRIC_MAP.keys())
    valid_suffixes = {"daily", "weekly", "monthly"}
    parts = name.rsplit("_", 1)
    if len(parts) == 2 and parts[1] in valid_suffixes:
        base, suffix = parts
        if base in valid_names:
            return {
                "name": name,
                "value": _summary().get(name, 0.0),
                "last_scrape": _format_ts(_last_scrape.timestamp()) if _last_scrape else None,
            }
    if name in valid_names:
        prom_name = METRIC_MAP[name]
        return {
            "name": name,
            "value": _raw_metrics.get(prom_name, 0.0),
            "last_scrape": _format_ts(_last_scrape.timestamp()) if _last_scrape else None,
        }
    return JSONResponse(
        status_code=404,
        content={
            "error": f"Unknown metric: {name}",
            "available": list(METRIC_MAP.keys()),
        },
    )


@app.get("/api/v1/history")
async def history(limit: int = 168):
    if _db is not None:
        snapshots = get_history(_db, limit=limit, tz=_TZ)
        return {
            "snapshots": snapshots,
            "count": len(snapshots),
            "source": "sqlite",
        }
    if _history is not None:
        snapshots = []
        for entry in list(_history)[-limit:]:
            out = {k: v for k, v in entry.items() if k != "ts"}
            out["timestamp"] = _format_ts(entry["ts"])
            snapshots.append(out)
        return {
            "snapshots": snapshots,
            "count": len(snapshots),
            "source": "memory",
        }
    return {"snapshots": [], "count": 0, "source": "disabled"}


@app.get("/raw")
async def raw_metrics():
    return _raw_metrics


@app.get("/health")
async def health():
    return {"status": "ok" if _last_scrape else "starting"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    uvicorn.run(app, host=HOST, port=PORT, log_level=LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
