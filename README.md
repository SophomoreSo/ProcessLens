# ProcessLens

ProcessLens is a local-first, single-user web application for **process mining and process
analysis of case-centric event logs**. A business analyst can import a CSV event log, map and
validate it, and then explore the process: KPIs, a directly-follows graph, variants, case
histories, performance, rule-based conformance, cohort comparisons, in-app alerts and exports.

> ProcessLens is an independent implementation with its own branding. It is inspired by the
> kind of analysis commercial process-mining suites offer, but it is **not affiliated with
> Celonis**, does not claim feature parity, and is not compatible with proprietary Celonis
> features or formats.

Precise definitions of every metric, filter and rule: **[docs/METRICS.md](docs/METRICS.md)**.

---

## Quick start

Requirements: **Python 3.11** (tested with 3.11.6), macOS/Linux/Windows.

```bash
python3.11 -m venv .venv
```

```bash
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

```bash
pip install -r requirements-dev.txt
```

```bash
python -m app
```

Open <http://127.0.0.1:8000>, go to **Datasets → Load demo data**, and explore. The database
is created and migrated automatically on first start (`./data/processlens.db`).

With [uv](https://docs.astral.sh/uv/) instead of pip: `uv venv --python 3.11 .venv` then
`uv pip install -r requirements-dev.txt`.

Runtime-only install (no test tools): `pip install -r requirements.txt`.

### Run the tests

```bash
python -m pytest
```

### Docker

```bash
docker compose up --build
```

The container listens on all interfaces *inside* the container, and `compose.yaml` publishes
the port on **127.0.0.1 only**. Data lives in the `processlens-data` volume and survives
restarts. Without compose: `docker build -t processlens .` and
`docker run -p 127.0.0.1:8000:8000 -v processlens-data:/data processlens`.

### Configuration

All settings are environment variables with the prefix `PROCESSLENS_` (or a `.env` file);
see [.env.example](.env.example). Important ones: `HOST` (default `127.0.0.1`), `PORT`,
`DATA_DIR`, `DATABASE_URL`, `SECRET_KEY`, `MAX_UPLOAD_MB` (200), `MAX_ROWS` (1,000,000),
`ALLOWED_HOSTS`.

The launcher (`python -m app`) **refuses to bind to a non-loopback address** unless
`PROCESSLENS_ALLOW_NON_LOOPBACK_BIND=true` is set (the Docker image sets it because the port
is published on loopback only).

---

## Assumptions and architecture

* **Single user, single process, local machine.** No authentication; bound to localhost.
* **Case-centric event logs**: every event belongs to exactly one case. Object-centric logs
  are a roadmap item.
* **Business logic in Python.** The browser only fetches, formats and renders; every number
  on screen comes from the API.
* **Explicitness over convenience**: nothing is silently dropped, timezones and DST handling
  are chosen explicitly, a case is never assumed complete without a completion rule, and
  results that the data cannot establish are reported as *not evaluable*.

```
Browser (Jinja2 pages + vanilla JS modules, Cytoscape.js, Plotly.js)
   │  fetch() — same origin, CSRF header on state changes
FastAPI routers (app/api)  ──  Pydantic schemas (app/schemas)
   │
Services (app/services)
   ingestion → validation → importer/jobs ─┐
   eventlog (cached per dataset version)   ├─ filtering ─ analytics / conformance / alerts / exports
   cache (LRU keyed by version + filters) ─┘
   │
SQLAlchemy models (app/models) + Alembic migrations  →  SQLite (default) / PostgreSQL (configurable)
```

| Layer | Technology |
|---|---|
| API & pages | FastAPI 0.156, Starlette, Jinja2 templates, OpenAPI at `/docs` |
| Validation | Pydantic 2 |
| Persistence | SQLAlchemy 2.1, Alembic, SQLite (WAL) |
| Analysis | pandas 3.0, numpy; PM4Py 2.7 for XES export and as an independent cross-check of DFG discovery in the tests |
| Frontend | HTML5, CSS3, vanilla ES modules, Cytoscape.js 3.30 (+ dagre layout), Plotly.js 2.35 (basic bundle) — vendored in `app/static/vendor`, no CDN at runtime |
| Tests | pytest (71 tests) |

### Project structure

```
app/
  main.py            FastAPI app factory, middleware, routers
  __main__.py        safe launcher (`python -m app`)
  config.py          settings from environment
  database.py        engine/session (SQLite pragmas, PostgreSQL-ready)
  migrate.py         programmatic `alembic upgrade head`
  security.py        CSRF token, Origin check, security headers / CSP
  errors.py          uniform JSON error responses
  models/            Upload, Dataset, Event, ImportFinding, QuarantinedRow, ImportJob,
                     ConformanceRule, AlertRule, Alert
  schemas/           Pydantic request/response models, FilterSpec
  api/               imports, datasets, analytics, conformance, alerts, exports, meta, pages
  services/          ingestion, validation, importer, jobs, eventlog, filtering, analytics,
                     conformance, alerts, exports, cache, demo
  templates/         Jinja2 pages (base + one per page)
  static/css|js|img  styles and page modules
  static/vendor/     Cytoscape.js, dagre, cytoscape-dagre, Plotly.js (MIT, see README there)
