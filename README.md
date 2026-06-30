<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jakepenzak/litellm-pulse/main/assets/litellm-pulse-dark.svg">
    <img src="https://raw.githubusercontent.com/jakepenzak/litellm-pulse/main/assets/litellm-pulse.svg" alt="LiteLLM Pulse" width="320">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/jakepenzak/litellm-pulse/releases"><img src="https://img.shields.io/github/v/release/jakepenzak/litellm-pulse" alt="GitHub release"></a>
  <a href="https://pypi.org/project/litellm-pulse/"><img src="https://img.shields.io/pypi/v/litellm-pulse" alt="PyPI version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python 3.11+"></a>
  <a href="https://github.com/jakepenzak/litellm-pulse/blob/main/LICENSE"><img src="https://img.shields.io/github/license/jakepenzak/litellm-pulse" alt="License: MIT"></a>
  <a href="https://github.com/jakepenzak/litellm-pulse"><img src="https://img.shields.io/badge/status-beta-yellow" alt="Development Status"></a>
  <br>
  <a href="https://github.com/jakepenzak/litellm-pulse/actions/workflows/ci.yml"><img src="https://github.com/jakepenzak/litellm-pulse/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/jakepenzak/litellm-pulse/actions/workflows/release.yml"><img src="https://github.com/jakepenzak/litellm-pulse/actions/workflows/release.yml/badge.svg" alt="Release"></a>
</p>



