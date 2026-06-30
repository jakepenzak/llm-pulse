"""FastAPI application — endpoints, lifespan, and HTML UI."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__
from .config import Settings
from .scraper import Scraper

logger = logging.getLogger("litellm-pulse")

_HTML_PATH = Path(__file__).parent / "static" / "index.html"


def _load_index_html() -> str:
    try:
        return _HTML_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("UI template not found at %s — serving placeholder", _HTML_PATH)
        return "<html><body><h1>LiteLLM Pulse</h1><p>UI template missing.</p></body></html>"


def get_scraper(request: Request) -> Scraper:
    """FastAPI dependency that returns the Scraper instance from app.state."""
    return request.app.state.scraper


def build_app(settings: Settings) -> FastAPI:
    """Construct a configured FastAPI app bound to a Scraper for ``settings``."""
    scraper = Scraper(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logging.basicConfig(
            level=settings.log_level,
            format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        )
        scraper.open_db()
        scrape_task = asyncio.create_task(scraper.scraper_loop())
        purge_task = asyncio.create_task(scraper.purge_loop())
        logger.info(
            "LiteLLM Pulse started — scraping %s every %ds, DB: %s, timezone: %s, auth: %s",
            settings.metrics_url,
            settings.scrape_interval,
            settings.db_path if scraper.db else "disabled",
            settings.tz_name,
            "enabled"
            if settings.metrics_api_key and settings.metrics_api_key.strip()
            else "disabled",
        )
        try:
            yield
        finally:
            scrape_task.cancel()
            purge_task.cancel()
            with suppress(asyncio.CancelledError):
                await scrape_task
            with suppress(asyncio.CancelledError):
                await purge_task
            scraper.close_db()

    app = FastAPI(
        title="LiteLLM Pulse",
        description="A lightweight metrics exporter for LiteLLM with SQLite time-series storage.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.scraper = scraper
    app.state.index_html = _load_index_html()

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        return HTMLResponse(content=app.state.index_html)

    @app.get("/api/v1/metrics")
    async def all_metrics(scraper: Scraper = Depends(get_scraper)) -> dict:  # noqa: B008
        return scraper.summary()

    @app.get("/api/v1/metrics/{name}")
    async def get_metric(name: str, scraper: Scraper = Depends(get_scraper)):  # noqa: B008
        result = scraper.metric(name)
        if result is not None:
            return result
        return JSONResponse(
            status_code=404,
            content={
                "error": f"Unknown metric: {name}",
                "available": scraper.available_metrics(),
            },
        )

    @app.get("/api/v1/history")
    async def history(limit: int = 168, scraper: Scraper = Depends(get_scraper)) -> dict:  # noqa: B008
        return scraper.history_snapshots(limit)

    @app.get("/api/v1/models")
    async def model_metrics(scraper: Scraper = Depends(get_scraper)) -> dict:  # noqa: B008
        return scraper.model_summary()

    @app.get("/raw")
    async def raw_metrics(scraper: Scraper = Depends(get_scraper)) -> dict:  # noqa: B008
        return scraper.raw_metrics

    @app.get("/health")
    async def health(scraper: Scraper = Depends(get_scraper)) -> dict:  # noqa: B008
        return scraper.health()

    return app


# Default app instance for ``uvicorn litellm_pulse.api:app`` and for tests.
_app_settings = Settings.from_env()
app = build_app(_app_settings)


def main():
    """Re-exported CLI entry point — see config.main for the real implementation."""
    from .config import main as _main

    _main()