migrations/          Alembic environment + versions
sample_data/         synthetic demo log + validation-demo file
scripts/             generate_demo_data.py, benchmark.py
tests/               pytest suite + hand-checkable fixture
docs/METRICS.md      metric, filter, rule definitions
Dockerfile, compose.yaml, requirements*.txt, .env.example, pyproject.toml
```

---

## Using ProcessLens

### Import wizard (Import page)

1. **Upload** a CSV (`.csv`, `.txt`, `.tsv`). The file gets a server-generated name; size, row,
   column and field-length limits are enforced; binary files are refused.
2. **Preview & mapping**: sample rows, detected encoding/delimiter (overridable), suggested
   column mapping. Required: `case_id`, `activity`, `timestamp`. Optional: `event_id`,
   `resource`, `department`, `cost`, `start_timestamp`, `case_status`, plus any extra columns
   kept as scalar event attributes. All values are read as **text** (leading zeros survive).
3. **Timestamps**: format (`ISO8601`, presets, custom strftime, Unix s/ms — with the share of
   sampled values each candidate parses), source timezone, display timezone, and an explicit
   policy for **ambiguous** (DST fall-back: reject / earliest / latest) and **nonexistent**
   (DST spring-forward: reject / shift forward / shift backward) local times.
4. **Validation findings**: every row is validated — missing case IDs, empty activities,
   missing/malformed timestamps, DST problems, duplicate event IDs (all rows sharing an ID are
   flagged), invalid or non-finite costs, start later than completion, wrong field counts.
   Warnings cover timestamp ties, negative costs, trimmed whitespace. Download the full report
   as CSV or JSON.
5. **Confirm**: new dataset or new version of an existing one, invalid-row policy —
   **reject the import** if any row is invalid, or **quarantine** invalid rows (stored with
   their reasons, excluded from analysis, downloadable) — completion rule and reference time.

The import runs as a background job with progress; all data is written in **one transaction**
so a failed or interrupted import never leaves partial data. Each successful import
increments the dataset **version**, invalidates cached analytics and evaluates alert rules.

### Pages

| Page | What it shows |
|---|---|
| **Overview** | Case/event/activity counts, completed vs incomplete/unknown, observed duration, completed throughput and open-case age (each with its population and n), rework rate, variant count, daily case starts, duration distribution, top variants, bottleneck candidates. |
| **Process explorer** | Directly-follows graph with synthetic start/end markers, frequency or elapsed-time mode (median/p90/mean), edge occurrences and distinct cases in tooltips, zoom/pan/fit/reset, render-only node/edge thresholds, legend, click-through to matching cases, transitions CSV and XES export. |
| **Variants** | Sequences with case count, share, cumulative coverage, median/p90 observed duration and rework markers. Selecting a variant highlights its path and lists its cases; it can be applied as a global filter. |
| **Cases** | Paginated, sortable table; case timeline with timestamps (display timezone), resources, departments, costs, execution times, gaps, tie markers, attributes and conformance findings. |
| **Performance** | Transitions ranked by median/p90 elapsed time; resource and department comparisons with sample sizes; activity execution durations (when start timestamps exist); two-cohort comparison. All labeled as descriptive associations. |
| **Conformance** | Rule CRUD (required, forbidden, A precedes B, max duration, max occurrences, start/end activity), compliant/violating/not-evaluable counts, case-level explanations, “use as filter”, CSV export. |
| **Alerts** | Alert rules (duration threshold, rework, conformance violation), “Evaluate rules now”, alert list with open/acknowledged/resolved workflow. |
| **Datasets** | Load demo data, dataset list with versions and source metadata, analysis settings (completion rule, reference time, display timezone), import findings and quarantine downloads, re-import, deletion with typed-name confirmation. |

The **filter bar** (top of every analysis page) applies case-level filters — case-start date
range, activity presence/absence, resource, department, variant, duration, rework, completion
status, conformance result, transition — shown as removable chips and kept in the URL.

### Exports

`GET /api/datasets/{id}/exports/{kind}.{csv|json}` for `cases`, `events`, `variants`,
`transitions`, `conformance` (with `rule_id`), `alerts`, plus `summary.json` and `events.xes`
(via PM4Py). Every export accepts the same filter parameters and therefore contains exactly
the population shown on screen. Text cells beginning with `= + - @`, tab or CR are prefixed
with `'` in CSV to prevent spreadsheet formula injection.

