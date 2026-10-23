# ProcessLens

ProcessLens is a local-first web application for **object-centric process mining and process
analysis**. A business analyst imports a CSV, XES or OCEL 2.0 event log, maps it to events and
objects of several types, validates it, and then explores the process from any object type's
point of view: KPIs, directly-follows graphs (per type and object-centric), variants, object
histories with their related objects, performance, rule-based conformance, cohort comparisons,
in-app alerts and exports. On top of that it offers process discovery with 14 algorithms plus
object-centric Petri nets, formal conformance checking (token replay, alignments, soundness,
declarative checks), an algorithm comparison workspace with case-level train/test splits, task
mining on exported user-interaction events, guided question-driven analysis, and a read-only
guest site.

> ProcessLens is an independent implementation with its own branding. It is inspired by the
> kind of analysis commercial process-mining suites offer, but it is **not affiliated with
> Celonis**, does not claim feature parity, and is not compatible with proprietary Celonis
> features or formats.

Documentation:

* **[docs/METRICS.md](docs/METRICS.md)** — definitions of every metric, filter and rule.
* **[docs/MINING.md](docs/MINING.md)** — discovery algorithms (support matrix, parameters,
  limitations, unavailable algorithms), model exports, formal conformance, comparison and
  mining runs.
* **[docs/OBJECT_CENTRIC.md](docs/OBJECT_CENTRIC.md)** — the object-centric data model: storage,
  lead object types (perspectives), counting rules, imports, object-centric views, export.
* **[docs/TASK_MINING.md](docs/TASK_MINING.md)** — task-event import, sessionization, task
  analyses, privacy.
* **[docs/GUIDED_ANALYSIS.md](docs/GUIDED_ANALYSIS.md)** — questions and answer structure.

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

To also share a **read-only guest site** with other people:

```bash
python -m app --guest
```

