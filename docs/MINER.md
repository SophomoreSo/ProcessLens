# ProcessLens Miner

ProcessLens Miner is a separate program for **building** analyses on top of ProcessLens
datasets: a studio in which you define a knowledge model of KPIs and filters, assemble
dashboards from components, and explore the data in a query workbench. It is inspired by the
way commercial process-mining studios separate *data* from *analysis content*; like
ProcessLens it is an independent implementation, not affiliated with or compatible with any
commercial product.

```bash
python -m miner
```

Open <http://127.0.0.1:8100>. Miner reads the datasets of the ProcessLens installation
configured by the usual `PROCESSLENS_*` variables (it applies the ProcessLens migrations on
start, so it also works when ProcessLens has never run) and never writes to that database.
Its own workspace lives in `<data dir>/miner.db`. Import data and manage datasets in
ProcessLens (`python -m app`); both programs can run at the same time.

## Concepts

| Concept | What it is |
|---|---|
| **Package** | A workspace for one ProcessLens dataset seen through one **lead object type** (its objects are the "cases"). Holds a knowledge model, views and saved queries. Optional **base filter** applied everywhere in the package. |
| **KPI** | A named measure (an aggregation in PLQL), with a key, display format, optional target and direction. Referenced as `KPI("key")` — change it once, every view follows. |
| **Filter** | A named case condition in PLQL, referenced as `FILTER("key")`; toggled as a chip in views. |
| **View** | A dashboard: a 12-column grid of components (KPI tile, bar, line, donut, table, process graph, variants, text). Optional view filter; each component may add its own filter. |
| **Query** | Dimensions × measures under a filter; shown as a table or chart, saved, downloaded as CSV or added to a view. |

New packages can start with a **starter knowledge model** (cases, throughput, rework,
completion, variants, cost when available) and two views (*Process overview*, *Throughput
analysis*), adapted to what the dataset contains.

### Cross-filtering

In a view, clicking a bar, donut slice, table row, process-graph activity or variant adds a
**selection** (shown as a removable chip). Selections, active saved filters and the view
filter are combined with AND and applied to every component. Selections are not saved.

## PLQL — ProcessLens Query Language

PLQL is a small, safe expression language: formulas are tokenized and parsed into a syntax
tree and interpreted over pandas data (`miner/plql`). Nothing is ever passed to `eval`.
Errors name the position (`^` under the formula in the editors).

### Tables and levels

* `case.<column>` — one row per case (object of the lead type).
* `event.<column>` — one row per (case, event).

Every expression has a level: constant, case or event. Case values used next to event values
are repeated on each of the case's events. The editors' reference panel lists every column,
function, KPI, filter and activity name; click to insert.