### API

Interactive OpenAPI documentation: <http://127.0.0.1:8000/docs>. Main routes:

| Area | Routes |
|---|---|
| Health/meta | `GET /api/health`, `GET /api/session` (CSRF token), `GET /api/meta/timezones` |
| Import | `POST /api/uploads`, `GET /api/uploads/{id}`, `POST /api/uploads/{id}/validate`, `GET /api/uploads/{id}/validation-report`, `POST /api/uploads/{id}/import`, `GET /api/jobs/{id}`, `POST /api/demo/load`, `GET /api/sample-data/{name}` |
| Datasets | `GET /api/datasets`, `GET/PATCH/DELETE /api/datasets/{id}` (`DELETE` needs `confirm_name`), `GET …/validation-report`, `GET …/quarantine` |
| Analytics | `GET …/overview`, `…/filter-options`, `…/graph`, `…/variants`, `…/cases`, `…/cases/{case_id}`, `…/performance`, `POST …/performance/compare` |
| Conformance | `GET/POST …/conformance-rules`, `GET/PUT/DELETE …/conformance-rules/{rid}`, `GET …/conformance-rules/{rid}/evaluation`, `GET …/conformance/summary` |
| Alerts | `GET/POST …/alert-rules`, `PUT/DELETE …/alert-rules/{rid}`, `POST …/alert-rules/evaluate`, `GET /api/alerts`, `GET /api/alerts/counts`, `PATCH /api/alerts/{id}` |
| Exports | `GET …/exports/{kind}.{fmt}`, `GET …/exports/events.xes` |

Errors always look like `{"error": {"code", "message", "details"}}` with meaningful status
codes (404 missing, 409 conflicting state, 413 too large, 422 invalid input, 403 CSRF/origin).
State-changing requests need the `X-CSRF-Token` header (the pages send it automatically).

---

## Demo data

* `sample_data/p2p_demo_events.csv` — **synthetic** purchase-to-pay log: 600 cases, 5,304
  events, 12 activities, 39 variants, 8 departments, 25+ resources. Contains standard paths,
  approval delays (high-value orders, a slow approver, a slow AP clerk), price-change and
  invoice-correction rework loops, missing PR/PO approvals, cancellations, 83 open cases cut
  off at the extraction time, 30-day SLA violations and deliberate timestamp ties. Case IDs
  have leading zeros. Generated deterministically by `scripts/generate_demo_data.py` (a test
  verifies the committed file equals the generator output).
* `sample_data/p2p_validation_demo.csv` — 17 rows with intentionally malformed values for
  exercising the validation and quarantine flow (import it with source timezone
  `Europe/Berlin` to also see DST findings).
* “Load demo data” imports the log through the normal pipeline (source timezone
  Europe/Berlin, completion from the `case_status` column, fixed reference time
  2025-06-27 16:00 UTC), adds six example conformance rules and three alert rules, and runs
  the alert evaluation. All demo screens show a **SYNTHETIC DEMO DATA** badge.
* `tests/fixtures/hand_check.csv` — 16-event fixture with expected metrics worked out by hand
  in [tests/fixtures/README.md](tests/fixtures/README.md).

---

## Verification

### Automated (run for this release)

