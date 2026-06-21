<p align="center">
  <img src="https://raw.githubusercontent.com/jakepenzak/llm-pulse/main/llm-pulse.svg" alt="LLM Pulse" width="320">
</p>

<p align="center">
  <a href="https://github.com/jakepenzak/llm-pulse/releases"><img src="https://img.shields.io/github/v/release/jakepenzak/llm-pulse" alt="GitHub release"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python 3.11+"></a>
  <a href="https://github.com/jakepenzak/llm-pulse/blob/main/LICENSE"><img src="https://img.shields.io/github/license/jakepenzak/llm-pulse" alt="License: MIT"></a>
  <a href="https://github.com/jakepenzak/llm-pulse"><img src="https://img.shields.io/badge/status-beta-yellow" alt="Development Status"></a>
  <br>
  <a href="https://github.com/jakepenzak/llm-pulse/actions/workflows/ci.yml"><img src="https://github.com/jakepenzak/llm-pulse/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/jakepenzak/llm-pulse/actions/workflows/release.yml"><img src="https://github.com/jakepenzak/llm-pulse/actions/workflows/release.yml/badge.svg" alt="Release"></a>
</p>



A lightweight metrics exporter for [LiteLLM](https://github.com/BerriAI/litellm) — scrapes Prometheus metrics, stores them in SQLite, and serves JSON for dashboards like [Homepage](https://gethomepage.dev) and home automation systems like [Home Assistant](https://www.home-assistant.io).

## What It Does

LiteLLM exposes usage metrics (requests, tokens, spend) in Prometheus text format as cumulative counters. LLM Pulse scrapes that endpoint on a schedule, parses the metrics, stores snapshots in SQLite, and serves them as clean JSON over a REST API.

Beyond raw cumulative totals, LLM Pulse computes **deltas** (change since last scrape), and **daily/weekly/monthly aggregates** (sum of deltas since the start of the current day/week/month) — all backed by SQLite for persistence across restarts.

```
LiteLLM /metrics  ──scrape──▶  LLM Pulse  ──JSON──▶  Homepage / Home Assistant / anything
                                   │
                                   ▼
                               SQLite
                          (time-series storage)
```

## Quick Start

### Docker Compose

```yaml
services:
  llm-pulse:
    image: ghcr.io/jakepenzak/llm-pulse:latest
    container_name: llm-pulse
    restart: unless-stopped
    environment:
      LLM_PULSE_METRICS_URL: "http://litellm:4000/metrics/"
      LLM_PULSE_SCRAPE_INTERVAL: "60"
      LLM_PULSE_PORT: "8000"
    ports:
      - "8000:8000"
    volumes:
      - llm-pulse-data:/app/data

volumes:
  llm-pulse-data:
```

### Running Locally (with uv)

```bash
uv sync
uv run llm-pulse
```

## Configuration

All configuration is via environment variables prefixed with `LLM_PULSE_`. No config files required.

### Core Settings

| Variable | Default | Description |
|---|---|---|
| `LLM_PULSE_METRICS_URL` | `http://litellm:4000/metrics/` | Prometheus metrics endpoint to scrape |
| `LLM_PULSE_SCRAPE_INTERVAL` | `60` | Seconds between scrapes |
| `LLM_PULSE_PORT` | `8000` | Port to serve the API on |
| `LLM_PULSE_HOST` | `0.0.0.0` | Address to bind to |
| `LLM_PULSE_VERIFY_SSL` | `false` | Whether to verify TLS certificates when scraping |
| `LLM_PULSE_SCRAPE_TIMEOUT` | `30` | Request timeout in seconds |
| `LLM_PULSE_LOG_LEVEL` | `info` | Log level (`debug`, `info`, `warning`, `error`) |

### SQLite / Time-Series Settings

| Variable | Default | Description |
|---|---|---|
| `LLM_PULSE_DB_PATH` | `./data/llm_pulse.db` | Path to the SQLite database file |
| `LLM_PULSE_DB_RETENTION_DAYS` | `90` | Auto-purge data older than N days (hourly purge cycle) |
| `LLM_PULSE_HISTORY_SIZE` | `168` | Max snapshots in the in-memory ring buffer (used as fallback if DB is unavailable) |

### Metric Mappings

Each tracked metric maps a friendly name to a Prometheus metric name. Override any of them by setting the corresponding `LLM_PULSE_METRIC_*` env var.

| Variable | Default |
|---|---|
| `LLM_PULSE_METRIC_REQUESTS` | `litellm_proxy_total_requests_metric_total` |
| `LLM_PULSE_METRIC_FAILED_REQUESTS` | `litellm_proxy_failed_requests_metric_total` |
| `LLM_PULSE_METRIC_TOKENS` | `litellm_total_tokens_metric_total` |
| `LLM_PULSE_METRIC_INPUT_TOKENS` | `litellm_input_tokens_metric_total` |
| `LLM_PULSE_METRIC_OUTPUT_TOKENS` | `litellm_output_tokens_metric_total` |
| `LLM_PULSE_METRIC_REASONING_TOKENS` | `litellm_output_reasoning_tokens_metric_total` |
| `LLM_PULSE_METRIC_COST` | `litellm_spend_metric_total` |
| `LLM_PULSE_METRIC_IN_FLIGHT_REQUESTS` | `litellm_in_flight_requests` |

## API Endpoints

### `GET /` or `GET /api/v1/metrics`

Returns all tracked metrics: cumulative totals, daily/weekly/monthly aggregates, and metadata.

```json
{
  "requests": 1234,
  "failed_requests": 5,
  "tokens": 567890,
  "input_tokens": 300000,
  "output_tokens": 267890,
  "reasoning_tokens": 0,
  "cost": 12.345678,
  "in_flight_requests": 2,
  "requests_daily": 215,
  "requests_weekly": 1200,
  "requests_monthly": 3400,
  "tokens_daily": 45000,
  "tokens_weekly": 310000,
  "tokens_monthly": 780000,
  "cost_daily": 0.02,
  "cost_weekly": 0.15,
  "cost_monthly": 0.52,
  "last_scrape": "2025-06-21T12:00:00+00:00",
  "source": "http://litellm:4000/metrics/"
}
```

Every tracked metric gets `_daily`, `_weekly`, and `_monthly` suffixes:

| Suffix | Meaning |
|---|---|
| _(none)_ | Cumulative total since LiteLLM started (raw counter value) |
| `_daily` | Sum of deltas since start of today (UTC midnight) |
| `_weekly` | Sum of deltas since start of this week (Monday UTC) |
| `_monthly` | Sum of deltas since start of this month (1st UTC) |

### `GET /api/v1/metrics/{name}`

Returns a single metric by friendly name. Also supports `_daily`, `_weekly`, `_monthly` suffixes.

```
GET /api/v1/metrics/cost
GET /api/v1/metrics/cost_daily
GET /api/v1/metrics/tokens_weekly
```

```json
{
  "name": "cost_daily",
  "value": 0.02,
  "last_scrape": "2025-06-21T12:00:00+00:00"
}
```

### `GET /api/v1/history?limit=168`

Returns the most recent scrape snapshots as a JSON array (newest last). Draws from SQLite if available, falls back to in-memory ring buffer.

```json
{
  "snapshots": [
    {
      "timestamp": "2025-06-21T12:00:00+00:00",
      "is_reset": false,
      "requests": 1234,
      "requests_delta": 3,
      "tokens": 567890,
      "tokens_delta": 24500,
      "cost": 12.3456,
      "cost_delta": 0.0231
    }
  ],
  "count": 168,
  "source": "sqlite"
}
```

### `GET /raw`

Returns all raw parsed Prometheus metrics (every metric family found, summed). Useful for debugging.

### `GET /health`

Returns `{"status": "ok"}` once the first successful scrape has completed.

## How Deltas & Aggregates Work

LiteLLM's Prometheus metrics are **counters** — they grow cumulatively and only reset when the LiteLLM process restarts. LLM Pulse handles this as follows:

1. **Each scrape** stores the raw cumulative value and a computed delta (change since the previous scrape).
2. **Daily/weekly/monthly** values are computed as `SUM(delta)` for all scrapes within the time window.
3. **Counter reset detection**: If any counter drops by more than 50%, LLM Pulse assumes LiteLLM restarted. The delta for that scrape is set to the current value (treating it as starting from 0), and `is_reset=true` is recorded in the database. This ensures daily/weekly/monthly sums remain correct even across LiteLLM restarts.

## State Recovery

| Scenario | Behavior |
|---|---|
| **Fresh start** | DB empty → first scrape has no deltas, second scrape onward has valid deltas |
| **App restart** | Reads last row from DB → restores last-known raw counters → seamless continuation |
| **LiteLLM restart** | Counters drop → reset detected → delta computed from 0, `is_reset=1` stored → daily sums remain correct |
| **DB corrupted** | `open_db()` catches SQLite errors, starts fresh with a warning log |
| **Disk full** | Writes fail → `error` field set in API response → recovers when disk space returns |

## Integrations

### Homepage (Custom API Widget)

Add a service entry in `services.yaml` with a `customapi` widget:

```yaml
- LiteLLM:
    icon: https://cdn.jsdelivr.net/gh/selfhst/icons/png/litellm.png
    href: https://litellm.home.lan
    description: LLM proxy and management
    widget:
      type: customapi
      url: http://llm-pulse:8000/api/v1/metrics
      refreshInterval: 60000
      mappings:
        - field: requests
          label: Total Requests
          format: number
        - field: cost_daily
          label: Spend Today
          format: float
          prefix: "$"
        - field: cost_monthly
          label: Spend This Month
          format: float
          prefix: "$"
        - field: tokens_daily
          label: Tokens Today
          format: number
```

### Home Assistant (REST Sensors)

Add RESTful sensors to `configuration.yaml`. The [`rest`](https://www.home-assistant.io/integrations/rest) integration lets you define multiple sensors from a single HTTP request, which avoids polling the LLM Pulse endpoint more than necessary:

```yaml
rest:
  - resource: http://llm-pulse:8000/api/v1/metrics
    scan_interval: 60        # seconds between polls (default: 30)
    timeout: 10              # seconds before the sensor is marked unavailable
    verify_ssl: true
    sensor:
      - name: LiteLLM Requests
        unique_id: litellm_requests
        value_template: "{{ value_json.requests }}"
        unit_of_measurement: "req"
        device_class: duration
        state_class: total_increasing
      - name: LiteLLM Tokens
        unique_id: litellm_tokens
        value_template: "{{ value_json.tokens }}"
        unit_of_measurement: "tokens"
        state_class: total_increasing
      - name: LiteLLM Spend
        unique_id: litellm_spend
        value_template: "{{ value_json.cost }}"
        unit_of_measurement: "USD"
        state_class: total_increasing
      - name: LiteLLM Spend Today
        unique_id: litellm_spend_today
        value_template: "{{ value_json.cost_daily }}"
        unit_of_measurement: "USD"
        state_class: measurement
        force_update: true
      - name: LiteLLM Spend This Month
        unique_id: litellm_spend_this_month
        value_template: "{{ value_json.cost_monthly }}"
        unit_of_measurement: "USD"
        state_class: measurement
        force_update: true
      - name: LiteLLM Tokens Today
        unique_id: litellm_tokens_today
        value_template: "{{ value_json.tokens_daily }}"
        unit_of_measurement: "tokens"
        state_class: measurement
        force_update: true
```

If you only need a single metric, you can use the [`sensor.rest`](https://www.home-assistant.io/integrations/sensor.rest/) platform instead, which polls the endpoint once per sensor:

```yaml
sensor:
  - platform: rest
    resource: http://llm-pulse:8000/api/v1/metrics/cost_daily
    name: LiteLLM Spend Today
    unique_id: litellm_spend_today
    value_template: "{{ value_json.value }}"
    unit_of_measurement: "USD"
    state_class: measurement
    force_update: true
```

> **Tip:** To refresh a sensor on demand (outside the polling schedule), call the `homeassistant.update_entity` action targeting the sensor entity.


## Contributing

Contributions are welcome! Please read the guidelines below before opening a pull request.

### Pull Request Process

1. Fork the repository and create a feature branch from `main`
2. Run `uv run pre-commit install` to set up local git hooks
3. Make your changes, ensuring `pre-commit run --all-files` passes
4. Add or update tests as appropriate
5. Open a pull request with a clear description of the changes

### Conventional Commits

**Pull request titles must follow the [Conventional Commits](https://www.conventionalcommits.org/) specification.** This is enforced by branch protection rules and is required for the release automation to work correctly.

The format is:

```
<type>(<scope>): <description>
```

#### Allowed Types

| Type | Description |
|---|---|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation only changes |
| `style` | Changes that do not affect the meaning of the code (formatting, etc.) |
| `refactor` | A code change that neither fixes a bug nor adds a feature |
| `perf` | A code change that improves performance |
| `test` | Adding or correcting tests |
| `ci` | Changes to CI configuration files and scripts |
| `chore` | Other changes that don't modify src or test files |
| `build` | Changes that affect the build system or dependencies |

#### Examples

- `feat: add Prometheus push gateway support`
- `fix(db): handle negative deltas on counter reset`
- `docs: update Home Assistant integration examples`
- `ci: add Python 3.13 to test matrix`
- `refactor(parser): simplify metric extraction logic`

#### Scopes (optional)

Common scopes: `parser`, `db`, `app`, `ci`, `docker`, `deps`

### Releases

Releases are managed automatically by [release-please](https://github.com/googleapis/release-please) using the [manifest-driven](https://github.com/googleapis/release-please/blob/main/docs/manifest-releaser.md) approach. Configuration lives in [`.github/release-please-config.json`](.github/release-please-config.json) and version tracking in [`.github/.release-please-manifest.json`](.github/.release-please-manifest.json).

When PRs with conventional commit titles are merged to `main`:

1. release-please maintains a "release PR" that accumulates changes and updates `CHANGELOG.md`
2. When the release PR is merged, a new GitHub Release is created with an auto-generated changelog (with emoji section headers)
3. release-please bumps the version in `pyproject.toml` and `llm_pulse/__init__.py`
4. The Docker build & publish workflow is triggered by the `release: published` event
5. Images are tagged with semantic version (e.g., `v0.1.0`), major/minor aliases (e.g., `0.1`, `0`), and `latest`

### Setup

```bash
uv sync                    # install all deps including dev tools
```

### Running

```bash
uv run llm-pulse           # run the server locally
```

### Linting & Formatting

Linting and formatting are enforced via [pre-commit](https://pre-commit.com) with [ruff](https://docs.astral.sh/ruff):

```bash
uv run pre-commit install           # install git hooks (run once)
uv run pre-commit run --all-files   # run all checks manually
```

This runs `ruff check --fix` and `ruff format` across the codebase. The same checks run in CI on every push and pull request.

### Testing

```bash
uv run pytest -v           # run tests
```

### CI/CD

| Workflow | Trigger | What it does |
|---|---|---|
| **CI** ([ci.yml](.github/workflows/ci.yml)) | Push to `main`, PRs | Runs pre-commit (ruff lint + format) and pytest on Python 3.11 & 3.12 |
| **Release Please** ([release-please.yml](.github/workflows/release-please.yml)) | Push to `main` | Manages versioning, generates changelog, creates GitHub releases |
| **Release** ([release.yml](.github/workflows/release.yml)) | GitHub Release published | Builds Docker image and publishes to `ghcr.io/jakepenzak/llm-pulse` with semantic version tags |

### Using the Pre-built Docker Image

Once a release is published, the image is available on GHCR:

```yaml
services:
  llm-pulse:
    image: ghcr.io/jakepenzak/llm-pulse:latest
    container_name: llm-pulse
    restart: unless-stopped
    environment:
      LLM_PULSE_METRICS_URL: "http://litellm:4000/metrics/"
    ports:
      - "8000:8000"
    volumes:
      - llm-pulse-data:/app/data

volumes:
  llm-pulse-data:
```

## License

MIT — see [LICENSE](LICENSE).
