# Object-centric process mining (OCEL 2.0)

Page: **Object-centric** (`/ocel`). Tables: `ocel_logs`, `ocel_events`, `ocel_objects`,
`ocel_object_attributes`, `ocel_e2o`, `ocel_o2o` (migration `0004`). Object-centric logs are
stored separately from case-centric datasets: an event can relate to many objects of many
types and is stored **once**.

## Import and validation

`POST /api/ocel/upload` (multipart `file`, optional `name`) accepts **OCEL 2.0 JSON**
(`.json`, `.jsonocel`). XML and SQLite OCEL files are not supported. The file is validated
completely before anything is written; if there is any error, nothing is stored and the
response (`422`) lists every finding with severity, element and a translated message.

Supported constructs:

| Construct | Handling |
|---|---|
| `objectTypes`, `eventTypes` with typed attributes (`string`, `time`, `integer`, `float`, `boolean`) | stored; other attribute types → warning, stored as string |
| `objects` with `id`, `type`, `attributes` (`name`, `value`, `time`) | stored; each attribute value keeps its validity time (changing attributes); attributes without `time` are static |
| object `relationships` (`objectId`, `qualifier`) | stored as object-to-object (O2O) relationships |
| `events` with `id`, `type`, `time`, `attributes` | stored once |
| event `relationships` (`objectId`, `qualifier`) | stored as event-to-object (E2O) relationships |
| any other key | warning *not supported and ignored* (never silently accepted) |

Errors: missing top-level sections, duplicate type names, objects/events without id,
duplicate ids, undeclared object or event types, invalid timestamps, relationships to unknown
objects, no valid events. Warnings: undeclared attributes, values not matching the declared
type, duplicate relationships, timestamps without offset (read as UTC), ignored keys.
Info: objects without any event.

`POST /api/ocel/demo` imports `sample_data/ocel_demo.json` — a **synthetic** order-to-delivery
log (customers, orders, items, packages; 527 events, 261 objects; packages mix items of
different orders; orders change status and price over time), generated deterministically by
`scripts/generate_ocel_demo.py`.

## Counting rules

| Count | Definition |
|---|---|
| Unique events | each event once, however many objects it relates to |
| Objects | distinct object ids |
| Event-object relationships | one per (event, object, qualifier) link |
| Object-object relationships | one per (source, target, qualifier) link |
| Shared events | events related to two or more objects (within the current type selection) |
| Object transitions (OC-DFG) | consecutive event pairs per object of one type |
| Unique event pairs (OC-DFG) | distinct (event, next event) pairs behind those transitions |
| Event cost (unique events) | sum of the `cost` event attribute over unique events |

Example from `tests/fixtures/ocel_small.json`: events e6 and e8 are shared by package p1 and
items i2 and i3. For object type *item*, *send package → deliver package* has **2 object
transitions** (i2 and i3) but **1 unique event pair** (e6 → e8). The cost over unique events
is 33; summing over event-object relationships would wrongly give 78.

## Object-type filtering

Selecting object types keeps the events related to at least one object of a selected type
and the relationships of those types. Events stay single events; counts are recomputed for
the selection (shared events counts only sharing within the selection).

## Analyses

* **Statistics** (`GET /api/ocel/{id}/stats?object_type=…`): the counts above, per object
  type (objects, unique events, E2O links, events with several objects of the type, median
  events per object), per event type, O2O relationships by qualifier.
* **Object-centric DFG** (`GET …/ocdfg`): nodes are event types with unique-event counts;
  edges are per object type with object transitions, objects, unique event pairs and median
  elapsed time. Rendered with one colour per object type.
* **Objects** (`GET …/objects`, `GET …/objects/{object_id}`): paged object list; object
  timeline (events in time order, qualifiers, the other objects sharing each event),
  attribute history with validity times, related objects via O2O (both directions) and via
  shared events. Related objects are links, so one can navigate from object to object.
* **Relationships and delays** (`GET …/relationship-delays?object_type=…`): lifetimes
  (first to last event) of objects of a type, grouped by how many related objects of each
  other type they have (0, 1, 2, 3+), via shared events and via O2O qualifiers. Descriptive
  only.

## Object-centric Petri net

`POST /api/ocel/{id}/ocpn` (`object_types`, `params`, `timeout_s`, `force`) starts a mining run
(kind `ocpn`) that calls PM4Py's `discover_oc_petri_net` in the worker process. Parameters:
`inductive_miner_variant` (`im`, `imf`, `imd`; default `im`) and `noise_threshold` (only with
`imf`). The model is serialized with places per object type, initial/final token counts per
place (object ids are not exposed), visible and silent transitions, and arcs with object
type and a *variable* flag (activities that usually involve several objects of the type).
PM4Py discovers one net per object type by internal flattening and merges them on shared
activities; interactions between types are not checked for soundness, and no conformance
method is offered for OC Petri nets. Runs are cached, cancellable and time-limited like all
mining runs, keyed by log id, log version, source SHA-256, object types and parameters.
Export: application JSON and PNG.

## Flattening to a case-centric dataset

`GET …/projection-preview?object_type=…` reports what flattening would do;
`POST …/project` (`object_type`, optional `dataset_name`, **`acknowledge_flattening: true`
required**) creates a normal case-centric dataset through the import pipeline:

* each object of the chosen type becomes a case;
* an event related to several objects of that type is **copied** into each of their cases;
  events not related to the type are dropped; relations to other types are lost;
* every row keeps `ocel_event_id`, `qualifier` and `objects_sharing_event` attributes so
  copies of the same event remain identifiable;
* event costs are **not** mapped to the cost field (they would be counted once per copy);
* the dataset description records the source log, object type and the number of duplicated
  rows.

The UI shows the duplication warning and requires a confirmation checkbox.

## Tests

`tests/test_ocel.py` (10 tests) with `tests/fixtures/ocel_small.json` (expected values in
`tests/fixtures/README.md`): persistence of shared events once, unique/E2O/O2O/shared counts,
cost over unique events, type filtering, OC-DFG object transitions vs unique event pairs,
object timelines, attribute history and related objects, relationship groups, validation
errors and warnings, projection acknowledgement and event identity, OCPN run (places per
type, initial tokens per type, variable arcs, caching, parameter validation) and deletion.
