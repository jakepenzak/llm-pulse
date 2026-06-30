"""Tests for config module — timezone resolution, metric overrides, CLI, main()."""

from unittest.mock import patch

from litellm_pulse.config import Settings, _build_metric_overrides, _resolve_timezone


class TestResolveTimezone:
    def test_resolve_valid_timezone(self):
        name, tz = _resolve_timezone("America/New_York")
        assert name == "America/New_York"
        assert tz is not None

    def test_resolve_unknown_timezone_falls_back_to_utc(self, caplog):
        name, tz = _resolve_timezone("Not/A_Timezone")
        assert name == "UTC"
        from datetime import UTC

        assert tz is UTC
        assert "Unknown timezone" in caplog.text

    def test_resolve_exception_falls_back_to_utc(self, caplog):
        with patch("litellm_pulse.config.ZoneInfo", side_effect=RuntimeError("boom")):
            name, tz = _resolve_timezone("Any/Thing")
            assert name == "UTC"
            from datetime import UTC

            assert tz is UTC
            assert "Failed to load timezone" in caplog.text


class TestMetricOverrides:
    def test_env_override_populates_overrides(self, monkeypatch):
        monkeypatch.setenv("LITELLM_PULSE_METRIC_REQUESTS", "custom_requests_total")
        overrides = _build_metric_overrides()
        assert overrides["requests"] == "custom_requests_total"
        monkeypatch.delenv("LITELLM_PULSE_METRIC_REQUESTS", raising=False)

    def test_no_env_overrides_when_not_set(self, monkeypatch):
        monkeypatch.delenv("LITELLM_PULSE_METRIC_REQUESTS", raising=False)
        overrides = _build_metric_overrides()
        assert "requests" not in overrides

    def test_settings_from_env_applies_overrides(self, monkeypatch):
        monkeypatch.setenv("LITELLM_PULSE_METRIC_COST", "custom_spend_total")
        s = Settings.from_env()
        assert s.metric_map["cost"] == "custom_spend_total"
        monkeypatch.delenv("LITELLM_PULSE_METRIC_COST", raising=False)

    def test_settings_from_env_uses_default_when_no_override(self):
        s = Settings.from_env()
        assert s.metric_map["cost"] == "litellm_spend_metric_total"


class TestSettingsFromArgs:
    def test_args_override_metric(self):
        from litellm_pulse.config import Settings, build_arg_parser, settings_from_args

        env_settings = Settings.from_env()
        parser = build_arg_parser(env_settings)
        args = parser.parse_args(["--metrics-url", "http://custom:9090/metrics/"])
        settings = settings_from_args(args)
        assert settings.metrics_url == "http://custom:9090/metrics/"

    def test_args_override_scrape_interval(self):
        from litellm_pulse.config import Settings, build_arg_parser, settings_from_args

        env_settings = Settings.from_env()
        parser = build_arg_parser(env_settings)
        args = parser.parse_args(["--scrape-interval", "120"])
        settings = settings_from_args(args)
        assert settings.scrape_interval == 120

    def test_args_override_timezone(self):
        from litellm_pulse.config import Settings, build_arg_parser, settings_from_args

        env_settings = Settings.from_env()
        parser = build_arg_parser(env_settings)
        args = parser.parse_args(["--timezone", "America/New_York"])
        settings = settings_from_args(args)
        assert settings.tz_name == "America/New_York"

    def test_args_verify_ssl_true(self):
        from litellm_pulse.config import Settings, build_arg_parser, settings_from_args

        env_settings = Settings.from_env()
        parser = build_arg_parser(env_settings)
        args = parser.parse_args(["--verify-ssl"])
        settings = settings_from_args(args)
        assert settings.verify_ssl is True

    def test_args_no_verify_ssl(self):
        from litellm_pulse.config import Settings, build_arg_parser, settings_from_args

        env_settings = Settings.from_env()
        parser = build_arg_parser(env_settings)
        args = parser.parse_args(["--no-verify-ssl"])
        settings = settings_from_args(args)
        assert settings.verify_ssl is False


class TestMetricKeys:
    def test_metric_keys_is_tuple(self):
        s = Settings()
        keys = s.metric_keys
        assert isinstance(keys, tuple)
        assert "requests" in keys
        assert "cost" in keys

    def test_prom_to_friendly_includes_model_extras(self):
        s = Settings()
        rev = s.prom_to_friendly
        assert rev["litellm_deployment_total_requests_total"] == "deployment_requests"
        assert rev["litellm_deployment_success_responses_total"] == "deployment_success"
        assert rev["litellm_deployment_failure_responses_total"] == "deployment_failures"


class TestCLIMain:
    @patch("uvicorn.run")
    def test_main_starts_uvicorn(self, mock_run):
        from litellm_pulse.config import main

        main(["--host", "127.0.0.1", "--port", "9999"])
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["host"] == "127.0.0.1"
        assert call_kwargs["port"] == 9999

    @patch("uvicorn.run")
    def test_main_uses_defaults(self, mock_run):
        from litellm_pulse.config import main

        main([])
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["host"] == "0.0.0.0"
        assert call_kwargs["port"] == 8000
