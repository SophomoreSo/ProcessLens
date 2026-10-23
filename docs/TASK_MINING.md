# Task mining (user-interaction events)

Page: **Task mining** (`/tasks`). Tables: `task_logs`, `task_events` (migration `0005`).

ProcessLens analyses user-interaction events that **another tool exported**. It does not
record screens, keystrokes, clipboard contents or window titles, and it has no agent or
browser extension that could.

## Import

1. `POST /api/tasks/preview` (multipart `file`) returns the columns, a suggested mapping and
   sample rows.
2. `POST /api/tasks/upload` (multipart `file`, `mapping` as JSON, optional `name`,
   `source_timezone`, `invalid_row_policy`, `linked_dataset_id`) validates and stores.

| Field | Required | Notes |
|---|---|---|
| `timestamp` | yes | ISO 8601; values without offset are read in `source_timezone` (default UTC) |
| `application` | yes | |
| `action` | yes | |
| `user_id` | yes | must be **pseudonymous**: values containing `@` are rejected, even when invalid rows would otherwise be skipped |
| `session_id` | no | exported session identifier |
| `business_case_id` | no | link to a case of a case-centric dataset |
| `duration` | no | seconds, explicitly exported; non-negative |

Only these fields are stored. **Every other column is dropped** and listed in the import
findings and in the log's `ignored_columns` (for example a `window_title` column). Invalid
rows (missing values, unparsable timestamps, invalid durations) reject the import by default;
with `skip_invalid` they are skipped and counted. Delimiter and encoding (UTF-8 or
Windows-1252) are detected.

`POST /api/tasks/demo` imports `sample_data/task_events_demo.csv` — **synthetic** events of
six pseudonymous accounts-payable clerks posting invoices of the purchase-to-pay demo (3,968
events). Its `business_case_id` values are case ids of the P2P demo; if the P2P demo dataset
is loaded, the task log is linked to it automatically.

## Sessionization

Configurable per request (`mode`, `gap_minutes`, default 30):

| Mode | A new session starts when … |
|---|---|
| `source` | the exported `session_id` changes (events without one fall back to the inactivity rule) |
| `inactivity` | the user changes or the gap to the user's previous event exceeds the threshold |
| `source_and_inactivity` (default) | the session id or user changes, or the gap exceeds the threshold |

Events are ordered by user, timestamp and source row.

## Analyses

All endpoints take the sessionization parameters; most take `granularity` = `action`
(*application · action*) or `application` (consecutive repeats of the same application are
collapsed).

* **Summary** (`/summary`): sessions, events, users, applications, actions, sessions linked to
  business cases, median events per session, median **elapsed span**, explicit duration total
  and its coverage.
* **Task variants** (`/variants`): sessions with identical step sequences, with share, users,
  median elapsed span and duration coverage.
* **Repeated task sequences** (`/sequences`, `min_length`, `max_length`, `min_sessions`):
  contiguous step sequences that recur across sessions, with occurrences, sessions and users.
  A sequence is hidden when a longer one covers exactly the same occurrences.
* **Application switches** (`/switches`): consecutive steps within a session where the
  application changes; totals, share, sessions with switches, switches per session, and the
  most frequent from → to pairs.
* **Task DFG** (`/dfg`): directly-follows frequencies of steps within sessions, start/end
  counts. Node durations are means of **explicit** durations only, with the number of events
  that had one.
* **Business cases** (`/cases`, optional `dataset_id`): per `business_case_id`, sessions,
  events, users, applications and explicit duration; with a linked dataset
  (`PUT /api/tasks/{id}/link`), whether an object with that id exists there (of any object
  type; the type is shown), with a link to that object in its own perspective.
  Deleting the dataset removes the link.

## Time

Working time is **never inferred** from gaps between events. The time between a session's
first and last event is reported as *elapsed span* and labelled as not being working time.
Durations appear only where the source exported them, always together with their coverage.

## Tests

`tests/test_tasks.py` (8 tests) with `tests/fixtures/task_events_small.csv` (expected values in
`tests/fixtures/README.md`): mapping suggestion, storage of mapped fields only, rejection of
identifying user ids, invalid timestamps and durations (reject and skip policies), unknown
mapping fields and time zones, all sessionization modes and gap thresholds, source-timezone
handling, variants at both granularities, repeated sequences, application switches, the task
DFG without inferred durations, business-case links and their removal on dataset deletion,
demo import and deletion.