The admin site keeps running on <http://127.0.0.1:8000> (this computer only); the guest site
listens on port **8080** on all interfaces (`http://<your-computer's-address>:8080`). See
[Admin and guest sites](#admin-and-guest-sites).

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
`ALLOWED_HOSTS`, `GUEST_ENABLED` (false), `GUEST_HOST` (`0.0.0.0`), `GUEST_PORT` (8080),
`GUEST_ALLOWED_HOSTS` (`*`), `MINING_TIMEOUT_S` (300), `MINING_MAX_TIMEOUT_S` (3600),
`MINING_MAX_ALIGNMENT_VARIANTS` (2000).

The launcher (`python -m app`) **refuses to bind the admin site to a non-loopback address**
unless `PROCESSLENS_ALLOW_NON_LOOPBACK_BIND=true` is set (the Docker image sets it because the
admin port is published on loopback only).

### Admin and guest sites

ProcessLens serves two layouts of the same data from one process:

| | Admin site | Guest site |
|---|---|---|
| Who | you, on this computer | everyone else |
| Address | `127.0.0.1:8000` (loopback only) | `0.0.0.0:8080` by default (`--guest` or `GUEST_ENABLED=true`) |
| Access | read and write | **read only**, no login |
| Pages | all | all except *Import* and *Datasets*; no API docs |
| Can do | import, edit rules and alerts, run discovery/conformance/comparison, approve models, delete, change settings | browse every dataset, OCEL and task log, use filters, cases, variants, performance (incl. cohort comparison), guided analysis, view and export existing mining results, download exports |
| Settings | language/animations saved in the database | language/animations saved in the guest's own browser (cookies) |

The guest layout hides every control that would change data, and the server enforces it
independently: the guest site answers every `POST`/`PUT`/`PATCH`/`DELETE` with
`403 read_only` (the only accepted `POST` is the stateless cohort comparison), and staged
uploads and import jobs are not readable there. Both sites share the database, caches and
mining worker, so anything the admin does is visible to guests immediately.

**What guests can see:** everything in the database — all datasets, case ids, activities,
resources, departments, costs, attributes, OCEL objects and task events. Do not import data
that the people who can reach port 8080 must not see. The guest site has **no login, no TLS
and no rate limiting**: on an untrusted network or the internet, put it behind a firewall rule
or a reverse proxy that adds HTTPS (and, if needed, authentication). Guests can trigger
analyses that use CPU on your computer.

---

## Assumptions and architecture

* **Single user, single process, local machine.** No authentication; bound to localhost.
* **Object-centric datasets.** Every dataset is an OCEL-style log: events (stored once), objects
  of several types, event–object and object–object relationships, object attribute histories.
  Analyses use a **lead object type** whose objects play the role of cases; every page can
  switch it, and shared events are counted once (see [docs/OBJECT_CENTRIC.md](docs/OBJECT_CENTRIC.md)).
  Task-event logs are stored separately and link to objects by id.
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
   ocel · tasks · guide (question answering over the services above)
   mining runs (app/mining): registry → supervisor thread → isolated worker process (PM4Py)
   │
SQLAlchemy models (app/models) + Alembic migrations  →  SQLite (default) / PostgreSQL (configurable)
```

| Layer | Technology |
|---|---|
| API & pages | FastAPI 0.156, Starlette, Jinja2 templates, OpenAPI at `/docs` |
| Validation | Pydantic 2 |
| Persistence | SQLAlchemy 2.1, Alembic, SQLite (WAL) |
| Analysis | pandas 3.0, numpy; PM4Py 2.7.23 for discovery, formal conformance, OC Petri nets and XES export (run in a separate worker process), and as a cross-check of DFG discovery in the tests |
| Frontend | HTML5, CSS3, vanilla ES modules, Cytoscape.js 3.30 (+ dagre layout), Plotly.js 2.35 (basic bundle) — vendored in `app/static/vendor`, no CDN at runtime |
| Tests | pytest (176 tests) |

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
  models/            Upload, Dataset, Event (legacy_events, pre-0007), ImportFinding, QuarantinedRow, ImportJob,
                     ConformanceRule, AlertRule, Alert, UserPreferences, MiningRun,
                     MiningArtifact, ReferenceModel, Ocel* (log, events, objects, attributes,
                     E2O, O2O), TaskLog, TaskEvent
  schemas/           Pydantic request/response models, FilterSpec
  api/               imports, datasets, analytics, conformance, alerts, exports, meta,
                     preferences, mining, ocel, tasks, guide, pages
  services/          ingestion, validation, importer, jobs, eventlog, filtering, analytics,
                     conformance, alerts, exports, cache, demo, preferences, ocel, ocel_store
                     (the single OCEL writer of every import), ocel_import, ocel_export, xes,
                     tasks, guide
  mining/            registry (algorithm adapters), tasks (PM4Py calls), serialize (model
                     JSON), worker (isolated process), runs (snapshots, cache, supervisor)
  i18n/              translation catalogs (en, ko, pl)
  templates/         Jinja2 pages (base + one per page)
  static/css|js|img  styles and page modules
  static/vendor/     Cytoscape.js, dagre, cytoscape-dagre, Plotly.js (MIT, see README there)
migrations/          Alembic environment + versions
sample_data/         synthetic P2P demo log, validation-demo file, OCEL demo, task-event demo
scripts/             generate_demo_data.py, generate_ocel_demo.py, generate_task_demo.py,
                     benchmark.py, i18n_merge.py
tests/               pytest suite + hand-checkable fixtures (tests/fixtures/README.md)
docs/                METRICS.md, MINING.md, OBJECT_CENTRIC.md, TASK_MINING.md, GUIDED_ANALYSIS.md
Dockerfile, compose.yaml, requirements*.txt, .env.example, pyproject.toml
```

---

## Using ProcessLens

### Import wizard (Import page)

1. **Upload** a CSV (`.csv`, `.txt`, `.tsv`), an **XES** event log (`.xes`, `.xes.gz`) or an
   **OCEL 2.0 JSON** log (`.json`, `.jsonocel`). The file gets a server-generated name; size,
   row, column and field-length limits are enforced; binary files are refused. See
   [XES import](#xes-import); OCEL files are validated on upload and then only need a main object
   type and a field mapping (steps 3–4 are skipped) — see [docs/OBJECT_CENTRIC.md](docs/OBJECT_CENTRIC.md).
2. **Preview & mapping**: sample rows, detected encoding/delimiter (overridable), suggested
   column mapping. Required: `case_id`, `activity`, `timestamp`. Optional: `event_id`,
   `resource`, `department`, `cost`, `start_timestamp`, `case_status`. **Objects:** name the
   object type of the case column (default `case`) and give every other column a role — event
   attribute, attribute of the case object (kept as attribute history), related objects of a
   type you name (with an optional separator for multi-valued cells), or not imported. All
   values are read as **text** (leading zeros survive).
3. **Timestamps**: format (`ISO8601`, presets, custom strftime, Unix s/ms — with the share of
   sampled values each candidate parses), source timezone, display timezone, and an explicit
   policy for **ambiguous** (DST fall-back: reject / earliest / latest) and **nonexistent**
   (DST spring-forward: reject / shift forward / shift backward) local times.
4. **Validation findings**: every row is validated — missing case IDs, empty activities,
   missing/malformed timestamps, DST problems, duplicate event IDs (all rows sharing an ID are
   flagged), invalid or non-finite costs, start later than completion, wrong field counts, and
   object ids used with two object types. Warnings cover timestamp ties, negative costs, trimmed
   whitespace; notes cover empty and possibly multi-valued object cells. Download the full report
   as CSV or JSON.
5. **Confirm**: new dataset or new version of an existing one, invalid-row policy —
   **reject the import** if any row is invalid, or **quarantine** invalid rows (stored with
   their reasons, excluded from analysis, downloadable) — completion rule and reference time.

### XES import

XES files (IEEE 1849, also gzip-compressed) are converted on upload into the same table the CSV
wizard uses — one row per event — and imported as **object-centric** data: each trace becomes
an object (of a type you name), and mapping, timestamp options, validation, quarantine and
versioning work exactly as for CSV. The conversion streams the XML (100,000 events in about
1 s on the benchmark machine).

* **Columns:** event attributes keep their XES keys (`concept:name`, `time:timestamp`,
  `org:resource`, `org:group`, `cost:total`, `identity:id`, …); trace attributes are prefixed
  with `case:` (`case:concept:name` identifies the trace object). The standard keys are mapped
  automatically, trace attributes are suggested as attributes of the trace object, and event
  attributes such as `org:resource` can be turned into **objects** (role *related objects*), so
  events are shared between traces and those objects.
* **Lifecycle:** *one row per completed activity* pairs each `complete` event with the earliest
  unmatched `start` event of the same activity (and `concept:instance`, if present) in the same
  trace and fills `start_timestamp`; other transitions and unmatched starts are left out and
  counted. *One row per event* keeps every event and the `lifecycle:transition` column.
  Pairing is preselected only when the log contains `start` events; the choice can be changed
  in step 2 (*Convert again*) and is recorded with the dataset (`import_options.xes_conversion`).
* **Reported, not imported:** list and container attributes, meta-attributes, log-level
  attributes, events outside traces, empty traces; global default values are not filled in and
  classifiers are not applied (the activity is the mapped column). Traces without
  `concept:name` produce rows without a case id, which validation rejects.
* **Timestamps** keep their offsets; values without an offset use the source timezone chosen
  in step 3.
* **Safety:** documents with a DTD (`<!DOCTYPE`/`<!ENTITY`) are refused, and the size limit
  applies to the *uncompressed* XML of `.xes.gz` files.

The XES **export** (`…/exports/events.xes`) writes the lead object type (trace = object) and omits
missing resources, departments and costs, so an exported log imports again unchanged (tested).

The import runs as a background job with progress; all data is written in **one transaction**
so a failed or interrupted import never leaves partial data. Each successful import
increments the dataset **version**, invalidates cached analytics and evaluates alert rules.

### Pages

| Page | What it shows |
|---|---|
| **Guided analysis** | Predefined questions (longest transitions, rework variants, SLA violations, department differences, object relationships and delays) answered with metric definition, dataset version, filters, sample size, chart, table and links. |
| **Top bar** | Dataset selector and **Objects** selector (lead object type, kept in the URL across pages) with the type's objects and unique events. |
| **Overview** | Object/unique-event/activity counts (object-event rows when events are shared), an **object model** panel with every object type, completed vs incomplete/unknown, observed duration, completed throughput and open-case age (each with its population and n), rework rate, variant count, daily case starts, duration distribution, top variants, bottleneck candidates. |
| **Process explorer** | Switch between the lead type's graph and an **object-centric DFG** (all or selected types, one colour per type). Directly-follows graph with synthetic start/end markers, frequency or elapsed-time mode (median/p90/mean), edge occurrences and distinct cases in tooltips, zoom/pan/fit/reset, render-only node/edge thresholds, legend, click-through to matching cases, transitions CSV and XES export. |
| **Variants** | Sequences with case count, share, cumulative coverage, median/p90 observed duration and rework markers. Selecting a variant highlights its path and lists its cases; it can be applied as a global filter. |
| **Objects** | Paginated, sortable table of the lead type's objects; timeline with timestamps (display timezone), resources, departments, costs, execution times, gaps, tie markers, attributes, conformance findings and the **other objects of each event**; attribute history and related objects (O2O and shared events), each linking to its own perspective. |
| **Performance** | Transitions ranked by median/p90 elapsed time; resource and department comparisons with sample sizes; activity execution durations (when start timestamps exist); two-cohort comparison. All labeled as descriptive associations. |
| **Conformance** | Rules belong to an object type (created for the lead type). Rule CRUD (required, forbidden, A precedes B, max duration, max occurrences, start/end activity), compliant/violating/not-evaluable counts, case-level explanations, “use as filter”, CSV export. |
| **Alerts** | Alert rules (duration threshold, rework, conformance violation), “Evaluate rules now”, alert list with open/acknowledged/resolved workflow. |
| **Model discovery** | Algorithms run on the lead type; the **object-centric Petri net** uses all or selected types. Algorithm picker generated from the registry (parameters, defaults, preconditions, limitations, availability), background discovery runs with progress and cancel, native model views (process tree, Petri net, heuristics net, BPMN, POWL, transition system, DECLARE, log skeleton, temporal profile), exports (PTML, PNML, BPMN, JSON, PNG), provenance, formal conformance on the discovered or an approved/uploaded reference model with case diagnostics, run history. |
| **Algorithm comparison** | Up to 8 algorithms on one population with an optional case-level train/test split; runtime, representation, size, fitness/precision, soundness, declarative fit, parameters and warnings side by side (no combined score); two models side by side; CSV/JSON export. |
| **Object relationships** | Object-type graph (events shared between types, distinct object pairs, O2O qualifiers) and lifetimes of the lead type's objects by number of related objects. |
| **Task mining** | Task-event CSV import (privacy checks), configurable sessionization, task variants, repeated task sequences, application switches, task DFG, links to business cases. |
| **Datasets** | Load demo data, dataset list with object types, versions and source metadata, analysis settings per object type (completion rule, reference time) and display timezone, the main object type, import findings and quarantine downloads, re-import, deletion with typed-name confirmation. |
| **Settings** | Language (English, 한국어, Polski) and animations (follow system / on / off); stored in the database. |

The **filter bar** (top of every analysis page) applies filters to the lead type's objects — case-start date
range, activity presence/absence, resource, department, variant, duration, rework, completion
status, conformance result, transition — shown as removable chips and kept in the URL.

### Settings: language and animations

The **Settings** page (`/settings`, or `GET/PUT /api/preferences`) stores two user preferences in the
`user_preferences` table, so they survive restarts:

* **Language** — English, Korean (한국어) or Polish (Polski). Pages are rendered server-side in the
  selected language; numbers, dates and chart axes use the matching locale (`en-US`, `ko-KR`, `pl-PL`).
  Server-generated texts (validation findings, conformance explanations, rule descriptions, alert
  messages, job errors, API error messages, notes) are stored as a message key plus parameters and
  rendered in the language of each request, so alerts created earlier also appear translated. API
  clients may override the language per request with the `X-ProcessLens-Language: en|ko|pl` header.
  Data values such as activity names, case IDs and user-entered rule names are never translated.
  Catalogs live in `app/i18n/locales/{en,ko,pl}.json`; a test enforces that all three have the same
  keys and placeholders. Polish texts use “label: value” phrasing for numbers to avoid incorrect
  plural forms.
* **Animations** — *follow system* (default: animate unless the OS requests reduced motion), *on* or
  *off*. Animations include staggered page/card entrances, KPI count-up, chart bars growing in and
  animated transitions, animated graph zoom/fit/reset and layout, smooth path highlighting, an
  optional **“Animate flow”** effect that moves dashes along directly-follows edges, and dialog/toast/
  badge transitions. When animations are off, a global rule disables every CSS animation and
  transition and the JavaScript effects are skipped.

Translations were written for this release and have not yet been reviewed by native speakers.
Rows imported or alerts created before migration `0002` show their original English text until they
are re-imported or re-evaluated.

### Exports

`GET /api/datasets/{id}/exports/{kind}.{csv|json}` for `cases`, `events`, `variants`,
`transitions`, `conformance` (with `rule_id`), `alerts`, plus `summary.json` and `events.xes`
(via PM4Py). Every export accepts the same filter parameters and lead object type
(`object_type`) and therefore contains exactly the population shown on screen; event exports
carry `ocel_event_id`. `…/exports/ocel.json` writes the **whole dataset as OCEL 2.0 JSON** (all
object types, relationships and attribute histories; re-importable). Text cells beginning with `= + - @`, tab or CR are prefixed
with `'` in CSV to prevent spreadsheet formula injection.

### API

Interactive OpenAPI documentation: <http://127.0.0.1:8000/docs>. Main routes:

| Area | Routes |
|---|---|
| Health/meta | `GET /api/health`, `GET /api/session` (CSRF token), `GET /api/meta/timezones` |
| Import | `POST /api/uploads` (CSV, XES, OCEL JSON), `GET /api/uploads/{id}`, `POST /api/uploads/{id}/validate`, `GET /api/uploads/{id}/validation-report`, `POST /api/uploads/{id}/import`, `POST /api/uploads/{id}/xes-options`, `POST /api/uploads/{id}/import-ocel`, `GET /api/jobs/{id}`, `POST /api/demo/load`, `GET /api/sample-data/{name}` |
| Datasets | `GET /api/datasets`, `GET/PATCH/DELETE /api/datasets/{id}` (`DELETE` needs `confirm_name`), `GET …/validation-report`, `GET …/quarantine` |
| Analytics (all take `object_type`) | `GET …/overview`, `…/filter-options`, `…/graph`, `…/variants`, `…/cases`, `…/cases/{case_id}`, `…/performance`, `POST …/performance/compare` |
| Conformance | `GET/POST …/conformance-rules`, `GET/PUT/DELETE …/conformance-rules/{rid}`, `GET …/conformance-rules/{rid}/evaluation`, `GET …/conformance/summary` |
| Alerts | `GET/POST …/alert-rules`, `PUT/DELETE …/alert-rules/{rid}`, `POST …/alert-rules/evaluate`, `GET /api/alerts`, `GET /api/alerts/counts`, `PATCH /api/alerts/{id}` |
| Exports | `GET …/exports/{kind}.{fmt}`, `GET …/exports/events.xes`, `GET …/exports/ocel.json` |
| Preferences | `GET/PUT /api/preferences` |
| Mining | `GET /api/mining/algorithms`, `POST …/mining/discover`, `POST …/mining/conformance`, `POST …/mining/compare`, `GET /api/mining/runs`, `GET /api/mining/runs/{id}` (+ `/cancel`, `/model`, `/cases`, `/export/{fmt}`), `GET/POST …/reference-models`, `POST …/reference-models/upload` — see [docs/MINING.md](docs/MINING.md) |
| Object-centric | `GET /api/datasets/{id}/ocel` (object model), `…/ocel/stats`, `…/ocel/ocdfg`, `…/ocel/type-graph`, `…/ocel/objects`, `…/ocel/objects/{oid}`, `…/ocel/relationship-delays`, `POST …/ocel/ocpn` — see [docs/OBJECT_CENTRIC.md](docs/OBJECT_CENTRIC.md) |
| Task mining | `GET /api/tasks`, `POST /api/tasks/preview`, `POST /api/tasks/upload`, `POST /api/tasks/demo`, `GET/DELETE /api/tasks/{id}`, `PUT …/link`, `GET …/summary`, `…/variants`, `…/sequences`, `…/switches`, `…/dfg`, `…/cases` |
| Guided analysis | `GET /api/guide/questions`, `GET /api/guide/answer` |

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
* “Load demo data” imports the log through the normal pipeline as an object-centric dataset —
  objects of type `purchase order` (the case column, with amount and category as attribute
  history) related to `vendor` objects (source timezone Europe/Berlin, completion from the
  `case_status` column, fixed reference time 2025-06-27 16:00 UTC) — adds six example
  conformance rules and three alert rules on purchase orders, runs the alert evaluation, and
  also imports the OCEL order-to-delivery log below as a second dataset. All demo screens show a
  **SYNTHETIC DEMO DATA** badge.
* `sample_data/ocel_demo.json` — **synthetic** OCEL 2.0 order-to-delivery log (customers,
  orders, items, packages; 527 events, 261 objects, shared events, changing order status and
  price), from `scripts/generate_ocel_demo.py`; loaded by *Load demo data* (main type `order`).
* `sample_data/task_events_demo.csv` — **synthetic** task events of six pseudonymous
  accounts-payable clerks (3,968 events) linked to P2P demo case ids, from
  `scripts/generate_task_demo.py`; *Task mining → Load demo*.
* `tests/fixtures/` — hand-checkable fixtures with expected values in
  [tests/fixtures/README.md](tests/fixtures/README.md): `hand_check.csv` (16 events),
  `mining_fixture.csv` (22 cases, fitting and deviating behaviour), `ocel_small.json`
  (shared events), `lifecycle.xes` (XES lifecycle and attributes), `task_events_small.csv`
  (sessionization).

---

## Verification

### Automated (run for this release)

**Object-centric release:** `python -m pytest` → **176 passed**. Every earlier test passes
unchanged on the object-centric storage (single-type datasets give the same numbers as the
case-centric model did). New and rewritten: `test_ocel.py` (14: shared events, perspectives,
unique-event counts and cost, OC-DFG, type graph, object timelines, status history, rules per
type, per-type settings and main-type change, OCEL validation, OC Petri nets, OCEL export round
trips), `test_object_import.py` (4: CSV object columns, multi-valued cells, type conflicts, XES
trace and attribute objects), `test_migration.py` (1: upgrade of a 0006 database with
case-centric data and an OCEL log, backup file, identical KPIs). The real local database
(revision 0002, P2P demo) was upgraded on a copy: 600 cases, 5,304 events, 39 variants and
517/83 completed/incomplete before and after. Checked live with `python -m app --guest`: demo
load (two datasets), Objects selector on every page, overview object model, object-centric DFG,
object timelines with navigation across types, Object relationships page, OC Petri net from
Model discovery, conformance on another type (rules shown as belonging to purchase orders), CSV
import with object columns and OCEL import through the wizard, and the guest layout.

**XES import:** `python -m pytest` → **167 passed**, including `test_xes.py` (5): conversion and
suggested mapping, lifecycle pairing vs one row per event, switching and importing with
identifiers, timestamps, start times, costs and case attributes intact, `.xes.gz`, DTD
refusal and malformed files, traces without a case id, and an export → import round trip with
identical KPIs. Checked live in the wizard with an XES export of the demo log (600 traces,
5,304 events) in both lifecycle modes.

**Admin/guest release:** `python -m pytest` → **162 passed**, including `test_guest.py` (16):
guest pages and missing admin pages/controls, guest reads of admin data, refusal of every kind
of write with `read_only`, separate CSRF tokens per site, blocked staged uploads, guest language in cookies without touching
the admin preference, and the launcher keeping the admin site on loopback. Checked live with
`python -m app --guest`: both sites up, guest conformance/discovery/alerts/OCEL pages without
write controls, a model discovered on the admin site opened on the guest site, guest language
switched to Polish while the admin site stayed English.

**Mining, object-centric, task-mining and guided-analysis release:** `python -m pytest` →
**146 passed** (Python 3.11.6, PM4Py 2.7.23.11, macOS arm64): 87 earlier tests plus
`test_mining.py` (32), `test_ocel.py` (10), `test_tasks.py` (8), `test_guide.py` (8) and a determinism check of the OCEL and task demo generators; the
translation tests cover the new pages and keys in all three languages. See the *Tests*
section of each document in `docs/` for what they assert. No new dependencies were added.

Browser check of this release (built-in browser against `python -m app` with a scratch data
directory): Model discovery with IMf (process tree, Petri net and BPMN tabs, provenance,
alignments fitness 0.895 / precision 0.856, token replay 0.952, sound, deviating-case
diagnostics); Algorithm comparison with a 409/191 train/test split (Alpha alignments shown as
*failed*, unavailable algorithms disabled); Object-centric demo (KPIs, OC-DFG coloured by
object type, object timeline with navigation to related objects, OC Petri net with variable
arcs); Task mining demo (609 sessions, variants, task DFG, 480 cases matched in the P2P demo
dataset with links to the case explorer); Guided analysis SLA question (184 of 517 evaluable
cases over the demo's 720 h rule, histogram with threshold).

**Earlier release:** `python -m pytest` → **87 passed** (Python 3.11.6, macOS arm64). The original 71 tests also passed in a
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

Settings (second round): Settings → 한국어 → Save reloaded every page in Korean (Overview KPIs,
Korean month names on chart axes, durations such as “28일”); Polish was checked on every page
(Overview, Process explorer, Variants, Cases, Performance with cohort comparison, Conformance
evaluation, Alerts re-evaluation, Datasets findings) with no untranslated keys and no error notices.
With animations on, the flow animation advanced the edge dash offset; with animations off,
`document.getAnimations()` was empty, edges were solid and the flow toggle was disabled. An existing
database was upgraded from migration 0001 to 0002 without data changes.

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

* Admin site bound to `127.0.0.1`; the launcher refuses other interfaces without an explicit
  override. The optional guest site is read-only, enforced by server middleware (not only by
  the layout), and has no API documentation endpoints.
* Host-header allow-list (DNS-rebinding protection); no CORS (cross-origin reads blocked).
* CSRF: state-changing `/api` requests require an HMAC-derived `X-CSRF-Token` (the guest site
  uses a different token, so guest pages never reveal the admin token); requests with a foreign
  `Origin` are rejected.
* Strict Content-Security-Policy (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`,
  `no-referrer`.
* Upload size, row, column and field-length limits; extension and binary checks; uploads
  stored under server-generated UUID names only.
* All database access through SQLAlchemy with bound parameters.
* Frontend renders data with `textContent`/DOM APIs, never `innerHTML`; Plotly labels escaped.
* CSV export formula-injection protection.
* Secrets from environment variables; logs contain IDs and counts, not business data values.
* Dataset deletion requires typing the dataset name (UI) / `confirm_name` (API).

**Never expose the admin site** (port 8000) beyond this computer. The read-only guest site is
the supported way to let others look (see [Admin and guest sites](#admin-and-guest-sites)). A
multi-user deployment with editing rights for several people
requires, at minimum: authentication (e.g. OIDC/SSO), authorization and role-based access,
tenant/workspace isolation of datasets, TLS termination, a production database (PostgreSQL)
with backups, a multi-process-safe job queue, rate limiting, audit logging, secret management,
dependency and container scanning, and an operational monitoring/patching process.

### Background jobs

Mining runs (discovery, conformance, comparison, model import, OC Petri nets) are separate:
they are persisted in `mining_runs`, executed one at a time by a supervisor thread in a
**separate worker process** that can be killed for cancellation and timeouts, cached by their
inputs, and marked *interrupted* if the server stops while they are queued or running. See
[docs/MINING.md](docs/MINING.md).

Imports run in a single worker thread inside the web process (`app/services/jobs.py`). They
are not distributed and do not survive a restart. Because each import commits in one
transaction, an interrupted import leaves no partial data; on start-up unfinished jobs are
marked **interrupted** with an explanation, and the user re-runs the import. Running several
server processes against one database is not supported.

### Database migrations

| Revision | Content |
|---|---|
| `0001` | initial schema (uploads, datasets, events, findings, quarantine, jobs, rules, alerts) |
| `0002` | user preferences, translatable message keys |
| `0003` | mining runs, run artifacts, reference models |
| `0004` | object-centric logs: events, objects, object attributes, E2O and O2O relationships |
| `0005` | task-event logs and task events |
| `0006` | uploads remember their source format, original XES file and conversion summary |
| `0007` | **object-centric datasets**: standard event fields on OCEL events, object types on rules, runs and models, per-type settings; converts every case-centric dataset (type `case`) and standalone OCEL log; keeps `legacy_events`; SQLite copied to `*.bak-0007` first |

Migrations run automatically on start (`alembic upgrade head`); existing databases are upgraded
in place without data changes.

### PostgreSQL

Set `PROCESSLENS_DATABASE_URL=postgresql+psycopg://…` and `pip install "psycopg[binary]"`.
Models and migrations avoid SQLite-specific types, but **PostgreSQL is not covered by the test
suite** in this release.

### Third-party licenses

Python dependencies are permissively licensed **except PM4Py, which is AGPL-3.0** (PM4Py's own
start-up banner points out that commercial use without open-sourcing requires a commercial
license). PM4Py is used at runtime for discovery, formal conformance, OC Petri nets (in the
mining worker process) and the XES export; review the license before distributing ProcessLens. Vendored JS libraries are MIT-licensed (see
`app/static/vendor/README.md`).

---

## Implemented features (MVP)

* **Object-centric data model** throughout: datasets of events and objects of several types
  with E2O/O2O relationships and attribute histories; every analysis per lead object type with
  shared events counted once; object-centric DFG, object explorer with related objects,
  object-type graph, OC Petri nets; OCEL 2.0 JSON export.
* Import wizard for CSV (object columns, case attributes), XES (trace and attribute objects,
  lifecycle pairing) and OCEL 2.0 JSON, with preview, mapping suggestions, encoding/delimiter overrides, timestamp
  formats, source/display timezones, explicit DST policies, full validation report, reject or
  quarantine policies, background jobs with progress, re-import as new dataset version,
  persisted source metadata.
* Downloadable demo and validation-demo files; one-click synthetic demo with example rules.
* Overview dashboard, directly-follows process explorer, variant explorer, case explorer,
  performance analysis with cohort comparison, rule-based conformance, in-app alerts.
* Consistent filters on the lead type's objects across all views and exports; URL-persisted filter
  chips and perspective.
* CSV/JSON/XES exports; validation report and quarantine downloads.
* SQLite persistence with Alembic migrations; caching keyed by dataset version, settings
  revision, rule revision and normalized filters.
* Local-deployment security controls listed above; Docker image and compose file.
* User settings: interface language (English, Korean, Polish) with localized server messages, and
  animations (follow system / on / off).
* Process discovery with an extensible algorithm registry (IM, IMf, IMd, Heuristics,
  Heuristics++, Alpha, Alpha+, POWL, DECLARE, Log Skeleton, transition systems, temporal
  profiles, ILP, Genetic; Split Miner and Fuzzy Miner listed as unavailable with reasons),
  native model views and PTML/PNML/BPMN/JSON/PNG exports.
* Formal conformance: token replay, alignments, soundness, DECLARE, log-skeleton and
  temporal-profile checks with explicit statuses, precision, case diagnostics, execution
  limits; discovered vs approved/uploaded reference models.
* Algorithm comparison with reproducible case-level train/test splits and exports.
* Background mining runs with cancellation, timeouts, persistent status, history, caching
  and honest progress.
* Task mining on exported interaction events, guided question-driven analysis, and a read-only
  guest site.

## Known limitations

* Conformance rules, alignments and discovery algorithms other than the OC Petri net work on one
  lead object type at a time (no object-centric conformance checking); OCEL import is JSON only;
  filters cannot combine conditions on several object types; object attribute values are text.
* The directly-follows graph on the Process explorer is not a process model; use Model
  discovery for Petri nets, BPMN and other models.
* Split Miner and Fuzzy Miner are not available; SVG export is not available (PNG only).
* Mining runs execute one at a time; very large logs can hit the alignment variant limit or
  the run timeout, which is reported explicitly.
* No event-level filtering mode (by design, to keep traces intact).
* Cohort comparison is descriptive: no statistical significance tests or causal inference.
* Alerts are evaluated on demand and after imports; no scheduler, no notifications.
* In-memory analytics for the selected dataset; tested to ~100k events, not millions.
* Single process, single editing user, no authentication; the guest site is read-only but
  open to anyone who can reach it. PostgreSQL untested.
* The Docker image could not be built in the development environment used for this release
  (Docker was not installed); the Dockerfile follows the same steps as the tested local
  install but has not been executed.
* Variants page lists variants in pages of 25 (“Show more”); very long sequences wrap.

## Roadmap (not implemented)

* **Object-centric conformance checking** and OCEL XML/SQLite import.
* **Split Miner / Fuzzy Miner** once a verified implementation can be integrated.
* **Scheduled connectors and incremental ingestion**; ERP and CRM integrations.
* **External workflow automation** (tickets, messages) with explicit approval controls.
* **Multi-user workspaces**, authentication, role-based permissions, audit trail.
* **Predictive models** (remaining time, outcome) with proper training/validation/test splits
  and calibration.
* **Natural-language analytics** grounded in verified calculations from this API.
* **What-if simulation** with explicit, documented assumptions.
* **A safe analytical expression language** for custom KPIs and filters — parsed and
  evaluated in a sandboxed, whitelisted interpreter; arbitrary Python will never be executed.
