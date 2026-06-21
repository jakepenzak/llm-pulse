# Changelog

## 0.1.0 (2026-06-21)

Initial release of LLM Pulse, a lightweight service that scrapes LiteLLM Prometheus metrics and exposes them as JSON for Homepage widgets and Home Assistant sensors. Features a FastAPI application with a Prometheus text format parser, SQLite time-series storage with daily/weekly/monthly aggregates and counter reset detection, and REST endpoints for cost and token metrics. Includes 49 pytest tests, pre-commit linting with ruff, and CI/CD pipelines with automated releases via release-please and Docker image publishing to GHCR.

### ✨ Features

* initial LLM Pulse metrics exporter ([00f72f2](https://github.com/jakepenzak/llm-pulse/commit/00f72f299801e5daaaa0c7362795a9d4980b5e8f))
* SQLite time-series storage with daily/weekly/monthly aggregates ([c24ce5f](https://github.com/jakepenzak/llm-pulse/commit/c24ce5f453f12a170bfe8f3cb86a7ba5c30af2d9))


### 🐛 Bug Fixes

* copy README.md into Docker image for hatchling build ([4863ec4](https://github.com/jakepenzak/llm-pulse/commit/4863ec4baf1824f447de6efb016fd69d68ea4003))
* merge release workflows and reset version to 0.0.0 ([d666e16](https://github.com/jakepenzak/llm-pulse/commit/d666e1639ec50dd891cc4439d8ebf79ab929bceb))
* release-please manifest path ([ce54517](https://github.com/jakepenzak/llm-pulse/commit/ce54517f88cf740156cce2cf2eb04a2a9abf11fa))

### 🔧 CI/CD

* add CI/CD, release-please, tests, and project infrastructure ([4b184d3](https://github.com/jakepenzak/llm-pulse/commit/4b184d3b99635cc1ce49a00a89405ef5b956409d))
