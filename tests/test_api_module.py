"""Tests for api.py module-level functions and edge cases."""

from unittest.mock import patch

from litellm_pulse.api import _load_index_html, build_app, get_scraper
from litellm_pulse.config import Settings


class TestLoadIndexHtml:
    def test_loads_html_when_present(self):
        html = _load_index_html()
        assert "LiteLLM Pulse" in html
        assert "</html>" in html

    def test_fallback_when_file_missing(self):
        with patch("litellm_pulse.api._HTML_PATH") as mock_path:
            mock_path.read_text.side_effect = FileNotFoundError
            html = _load_index_html()
            assert "UI template missing" in html


class TestGetScraper:
    def test_retrieves_scraper_from_app_state(self):
        from litellm_pulse.scraper import Scraper

        app = build_app(Settings(metrics_url="http://test:4000/metrics/"))

        class FakeRequest:
            @property
            def app(self):
                return app

        scraper = get_scraper(FakeRequest())
        assert isinstance(scraper, Scraper)


class TestApiMain:
    def test_main_calls_config_main(self):
        with patch("litellm_pulse.config.main") as mock_main:
            from litellm_pulse.api import main

            main()
            mock_main.assert_called_once()


class TestBuildApp:
    def test_build_app_sets_state(self):
        app = build_app(Settings(metrics_url="http://test:4000/metrics/"))
        assert hasattr(app.state, "scraper")
        assert hasattr(app.state, "index_html")

    def test_build_app_routes_registered(self):
        app = build_app(Settings(metrics_url="http://test:4000/metrics/"))
        routes = {r.path for r in app.routes}
        assert "/" in routes
        assert "/api/v1/metrics" in routes
        assert "/api/v1/metrics/{name}" in routes
        assert "/api/v1/history" in routes
        assert "/api/v1/models" in routes
        assert "/raw" in routes
        assert "/health" in routes
