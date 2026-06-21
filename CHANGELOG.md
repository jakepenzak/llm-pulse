# Changelog

## 0.1.0 (2026-06-21)

Initial release of LLM Pulse, a lightweight service that scrapes LiteLLM Prometheus metrics and exposes them as JSON for Homepage widgets and Home Assistant sensors. Features a FastAPI application with a Prometheus text format parser, SQLite time-series storage with daily/weekly/monthly aggregates and counter reset detection, and REST endpoints for cost and token metrics. Includes 49 pytest tests, pre-commit linting with ruff, and CI/CD pipelines with automated releases via release-please and Docker image publishing to GHCR.