A lightweight metrics exporter for [LiteLLM](https://github.com/BerriAI/litellm) — scrapes Prometheus metrics, stores them in SQLite, and serves JSON for dashboards like [Homepage](https://gethomepage.dev) and home automation systems like [Home Assistant](https://www.home-assistant.io).

## Why

LiteLLM exposes usage metrics in Prometheus format, but consuming them typically means standing up Prometheus, Grafana, and an alertmanager — a stack that's overkill if you just want to see "how much did I spend today?" on a dashboard. For homelab enthusiasts running LiteLLM alongside services like Homepage and Home Assistant, that's a lot of overhead for very simple needs.

LiteLLM Pulse is a lightweight observability layer that sits between LiteLLM's `/metrics` endpoint and a JSON-based REST API. It scrapes Prometheus text format on a schedule, stores time-series snapshots in SQLite, and serves clean JSON that any HTTP client can consume — no Prometheus server, no Grafana dashboards, no query language to learn.

It is **not** designed to replace Prometheus or Grafana. If you need multi-source metrics, complex alerting rules, or rich visual dashboards, use those tools. LiteLLM Pulse is for the 90% case: you have a single LiteLLM instance, you want today's token spend on your Homepage dashboard, and you don't want to run three more containers to get it.

## What It Does

LiteLLM exposes usage metrics (requests, tokens, spend) in Prometheus text format as cumulative counters. LiteLLM Pulse scrapes that endpoint on a schedule, parses the metrics, stores snapshots in SQLite, and serves them as clean JSON over a REST API.

Beyond raw cumulative totals, LiteLLM Pulse computes **deltas** (change since last scrape), and **daily/weekly/monthly aggregates** (sum of deltas since the start of the current day/week/month) — all backed by SQLite for persistence across restarts. It also breaks down all metrics that carry a `model` label **per model**, so you can see which models are being used and how much each costs.

```
LiteLLM /metrics  ──scrape──▶  LiteLLM Pulse  ──JSON──▶  Homepage / Home Assistant / anything
                                    │
                                    ▼
                                SQLite
                           (time-series storage)
```

## LiteLLM Setup

> **The LiteLLM `/metrics` endpoint is not enabled by default.** You must configure LiteLLM to publish Prometheus metrics before LiteLLM Pulse can scrape them.

Add the `prometheus` callback to your LiteLLM proxy config (`config.yaml`):

```yaml
litellm_settings:
  callbacks:
    - prometheus
```

Start LiteLLM and verify the endpoint:

```bash
curl http://localhost:4000/metrics/
```

If you see Prometheus-formatted text, LiteLLM is publishing metrics and you're ready to set up LiteLLM Pulse.

See the [LiteLLM Prometheus docs](https://docs.litellm.ai/docs/proxy/prometheus) for advanced configuration options.

## Quick Start

### Docker Compose

```yaml
services:
  litellm-pulse:
    image: ghcr.io/jakepenzak/litellm-pulse:latest
    container_name: litellm-pulse
    restart: unless-stopped
    environment:
      LITELLM_PULSE_METRICS_URL: "http://litellm:4000/metrics/"
      LITELLM_PULSE_SCRAPE_INTERVAL: "60"
      LITELLM_PULSE_PORT: "8000"
      LITELLM_PULSE_TIMEZONE: "America/New_York"
      # LITELLM_PULSE_METRICS_API_KEY: "sk-your-litellm-api-key"
    ports:
      - "8000:8000"
    volumes:
      - litellm-pulse-data:/app/data

volumes:
  litellm-pulse-data:
```

### Docker Run

```bash
docker run -d \
  --name litellm-pulse \
  -p 8000:8000 \
  -e LITELLM_PULSE_METRICS_URL=http://litellm:4000/metrics/ \
  -e LITELLM_PULSE_SCRAPE_INTERVAL=60 \
  -e LITELLM_PULSE_TIMEZONE=America/New_York \
  -v litellm-pulse-data:/app/data \
  ghcr.io/jakepenzak/litellm-pulse:latest
```


> **Pre-release Docker images** are also available with the `:dev` tag:
> ```bash
> ghcr.io/jakepenzak/litellm-pulse:dev
> ```

### Running Locally (with uv)

```bash
uv sync
uv run litellm-pulse
```

### Running from PyPI

```bash
uvx litellm-pulse                                # run directly
uv tool install litellm-pulse && litellm-pulse   # install permanently
pip install litellm-pulse && litellm-pulse       # with pip
```

### Running dev/prerelease versions

```bash
uvx --prerelease=allow litellm-pulse             # uvx with prereleases
pip install --pre litellm-pulse                  # pip with prereleases
```

All of the above accept CLI arguments:

```bash
uv run litellm-pulse --port 9000 --metrics-url http://localhost:4000/metrics/ --timezone America/New_York
```

## Configuration

All settings can be specified via environment variables prefixed with `LITELLM_PULSE_`, CLI arguments, or both. CLI arguments take precedence over environment variables. No config files required.

Run `litellm-pulse --help` to see all available options.

### Core Settings

| CLI Flag | Env Variable | Default | Description |
|---|---|---|---|
| `--metrics-url` | `LITELLM_PULSE_METRICS_URL` | `http://litellm:4000/metrics/` | Prometheus metrics endpoint to scrape |
| `--scrape-interval` | `LITELLM_PULSE_SCRAPE_INTERVAL` | `60` | Seconds between scrapes |
| `--port` | `LITELLM_PULSE_PORT` | `8000` | Port to serve the API on |
| `--host` | `LITELLM_PULSE_HOST` | `0.0.0.0` | Address to bind to |
| `--verify-ssl` / `--no-verify-ssl` | `LITELLM_PULSE_VERIFY_SSL` | `false` | Whether to verify TLS certificates when scraping |
| `--scrape-timeout` | `LITELLM_PULSE_SCRAPE_TIMEOUT` | `30` | Request timeout in seconds |
| `--log-level` | `LITELLM_PULSE_LOG_LEVEL` | `info` | Log level (`debug`, `info`, `warning`, `error`) |
| `--timezone` | `LITELLM_PULSE_TIMEZONE` | `UTC` | Timezone for API timestamps and day/week/month boundaries (IANA name, e.g. `America/New_York`) |
| `--metrics-api-key` | `LITELLM_PULSE_METRICS_API_KEY` | _(empty)_ | LiteLLM API key for authenticated `/metrics` endpoints. Only needed if your LiteLLM proxy has [`require_auth_for_metrics_endpoint`](https://docs.litellm.ai/docs/proxy/prometheus#add-authentication-on-metrics-endpoint) set to `true`. |

> **When to use `LITELLM_PULSE_METRICS_API_KEY` / `--metrics-api-key`:** If your LiteLLM proxy config includes `require_auth_for_metrics_endpoint: true` under `litellm_settings`, the `/metrics` endpoint requires authentication via a `Bearer` token. Set `LITELLM_PULSE_METRICS_API_KEY` (or pass `--metrics-api-key`) to a valid LiteLLM API key so LiteLLM Pulse can authenticate. If left empty, no `Authorization` header is sent — matching the default unauthenticated LiteLLM behavior.

### SQLite / Time-Series Settings

| CLI Flag | Env Variable | Default | Description |
|---|---|---|---|
| `--db-path` | `LITELLM_PULSE_DB_PATH` | `./data/litellm_pulse.db` | Path to the SQLite database file |
| `--db-retention-days` | `LITELLM_PULSE_DB_RETENTION_DAYS` | `90` | Auto-purge data older than N days (hourly purge cycle) |
| `--history-size` | `LITELLM_PULSE_HISTORY_SIZE` | `168` | Max snapshots in the in-memory ring buffer (used as fallback if DB is unavailable) |

> **Timezone note:** The database always stores timestamps as UTC. The `LITELLM_PULSE_TIMEZONE` setting (or `--timezone` flag) only affects API output (timestamps are converted to the configured timezone) and aggregate window boundaries (daily/weekly/monthly resets are computed against the configured timezone's midnight/Monday/1st). Set it to any valid [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (e.g. `America/New_York`, `Europe/London`). Invalid values fall back to UTC with a warning.

### Metric Mappings

Each tracked metric maps a friendly name to a Prometheus metric name. Override any of them by setting the corresponding `LITELLM_PULSE_METRIC_*` env var.

| Variable | Default |
|---|---|
| `LITELLM_PULSE_METRIC_REQUESTS` | `litellm_proxy_total_requests_metric_total` |
| `LITELLM_PULSE_METRIC_FAILED_REQUESTS` | `litellm_proxy_failed_requests_metric_total` |
| `LITELLM_PULSE_METRIC_TOKENS` | `litellm_total_tokens_metric_total` |
| `LITELLM_PULSE_METRIC_INPUT_TOKENS` | `litellm_input_tokens_metric_total` |
| `LITELLM_PULSE_METRIC_OUTPUT_TOKENS` | `litellm_output_tokens_metric_total` |
| `LITELLM_PULSE_METRIC_REASONING_TOKENS` | `litellm_output_reasoning_tokens_metric_total` |
| `LITELLM_PULSE_METRIC_COST` | `litellm_spend_metric_total` |
| `LITELLM_PULSE_METRIC_IN_FLIGHT_REQUESTS` | `litellm_in_flight_requests` |
| `LITELLM_PULSE_METRIC_CACHE_HITS` | `litellm_cache_hits_metric_total` |
| `LITELLM_PULSE_METRIC_CACHE_MISSES` | `litellm_cache_misses_metric_total` |
| `LITELLM_PULSE_METRIC_CACHED_TOKENS` | `litellm_cached_tokens_metric_total` |
| `LITELLM_PULSE_METRIC_INPUT_CACHED_TOKENS` | `litellm_input_cached_tokens_metric_total` |
| `LITELLM_PULSE_METRIC_INPUT_CACHE_CREATION_TOKENS` | `litellm_input_cache_creation_tokens_metric_total` |

## API Endpoints

### `GET /`

Serves a built-in web dashboard showing key metrics at a glance — spend, tokens, requests, cache stats, and a per-model breakdown table. Auto-refreshes every 30 seconds. No extra setup required.

### `GET /api/v1/metrics`

Returns all tracked metrics as JSON: cumulative totals, daily/weekly/monthly aggregates, and metadata.

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
  "cache_hits": 40,
  "cache_misses": 60,
  "cached_tokens": 15000,
  "input_cached_tokens": 8000,
  "input_cache_creation_tokens": 2000,
  "requests_daily": 215,
  "requests_weekly": 1200,
  "requests_monthly": 3400,
  "tokens_daily": 45000,
  "tokens_weekly": 310000,
  "tokens_monthly": 780000,
  "cost_daily": 0.02,
  "cost_weekly": 0.15,
  "cost_monthly": 0.52,
  "cache_hits_daily": 5,
  "cache_misses_daily": 12,
  "cached_tokens_daily": 3000,
  "last_scrape": "2025-06-21T12:00:00+00:00",
  "source": "http://litellm:4000/metrics/"
}
```

Every tracked metric gets `_daily`, `_weekly`, and `_monthly` suffixes:

| Suffix | Meaning |
|---|---|
| _(none)_ | Cumulative total since LiteLLM started (raw counter value) |
| `_daily` | Sum of deltas since start of today (midnight in the configured timezone) |
| `_weekly` | Sum of deltas since start of this week (Monday in the configured timezone) |
| `_monthly` | Sum of deltas since start of this month (1st in the configured timezone) |

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

### `GET /api/v1/models`

Returns per-model breakdown of all metrics that carry a `model` label in the Prometheus data. Each model includes cumulative totals and daily/weekly/monthly aggregates.

```json
{
  "models": [
    {
      "model": "gpt-4o",
      "requests": 800,
      "tokens": 40000,
      "cost": 2.0,
      "deployment_requests": 80,
      "requests_daily": 50,
      "tokens_daily": 5000,
      "cost_daily": 0.15,
      "deployment_requests_daily": 5
    },
    {
      "model": "claude-sonnet",
      "requests": 200,
      "tokens": 10000,
      "cost": 0.5,
      "requests_daily": 10,
      "tokens_daily": 1000,
      "cost_daily": 0.02
    }
  ],
  "last_scrape": "2025-06-21T12:00:00+00:00"
}
```

The set of metrics per model depends on what LiteLLM exposes with `model` labels. Common metrics include `requests`, `tokens`, `input_tokens`, `output_tokens`, `cost`, `cache_hits`, `cache_misses`, `cached_tokens`, `deployment_requests`, `deployment_success`, and `deployment_failures`. Metrics without a known friendly name retain their raw Prometheus metric name.

### `GET /raw`

Returns all raw parsed Prometheus metrics (every metric family found, summed). Useful for debugging.

### `GET /health`

Returns `{"status": "ok"}` once the first successful scrape has completed.

## How Deltas & Aggregates Work

LiteLLM's Prometheus metrics are **counters** — they grow cumulatively and only reset when the LiteLLM process restarts. LiteLLM Pulse handles this as follows:

1. **Each scrape** stores the raw cumulative value and a computed delta (change since the previous scrape).
2. **Daily/weekly/monthly** values are computed as `SUM(delta)` for all scrapes within the time window.
3. **Counter reset detection**: If the primary `requests` counter drops by more than 50%, LiteLLM Pulse assumes LiteLLM restarted. The delta for that scrape is set to the current value (treating it as starting from 0), and `is_reset=true` is recorded in the database. This ensures daily/weekly/monthly sums remain correct even across LiteLLM restarts.

## State Recovery

| Scenario | Behavior |
|---|---|
| **Fresh start** | DB empty → first scrape has no deltas, second scrape onward has valid deltas |
| **App restart** | Reads last row from DB → restores last-known raw counters (flat + per-model) → seamless continuation |
| **LiteLLM restart** | Counters drop → reset detected → delta computed from 0, `is_reset=1` stored → daily sums remain correct |
| **DB corrupted** | `open_db()` catches SQLite errors, starts fresh with a warning log |
| **Disk full** | Writes fail → `error` field set in API response → recovers when disk space returns |
| **DB schema upgrade** | New columns auto-added to existing `scrapes` table via `ALTER TABLE` migration |

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
      url: http://litellm-pulse:8000/api/v1/metrics
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
        - field: cache_hits_daily
          label: Cache Hits Today
          format: number
```

### Home Assistant (REST Sensors)

Add RESTful sensors to `configuration.yaml`. The [`rest`](https://www.home-assistant.io/integrations/rest) integration lets you define multiple sensors from a single HTTP request, which avoids polling the LiteLLM Pulse endpoint more than necessary:

```yaml
rest:
  - resource: http://litellm-pulse:8000/api/v1/metrics
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
      - name: LiteLLM Cache Hits Today
        unique_id: litellm_cache_hits_today
        value_template: "{{ value_json.cache_hits_daily }}"
        unit_of_measurement: "hits"
        state_class: measurement
        force_update: true
      - name: LiteLLM Cached Tokens Today
        unique_id: litellm_cached_tokens_today
        value_template: "{{ value_json.cached_tokens_daily }}"
        unit_of_measurement: "tokens"
        state_class: measurement
        force_update: true
```

If you only need a single metric, you can use the [`sensor.rest`](https://www.home-assistant.io/integrations/sensor.rest/) platform instead, which polls the endpoint once per sensor:

```yaml
sensor:
  - platform: rest
    resource: http://litellm-pulse:8000/api/v1/metrics/cost_daily
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
5. Open a pull request **targeting the `main` branch**

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

Releases are managed automatically by [release-please](https://github.com/googleapis/release-please) using the [manifest-driven](https://github.com/googleapis/release-please/blob/main/docs/manifest-releaser.md) approach. Configuration lives in [`.github/release-please-config.json`](.github/release-please-config.json) (stable) and [`.github/release-please-config-prerelease.json`](.github/release-please-config-prerelease.json) (prerelease); version tracking in [`.github/.release-please-manifest.json`](.github/.release-please-manifest.json) (stable) and [`.github/.release-please-manifest-prerelease.json`](.github/.release-please-manifest-prerelease.json) (prerelease). The two manifests are independent so cutting a stable release doesn't reset prerelease version tracking, and vice versa.

**Single-branch model:** everything lives on `main`. The [Release workflow](.github/workflows/release.yml) selects the appropriate config based on the trigger:

- **Push to `main`** — runs release-please with the **prerelease** config → creates prerelease PRs (`v0.3.0.dev0`, `v0.3.0.dev1`, …), Docker tag `dev`, PyPI prerelease packages.
- **Manual `workflow_dispatch` with `stable_release: true`** — runs release-please with the **stable** config → creates a stable release PR (`v0.3.0`), Docker tags `latest`/`major`/`major.minor`/version, PyPI stable packages.

**Workflow:**

1. PRs are merged to `main` with conventional commit titles
2. release-please maintains a "release PR" that accumulates changes (prerelease by default)
3. When the prerelease PR is merged, a GitHub prerelease is created, Docker image is pushed with `dev` tag, and the package is published to PyPI as a prerelease
4. To cut a stable release, manually trigger the workflow with `stable_release: true` — release-please calculates the next stable version from the last stable tag and creates a release PR
5. When the stable release PR is merged, images are tagged with semantic version (e.g., `0.3.0`), major/minor aliases (e.g., `0.3`, `0`), and `latest`

### Setup

```bash
make venv                  # sync deps + install pre-commit hooks
# or: uv sync --all-extras --all-groups --frozen && uv run pre-commit install
```

### Running

```bash
uv run litellm-pulse       # run the server locally
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
# or: make tests           # runs pytest
# or: make coverage        # serve HTML coverage report at http://localhost:8080
```

> Run `make help` to see all available targets.

### CI/CD

| Workflow | Trigger | What it does |
|---|---|---|
| **CI** ([ci.yml](.github/workflows/ci.yml)) | Push to `main`, PRs | Runs pre-commit (ruff lint + format) and pytest on Python 3.11 & 3.12 |
| **Release** ([release.yml](.github/workflows/release.yml)) | Push to `main` / `workflow_dispatch` | `push` → prerelease config (dev tags, PyPI prerelease). `workflow_dispatch` with `stable_release: true` → stable config (semantic versioned Docker tags, PyPI stable). See [Releases](#releases). |

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

LiteLLM Pulse is an independent, community-developed project created to provide monitoring and analytics for LiteLLM deployments.

This project is **not affiliated with, endorsed by, sponsored by, or maintained by** LiteLLM or Berri AI.

"LiteLLM" and any associated trademarks, service marks, logos, or trade names are the property of their respective owners and are used here solely to identify compatibility with the LiteLLM ecosystem.