`python -m pytest` → **71 passed** (Python 3.11.6, macOS arm64), both in the development venv and in a
fresh venv created with `python3.11 -m venv` + `pip install -r requirements-dev.txt` (`pip check`: no broken
requirements). Coverage includes: valid and
invalid imports, identifier preservation, timestamp normalization and DST policies, tie
handling, variant counts, DFG edge occurrence/case counts (cross-checked against
`pm4py.discover_dfg`), rework, duration statistics, completion-status modes, preservation of
complete traces under case filtering, identical populations across endpoints and exports,
conformance including not-evaluable cases, alert deduplication and status preservation, CSRF/
Origin/Host protections, upload limits, quarantine, re-import versioning, deletion
confirmation, formula-safe CSV, XES export, persistence across restarts, interrupted-job
recovery and demo-data determinism. Edge cases: empty results, single-event cases, repeated
activities, equal timestamps, missing optional fields.

### Browser smoke test (performed with the built-in browser against `python -m app`)

Clean install → Datasets → **Load demo data** (job progress, 240 alerts created) → Overview
(600 cases, 5,304 events, 12 activities, 517 completed / 83 incomplete, rework 20 %, 39
variants, charts render, no console errors under the strict CSP) → Process explorer (dagre
layout, modes, thresholds) → Variants (select #1: 312 cases, path highlighted, case list) →
Cases (deep link to a case, timeline + conformance findings) → filters `department=IT` +
`rework=true` via the filter dialog (33 cases; identical in cases, graph, variants and CSV
export) → Performance (charts, cohort comparison) → Conformance (evaluate rule, 37 cases not
evaluable because of timestamp ties) → Alerts (acknowledge, re-evaluate: 0 new, status kept).
Import wizard: `p2p_validation_demo.csv` uploaded → mappings suggested → source timezone
Europe/Berlin → validation (17 rows, 11 invalid with per-code counts, tie warning) → quarantine
import (6 events, 11 rows quarantined). Server stopped and restarted: dataset, rules and the
acknowledged alert were still present. Phone width (375 px): no horizontal scrolling.
Restart persistence is also covered by `test_data_persists_across_restart`.

### Manual checklist

1. Start from a clean checkout (`rm -rf data`), install, `python -m app`.
2. Datasets → **Load demo data**; wait for “Demo data loaded”.
3. Overview: 600 cases, 5,304 events, 12 activities, 517 completed / 83 incomplete.
4. Process explorer: switch to *Elapsed time*, move the thresholds (KPIs elsewhere unchanged),
   click an edge → Cases page filtered by that transition.
5. Variants: click variant #1 → path highlighted, case list; open a case.
6. Open *Filters*, choose department *IT* and rework *yes*; check the same case count on
   Overview, Process, Variants and Cases.
7. Conformance → *Evaluate* “Approve before sending”; inspect violating and not-evaluable cases.
8. Alerts → *New alert rule* (e.g. rework) → *Evaluate rules now* → *Acknowledge* one alert.
9. Cases → *Cases CSV* (filtered export); open it in a spreadsheet.
10. Stop the server (Ctrl+C), start it again, reload: dataset, rules and alert statuses remain.
11. Import → upload `sample_data/p2p_validation_demo.csv` → review findings → confirm with
    *quarantine* → Datasets → *Findings* / *Quarantined rows CSV*.

### Measured performance

`python scripts/benchmark.py` (synthetic P2P log, **103,608 events / 11,500 cases**, Python
3.11.6, macOS 26.5 arm64, 10 logical CPUs, SQLite on local SSD, in-process client):

| Operation | Time |
|---|---|
| upload + preview | 0.16 s |
| validate all rows | 1.26 s |
| import job (validate + write + alert evaluation) | 4.72 s |
| first analysis after start (load events from DB) | 0.86 s |
| overview / variants / cases (cached) | ≤ 0.01 s |
| process graph | 0.14 s |
| performance | 0.19 s |
| filtered overview (activity + department) | 0.03 s |
| conformance evaluation (precedence) | 0.17 s |
| alert evaluation, 3 rules (first / re-run) | 0.29 s / 0.18 s |
| events CSV export (all 103k events) | 2.52 s |

These are single measurements on one machine, not a guarantee. Enterprise-scale logs (tens
of millions of events) are out of scope for this architecture: the event log of the active
dataset is held in memory as a pandas DataFrame.

---

## Deployment boundaries and security

The default deployment is **single-user and local-only**. Implemented protections:

* Bound to `127.0.0.1`; launcher refuses other interfaces without an explicit override.
* Host-header allow-list (DNS-rebinding protection); no CORS (cross-origin reads blocked).
* CSRF: state-changing `/api` requests require an HMAC-derived `X-CSRF-Token`; requests with
  a foreign `Origin` are rejected.
* Strict Content-Security-Policy (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`,
  `no-referrer`.
* Upload size, row, column and field-length limits; extension and binary checks; uploads
  stored under server-generated UUID names only.
* All database access through SQLAlchemy with bound parameters.
* Frontend renders data with `textContent`/DOM APIs, never `innerHTML`; Plotly labels escaped.
* CSV export formula-injection protection.
* Secrets from environment variables; logs contain IDs and counts, not business data values.
* Dataset deletion requires typing the dataset name (UI) / `confirm_name` (API).

**Do not expose this application publicly or on a shared network.** A shared deployment
requires, at minimum: authentication (e.g. OIDC/SSO), authorization and role-based access,
tenant/workspace isolation of datasets, TLS termination, a production database (PostgreSQL)
with backups, a multi-process-safe job queue, rate limiting, audit logging, secret management,
dependency and container scanning, and an operational monitoring/patching process.

### Background jobs

Imports run in a single worker thread inside the web process (`app/services/jobs.py`). They
are not distributed and do not survive a restart. Because each import commits in one
transaction, an interrupted import leaves no partial data; on start-up unfinished jobs are
marked **interrupted** with an explanation, and the user re-runs the import. Running several
server processes against one database is not supported.

### PostgreSQL

Set `PROCESSLENS_DATABASE_URL=postgresql+psycopg://…` and `pip install "psycopg[binary]"`.
Models and migrations avoid SQLite-specific types, but **PostgreSQL is not covered by the test
suite** in this release.

### Third-party licenses

Python dependencies are permissively licensed **except PM4Py, which is AGPL-3.0** (PM4Py's own
start-up banner points out that commercial use without open-sourcing requires a commercial
license). PM4Py is only imported lazily for the XES export and in tests; review the license
before distributing ProcessLens. Vendored JS libraries are MIT-licensed (see
`app/static/vendor/README.md`).

---

## Implemented features (MVP)

* CSV import wizard with preview, mapping suggestions, encoding/delimiter overrides, timestamp
  formats, source/display timezones, explicit DST policies, full validation report, reject or
  quarantine policies, background jobs with progress, re-import as new dataset version,
  persisted source metadata.
* Downloadable demo and validation-demo files; one-click synthetic demo with example rules.
* Overview dashboard, directly-follows process explorer, variant explorer, case explorer,
  performance analysis with cohort comparison, rule-based conformance, in-app alerts.
* Consistent case-level filters across all views and exports; URL-persisted filter chips.
* CSV/JSON/XES exports; validation report and quarantine downloads.
* SQLite persistence with Alembic migrations; caching keyed by dataset version, settings
  revision, rule revision and normalized filters.
* Local-deployment security controls listed above; Docker image and compose file.

## Known limitations

* Case-centric only; one case ID per event.
* The directly-follows graph is not a process model (no BPMN/Petri net, no concurrency).
* No event-level filtering mode (by design, to keep traces intact).
* Cohort comparison is descriptive: no statistical significance tests or causal inference.
* Rule-based conformance only; no token replay or alignments.
* Alerts are evaluated on demand and after imports; no scheduler, no notifications.
* In-memory analytics for the selected dataset; tested to ~100k events, not millions.
* Single process, single user, no authentication; PostgreSQL untested.
* The Docker image could not be built in the development environment used for this release
  (Docker was not installed); the Dockerfile follows the same steps as the tested local
  install but has not been executed.
* Variants page lists variants in pages of 25 (“Show more”); very long sequences wrap.

## Roadmap (not implemented)

* **Object-centric process mining**: objects, events and many-to-many event–object relations,
  OCEL 2.0 import, object-type perspectives. The current schema keeps cases and events in
  dedicated tables so object tables can be added alongside rather than overloading `case_id`.
* **Formal process discovery and alignment-based conformance** (e.g. inductive miner, Petri
  nets/BPMN, fitness/precision).
* **Scheduled connectors and incremental ingestion**; ERP and CRM integrations.
* **External workflow automation** (tickets, messages) with explicit approval controls.
* **Multi-user workspaces**, authentication, role-based permissions, audit trail.
* **Predictive models** (remaining time, outcome) with proper training/validation/test splits
  and calibration.
* **Natural-language analytics** grounded in verified calculations from this API.
* **What-if simulation** with explicit, documented assumptions.
* **A safe analytical expression language** for custom KPIs and filters — parsed and
  evaluated in a sandboxed, whitelisted interpreter; arbitrary Python will never be executed.
