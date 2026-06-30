"""Scraper — encapsulates state, scrape loop, and aggregation logic."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .config import GAUGE_METRICS, Settings
from .db import (
    get_latest,
    get_latest_model_metrics,
    get_model_window_aggregate,
    get_window_aggregate,
    open_db,
    purge_old,
    store_model_snapshots,
    store_snapshot,
)
from .parser import parse_prometheus_text, parse_prometheus_text_with_labels

logger = logging.getLogger("litellm-pulse")


class Scraper:
    """Encapsulates scrape state, scheduling, and aggregation.

    A single instance owns:
    - raw metrics state (current, previous)
    - per-model state
    - last scrape timestamp and last error
    - in-memory ring buffer of history snapshots
    - SQLite connection (optional)

    The class is designed to be instantiated once at startup and stored on
    ``app.state``. Tests can create independent instances without touching
    module-level globals.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db: sqlite3.Connection | None = None

        self.raw_metrics: dict[str, float] = {}
        self.previous_raw: dict[str, float] = {}
        self.raw_model_metrics: dict[str, dict[str, float]] = {}
        self.previous_raw_model_metrics: dict[str, dict[str, float]] = {}

        self.last_scrape: datetime | None = None
        self.last_error: str | None = None

        self.history: deque[dict[str, Any]] | None = (
            deque(maxlen=settings.history_size) if settings.history_size > 0 else None
        )

    # ------------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------------

    def _format_ts(self, ts: int | float) -> str:
        """Format a UTC Unix timestamp as ISO 8601 in the configured timezone."""
        return datetime.fromtimestamp(ts, tz=self.settings.tz).isoformat()

    def _start_of_day(self) -> int:
        now = datetime.now(self.settings.tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp())

    def _start_of_week(self) -> int:
        now = datetime.now(self.settings.tz)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = start_of_day - timedelta(days=start_of_day.weekday())
        return int(start.timestamp())

    def _start_of_month(self) -> int:
        now = datetime.now(self.settings.tz)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp())

    # ------------------------------------------------------------------
    # Reset detection & delta computation
    # ------------------------------------------------------------------

    def _detect_reset(self, prev: dict[str, float], curr: dict[str, float]) -> bool:
        """Return True if the primary requests counter appears to have reset (dropped >50%)."""
        prom_name = self.settings.metric_map["requests"]
        old_val = prev.get(prom_name, 0.0)
        new_val = curr.get(prom_name, 0.0)
        return old_val > 0 and new_val < old_val * 0.5

    def _compute_deltas(
        self, prev: dict[str, float], curr: dict[str, float], is_reset: bool
    ) -> dict[str, float]:
        """Compute per-metric deltas. On reset, delta is the current value (from 0)."""
        deltas: dict[str, float] = {}
        for friendly, prom_name in self.settings.metric_map.items():
            if friendly in GAUGE_METRICS:
                deltas[friendly] = 0.0
                continue
            curr_val = curr.get(prom_name, 0.0)
            if is_reset:
                deltas[friendly] = curr_val
            else:
                deltas[friendly] = curr_val - prev.get(prom_name, 0.0)
        return deltas

    def _map_model_metrics(
        self, labeled: dict[str, dict[str, float]]
    ) -> dict[str, dict[str, float]]:
        """Convert Prometheus metric names to friendly names in labeled data.

        Only metrics with a known mapping are kept; unknown Prometheus metrics
        (e.g. histogram sub-metrics like ``_bucket``/``_count``/``_sum``/``_created``)
        are dropped to avoid leaking raw metric fields into the model endpoint.
        """
        result: dict[str, dict[str, float]] = {}
        for prom_name, models in labeled.items():
            friendly = self.settings.prom_to_friendly.get(prom_name)
            if friendly is not None:
                result[friendly] = dict(models)
        return result

    def _compute_model_deltas(
        self,
        prev: dict[str, dict[str, float]],
        curr: dict[str, dict[str, float]],
        is_reset: bool,
    ) -> dict[str, dict[str, float]]:
        """Compute per-model deltas. On reset, each delta is the current value."""
        deltas: dict[str, dict[str, float]] = {}
        for metric, models in curr.items():
            deltas[metric] = {}
            prev_models = prev.get(metric, {})
            for model, value in models.items():
                if is_reset:
                    deltas[metric][model] = value
                else:
                    deltas[metric][model] = value - prev_models.get(model, 0.0)
        return deltas

    # ------------------------------------------------------------------
    # DB lifecycle
    # ------------------------------------------------------------------

    def open_db(self) -> None:
        """Open the SQLite database and recover previous state."""
        try:
            self.db = open_db(self.settings.db_path)
            latest = get_latest(self.db)
            if latest:
                self.previous_raw = {
                    self.settings.metric_map[k]: latest.get(k, 0.0)
                    for k in self.settings.metric_keys
                }
                logger.info("Recovered state from DB — %d metrics loaded", len(self.previous_raw))
            else:
                logger.info("DB empty — starting fresh")

            latest_model = get_latest_model_metrics(self.db)
            if latest_model:
                self.previous_raw_model_metrics = latest_model
                logger.info(
                    "Recovered per-model state from DB — %d metrics, %d models",
                    len(latest_model),
                    len({m for models in latest_model.values() for m in models}),
                )
        except Exception as exc:
            logger.error("Failed to open DB: %s — continuing without persistence", exc)
            self.db = None

    def close_db(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    # ------------------------------------------------------------------
    # Scrape loop
    # ------------------------------------------------------------------

    def build_auth_headers(self) -> dict[str, str] | None:
        if self.settings.metrics_api_key and self.settings.metrics_api_key.strip():
            return {"Authorization": f"Bearer {self.settings.metrics_api_key.strip()}"}
        return None

    async def scrape(self, client: httpx.AsyncClient) -> None:
        """Perform a single scrape, updating state and DB."""
        try:
            resp = await client.get(self.settings.metrics_url, timeout=self.settings.scrape_timeout)
            resp.raise_for_status()
            self.raw_metrics = parse_prometheus_text(resp.text)

            labeled = parse_prometheus_text_with_labels(resp.text)
            self.raw_model_metrics = self._map_model_metrics(labeled)

            now = datetime.now(UTC)
            self.last_scrape = now
            self.last_error = None

            is_reset = self._detect_reset(self.previous_raw, self.raw_metrics)
            deltas = self._compute_deltas(self.previous_raw, self.raw_metrics, is_reset)
            model_deltas = self._compute_model_deltas(
                self.previous_raw_model_metrics, self.raw_model_metrics, is_reset
            )

            if not self.previous_raw:
                deltas = {k: 0.0 for k in self.settings.metric_map}

            if not self.previous_raw_model_metrics:
                model_deltas = {
                    metric: {m: 0.0 for m in models}
                    for metric, models in self.raw_model_metrics.items()
                }

            if is_reset:
                logger.warning("Counter reset detected — treating as fresh LiteLLM session")

            if self.db is not None:
                ts = int(now.timestamp())
                raw_by_friendly = {
                    friendly: self.raw_metrics.get(prom_name, 0.0)
                    for friendly, prom_name in self.settings.metric_map.items()
                }
                store_snapshot(self.db, ts, raw_by_friendly, deltas, is_reset)
                if self.raw_model_metrics:
                    store_model_snapshots(
                        self.db, ts, self.raw_model_metrics, model_deltas, is_reset
                    )

            if self.history is not None:
                entry: dict[str, Any] = {
                    "ts": int(now.timestamp()),
                    "is_reset": is_reset,
                }
                for friendly, prom_name in self.settings.metric_map.items():
                    val = self.raw_metrics.get(prom_name, 0.0)
                    entry[friendly] = val
                    entry[f"{friendly}_delta"] = deltas.get(friendly, 0.0)
                self.history.append(entry)

            self.previous_raw = dict(self.raw_metrics)
            self.previous_raw_model_metrics = {
                metric: dict(models) for metric, models in self.raw_model_metrics.items()
            }

            logger.debug(
                "Scraped %s — %d metric families, %d per-model metrics, reset=%s",
                self.settings.metrics_url,
                len(self.raw_metrics),
                len(self.raw_model_metrics),
                is_reset,
            )

        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            self.last_error = f"Scrape timed out after {self.settings.scrape_timeout}s"
            logger.warning("Scrape timed out after %ss", self.settings.scrape_timeout)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            self.last_error = f"HTTP {status}"
            logger.warning("Scrape returned HTTP %s from %s", status, self.settings.metrics_url)
        except httpx.RequestError as exc:
            self.last_error = str(exc)
            logger.warning("Scrape failed: %s", exc)
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Unexpected scrape error: %s", exc)

    async def scraper_loop(self) -> None:
        """Run scrape() repeatedly on the configured interval."""
        async with httpx.AsyncClient(
            verify=self.settings.verify_ssl, headers=self.build_auth_headers()
        ) as client:
            while True:
                await self.scrape(client)
                await asyncio.sleep(self.settings.scrape_interval)

    async def purge_loop(self) -> None:
        """Run purge_old() hourly against the configured retention."""
        while True:
            await asyncio.sleep(3600)
            if self.db is not None:
                try:
                    purge_old(self.db, self.settings.db_retention_days)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Purge failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API data shapes (used by the FastAPI layer)
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Build the flat metrics summary (cumulative + daily/weekly/monthly)."""
        data: dict[str, Any] = {}

        for friendly, prom_name in self.settings.metric_map.items():
            data[friendly] = self.raw_metrics.get(prom_name, 0.0)

        if self.db is not None:
            daily = get_window_aggregate(self.db, self._start_of_day())
            weekly = get_window_aggregate(self.db, self._start_of_week())
            monthly = get_window_aggregate(self.db, self._start_of_month())
            for friendly in self.settings.metric_map:
                data[f"{friendly}_daily"] = daily.get(friendly, 0.0)
                data[f"{friendly}_weekly"] = weekly.get(friendly, 0.0)
                data[f"{friendly}_monthly"] = monthly.get(friendly, 0.0)
        else:
            for friendly in self.settings.metric_map:
                data[f"{friendly}_daily"] = 0.0
                data[f"{friendly}_weekly"] = 0.0
                data[f"{friendly}_monthly"] = 0.0

        data["last_scrape"] = (
            self._format_ts(self.last_scrape.timestamp()) if self.last_scrape else None
        )
        data["source"] = self.settings.metrics_url
        if self.last_error:
            data["error"] = self.last_error
        return data

    def metric(self, name: str) -> dict[str, Any] | None:
        """Return a single metric payload, or None if the name is unknown."""
        valid_suffixes = {"daily", "weekly", "monthly"}
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and parts[1] in valid_suffixes:
            base, suffix = parts
            if base in self.settings.metric_map:
                return {
                    "name": name,
                    "value": self.summary().get(name, 0.0),
                    "last_scrape": (
                        self._format_ts(self.last_scrape.timestamp()) if self.last_scrape else None
                    ),
                }
        if name in self.settings.metric_map:
            prom_name = self.settings.metric_map[name]
            return {
                "name": name,
                "value": self.raw_metrics.get(prom_name, 0.0),
                "last_scrape": (
                    self._format_ts(self.last_scrape.timestamp()) if self.last_scrape else None
                ),
            }
        return None

    def available_metrics(self) -> list[str]:
        return list(self.settings.metric_map.keys())

    def model_summary(self) -> dict[str, Any]:
        """Build per-model breakdown with cumulative + window aggregates."""
        if self.db is not None:
            latest_raw = get_latest_model_metrics(self.db)
            daily = get_model_window_aggregate(self.db, self._start_of_day())
            weekly = get_model_window_aggregate(self.db, self._start_of_week())
            monthly = get_model_window_aggregate(self.db, self._start_of_month())
        else:
            latest_raw = self.raw_model_metrics
            daily = weekly = monthly = {}

        all_models: set[str] = set()
        for model_map in latest_raw.values():
            all_models.update(model_map.keys())
        all_models.update(daily.keys(), weekly.keys(), monthly.keys())

        models: list[dict[str, Any]] = []
        for model in sorted(all_models):
            entry: dict[str, Any] = {"model": model}
            for metric, model_vals in latest_raw.items():
                entry[metric] = model_vals.get(model, 0.0)
            for metric, val in daily.get(model, {}).items():
                entry[f"{metric}_daily"] = val
            for metric, val in weekly.get(model, {}).items():
                entry[f"{metric}_weekly"] = val
            for metric, val in monthly.get(model, {}).items():
                entry[f"{metric}_monthly"] = val
            models.append(entry)

        return {
            "models": models,
            "last_scrape": (
                self._format_ts(self.last_scrape.timestamp()) if self.last_scrape else None
            ),
        }

    def history_snapshots(self, limit: int) -> dict[str, Any]:
        """Return the most recent snapshots, clamped to a sane upper bound."""
        max_limit = (
            max(1, self.settings.history_size * 12) if self.settings.history_size > 0 else 10000
        )
        clamped = max(0, min(limit, max_limit))

        if self.db is not None:
            from .db import get_history

            snapshots = get_history(self.db, limit=clamped, tz=self.settings.tz)
            return {
                "snapshots": snapshots,
                "count": len(snapshots),
                "source": "sqlite",
            }
        if self.history is not None:
            snapshots = []
            for entry in list(self.history)[-clamped:]:
                out = {k: v for k, v in entry.items() if k != "ts"}
                out["timestamp"] = self._format_ts(entry["ts"])
                snapshots.append(out)
            return {
                "snapshots": snapshots,
                "count": len(snapshots),
                "source": "memory",
            }
        return {"snapshots": [], "count": 0, "source": "disabled"}

    def health(self) -> dict[str, str]:
        return {"status": "ok" if self.last_scrape else "starting"}