| Case columns | |
|---|---|
| `id`, `start`, `end` | id, first and last event timestamp |
| `duration_s` / `_h` / `_d` | observed throughput time (last − first event) |
| `events`, `activities` | number of events / distinct activities |
| `first_activity`, `last_activity`, `variant`, `variant_id` | |
| `status` | `completed`, `incomplete`, `unknown` (the dataset's completion rule) |
| `rework`, `repeated` | any repeated activity / number of repeated occurrences |
| `cost` | sum of event costs (missing if no event has a cost) |
| `age_d` | age of a not-completed case at the reference time |

| Event columns | |
|---|---|
| `id`, `case_id`, `activity`, `ts`, `start_ts`, `resource`, `department`, `cost`, `status` | |
| `seq` | position in the case (0 = first) |
| `prev_activity`, `elapsed_h`, `elapsed_d` | previous activity and time since it |
| `exec_h` | execution time (needs a start timestamp) |

### Operators and functions

* Arithmetic `+ - * / %` (division by zero gives a missing value); comparison
  `= != < <= > >=`; `AND OR NOT`; `x IN (a, b)`, `x NOT IN (...)`.
  Timestamps compare with date text (`case.start >= "2024-03-01"`, read in the dataset's
  display timezone); subtracting two timestamps gives days. `x = NULL` tests for a missing
  value.
* **Row functions:** `IF`, `COALESCE`, `ISNULL`, `ROUND`, `ABS`, `BUCKET(x, size)`, `CONCAT`,
  `LOWER`, `UPPER`, `YEAR`, `QUARTER`, `MONTH`, `WEEK` (ISO), `DAY`, `WEEKDAY`, `HOUR` (all
  in the display timezone), `DAYS_BETWEEN`, `HOURS_BETWEEN`.
* **Case functions** (a value per case): `CONTAINS("A"[, "B"…])`, `COUNT_ACT("A")`,
  `THROUGHPUT("A", "B"[, unit])` (first A to the first B at or after it; unit `s`, `min`,
  `h`, `d`, `w`), and pull-ups of event values `CASE_COUNT([cond])`, `CASE_SUM`, `CASE_AVG`,
  `CASE_MIN`, `CASE_MAX`, `CASE_FIRST`, `CASE_LAST`, `CASE_COUNT_DISTINCT`.
* **Aggregations** (measures): `COUNT_CASES([cond])`, `COUNT_EVENTS([cond])`, `COUNT`,
  `COUNT_DISTINCT`, `SUM`, `AVG`, `MEDIAN`, `MIN`, `MAX`, `STDEV`, `PERCENTILE(x, p)` (linear
  interpolation), `SHARE(cond)`. Aggregations can be combined with arithmetic and row
  functions (`COUNT_CASES(case.duration_d <= 14) / COUNT_CASES()`), not nested.
* **References:** `KPI("key")`, `FILTER("key")` (expanded before evaluation; cycles and
  unknown keys are errors).

### Grouping semantics

* **No dimensions:** one value over all cases.
* **Case-level dimensions** (e.g. `case.status`, `MONTH(case.start)`): case values are
  aggregated per group; event values over all events of the group's cases.
* **An event-level dimension** (e.g. `event.activity`): event values are aggregated per
  group; case values over the **distinct** cases with at least one event in the group — a
  case that does `B` twice counts once for `B`. `COUNT_CASES(cond)` with an event condition
  counts the distinct cases with a matching event in the group.
* `SHARE(cond)` is the share of cases for a case condition and the share of events for an
  event condition.
* Missing values are skipped by aggregations; `SUM` of only missing values is missing.
* Default order: time-like dimensions (`YEAR`…`HOUR`, `BUCKET`) ascending, otherwise the
  first measure descending.

### Filters

Filters are **case-level**, as in ProcessLens: matching cases keep their complete event
histories. An event condition (`event.resource = "r2"`) keeps the cases with at least one
matching event. Filters combine with AND: package base filter → view filter → active saved
filters → selections → component filter.

### Examples

```text
SHARE(THROUGHPUT("Create Purchase Order", "Pay Invoice") <= 30)      -- paid within 30 days
AVG(IF(CONTAINS("Change Price"), case.duration_d, NULL))              -- throughput with price changes
COUNT_CASES(event.activity = "Request Invoice Correction") / KPI("cases")
CASE_FIRST(event.resource)                                           -- dimension: who started the case
BUCKET(case.duration_d, 5)                                           -- dimension: 5-day histogram
```

## Exchange format

**Settings → Export package** downloads `<name>.miner.json`: format
`processlens-miner-package`, version 1, with the KPIs, filters, views and saved queries — no
event data. **Workspace → Import package** creates a new package from such a file for a
dataset you choose.

## API

All endpoints are under `/api` (no OpenAPI UI). State-changing requests need the
`X-CSRF-Token` header from `GET /api/session` (the token differs from the ProcessLens tokens).

| Endpoint | |
|---|---|
| `GET /api/datasets`, `GET /api/reference` | ProcessLens datasets; PLQL catalog |
| `GET/POST /api/packages`, `GET/PATCH/DELETE /api/packages/{id}` | packages |
| `GET /api/packages/{id}/export`, `POST /api/packages/import` | exchange format |
| `GET /api/packages/{id}/schema` | dataset facts, activity/resource lists |
| `POST/PUT/DELETE …/kpis[/{kpi}]`, `…/filters[/{filter}]` | knowledge model (validated; referenced items cannot be deleted or re-keyed) |
| `GET …/kpi-values`, `GET …/filter-counts`, `POST …/evaluate` | values over all cases; test a formula |
| `POST …/query`, `GET/POST/PUT/DELETE …/queries[/{q}]` | run and save queries |
| `POST/GET/PUT/DELETE …/views[/{v}]`, `POST …/views/{v}/duplicate` | views |
| `POST …/data` | the data of one component under a list of PLQL conditions |

Errors: `{"error": {"code", "message", "details"}}`; PLQL errors use code `plql_error` with
`details.position` (the message starts with `Dimension n:` / `Measure n:` for queries).

## Configuration

`PROCESSLENS_MINER_HOST` (`127.0.0.1`), `PROCESSLENS_MINER_PORT` (`8100`, or `--port`),
`PROCESSLENS_MINER_DATABASE_URL` (default `sqlite:///<data dir>/miner.db`),
`PROCESSLENS_MINER_ALLOWED_HOSTS`, `PROCESSLENS_MINER_PROCESSLENS_URL` (link target, default
`http://127.0.0.1:8000`), `PROCESSLENS_MINER_ALLOW_NON_LOOPBACK_BIND`. Miner has no login and
can change its workspace, so — like the ProcessLens admin site — the launcher refuses a
non-loopback address unless explicitly allowed. It uses the same CSRF, Origin, Host and CSP
protections.

## Limitations

* English only (ProcessLens' language setting does not apply yet).
* One lead object type per package; no object-centric formulas across types and no
  object attributes beyond the status.
* Formulas are evaluated in memory per request on the cached event log (fine to ~100k
  events, like ProcessLens).
* No user accounts, sharing or version history of packages; selections are not saved.
* The Miner schema is created on start-up (no migrations yet; `miner_schema.version` = 1).
