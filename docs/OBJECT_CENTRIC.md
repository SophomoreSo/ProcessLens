# The object-centric data model

Every ProcessLens dataset is **object-centric**: it is an event log in the sense of OCEL 2.0,
with events, objects of several types, event-to-object (E2O) and object-to-object (O2O)
relationships, and object attribute values that change over time. Analyses look at a
dataset through a **lead object type** (the *perspective*): its objects play the role of
cases. Every page can switch the lead type; object-centric views (OC-DFG across types,
object-type graph, OC Petri nets) look at all types at once.

## Storage

| Table | Content |
|---|---|
| `datasets` | the workspace entry: name, version, settings revision, display timezone, import provenance, **main object type**, analysis settings of the main type, `object_type_settings` for the other types, `ocel_log_id` |
| `ocel_logs` | object and event types (with typed attributes), counts, import findings |
| `ocel_events` | one row per event, stored **once**: id, type, time (UTC), source position, attributes, and the standard fields `start_ts`, `resource`, `department`, `cost`, `source_row` |
| `ocel_objects` | object id and type (an id has exactly one type) |
| `ocel_object_attributes` | attribute values with their validity time and, if a source row reported them, that event |
| `ocel_e2o` / `ocel_o2o` | relationships with qualifiers |

Migration `0007` converted every pre-existing case-centric dataset into this model (one object
type `case`; event ids, order, standard fields, attributes and the case status as attribute
history kept; version and settings revision unchanged) and every standalone OCEL log into a
dataset. The old `events` table is kept as `legacy_events`; SQLite databases are copied to
`processlens.db.bak-0007` before the conversion.

## Perspectives (lead object type)

Every analysis endpoint takes `object_type` (default: the dataset's main type); the pages keep
it in the URL and offer it as the **Objects** selector in the top bar.

The view of a dataset through type *T* is built by flattening: every object of type *T*
becomes a case whose events are the events related to it, ordered by timestamp, then source
position. An event related to several objects of type *T* therefore appears in each of their
timelines. To keep the numbers honest:

| Figure | Rule |
|---|---|
| Objects (cases) | objects of type *T* with at least one event |
| Events | **unique events** related to an object of type *T* — each counted once |
| Object-event rows | (object, event) pairs; shown next to events when they differ |
| Total cost | sum of event costs over **unique events** |
| Cost of an object | sum of the costs of the events the object took part in (a shared event's cost appears in every one of its objects, so object costs are not additive) |
| Variants, durations, rework, transitions, filters | per object, exactly as for cases |

Settings that make sense per type — completion rule, end activities, completed/open status
values, reference time — are stored per object type (the main type's in the dataset's own
fields; the main type can be changed, and its settings move with it).
**Conformance rules, alert rules, mining runs and approved reference models belong to one
object type**: rules are created for the current lead type, listed with their type, and
evaluated on the objects of that type; a filter cannot use a rule of another type; alerts
link to the object in its own perspective. Mining runs record the lead type in their
provenance, snapshot and cache key.

Exports of cases, events, variants, transitions and XES use the lead type (event exports
carry `ocel_event_id` so copies of shared events are identifiable). The **OCEL 2.0 JSON**
export (`GET /api/datasets/{id}/exports/ocel.json`) writes the whole dataset — all types,
relationships and attribute histories — and imports again with identical figures (tested for
an OCEL-imported and a CSV-imported dataset).

## Import

All formats go through the import wizard (`/import`) and run as import jobs (progress,
re-import as a new dataset version).

**CSV.** Each row is one event. The *case* column identifies the event's object of the lead
type, whose type you name (default `case`). Every other column gets a role:

| Role | Stored as |
|---|---|
| event attribute | an attribute of the event |
| attribute of the case object | the case object's attribute history (a value is recorded when it first appears or changes, at the event's time) |
| related objects | objects of a type you name; the event is related to them (qualifier = column name). An optional separator splits multi-valued cells (`I1;I2`) |
| do not import | — |

The case status column becomes the case object's `status` attribute history; completion by
case status uses its latest value. Validation reports empty object cells and values that look
multi-valued without a separator, and **rejects** ids that would be used with two object types.

**XES.** Converted to the same table (see [MINING.md](MINING.md) and the README for lifecycle
handling): `case:concept:name` identifies the trace object (type of your choice), trace
attributes (`case:*`) are suggested as attributes of that object, and event attributes such as
`org:resource` can be turned into objects, so events are shared between traces and resources.

**OCEL 2.0 JSON** (`.json`, `.jsonocel`) is validated completely on upload; with any error
nothing is staged and the findings are returned. Supported constructs: `objectTypes`,
`eventTypes` (typed attributes), `objects` (attributes with `time`, O2O `relationships` with
qualifiers), `events` (attributes, E2O `relationships` with qualifiers). Other keys, untyped
values, naive times (read as UTC) and duplicates are reported. You then choose the main object
type (the type related to the most events is suggested) and which event attributes are the
resource, department, cost and start time (suggested from `org:resource`, `org:group`,
`cost:total`/`cost`, `start_timestamp`). Attributes stay with the events as well. Cost values
that are not numbers and start times that are unreadable or later than the event are left
empty and counted.

## Object-centric views

| Page / endpoint | What it shows |
|---|---|
| Overview → *Object model* (`GET …/ocel/stats`) | per type: objects, unique events, E2O links, events with several objects of the type; shared events; O2O; unique-event cost |
| Process explorer → *Object-centric* (`GET …/ocel/ocdfg`) | nodes = event types (unique events); edges per object type with object transitions, objects, unique event pairs and median elapsed time; type selection |
| Objects (`GET …/cases/{id}`, `GET …/ocel/objects/{id}`) | the object's timeline with the other objects of each event, attribute history, related objects via O2O and shared events — every object links to its own perspective |
| Object relationships (`GET …/ocel/type-graph`, `…/relationship-delays`) | object-type graph (shared events and distinct object pairs between types, O2O by qualifier); lifetimes of the lead type's objects grouped by the number of related objects of each other type |
| Model discovery → *Object-centric Petri net* (`POST …/ocel/ocpn`) | PM4Py's OC Petri net over all or selected types (places per type, variable arcs, token counts per place) as a mining run |

## Limitations

* No object-centric conformance checking: rules and alignments are evaluated per lead type.
* OCEL XML and SQLite are not imported (JSON only).
* Filters apply to the lead type's objects; conditions combining several object types
  (“orders whose items …”) are not available yet.
* Object attribute values are stored as text.

## Tests

`tests/test_ocel.py` (14), `tests/test_object_import.py` (4) and `tests/test_migration.py` (1)
with the fixtures `ocel_small.json`, `lifecycle.xes` and inline CSVs: shared events stored and
counted once, perspectives with unique events, object-event rows and unique-event cost, OC-DFG
object transitions vs unique event pairs, object-type graph, object timelines and attribute
histories, completion from status history, rules per type and their filters, per-type
settings and main-type change, CSV object columns (multi-valued cells, type conflicts), XES
trace and attribute objects, OCEL validation findings, OC Petri net runs, OCEL export round
trips, demo datasets and the upgrade of a pre-0007 database with identical KPIs.
