# ProcessLens analytics semantics

Precise definitions of every metric, filter and rule. The implementation lives
in `app/services/` and is pinned down by the tests in `tests/`.

## Cases are objects of the lead type

Datasets are object-centric ([OBJECT_CENTRIC.md](OBJECT_CENTRIC.md)). Every figure below is
computed for one **lead object type**: a *case* is an object of that type, and its events are
the events related to it, in time order. An event related to several objects of the lead type
belongs to each of their cases. Therefore:

* **Event count** counts **unique events**; the overview also shows the **object-event rows**
  (one per object and event) when the two differ.
* **Total cost** sums event costs over **unique events**. The cost of a single case (object) is
  the sum over its events, so the costs of objects that share events are not additive.
* All other case metrics (durations, variants, rework, transitions, completion, conformance
  results, filters) are per object exactly as defined below.

## Event ordering

* All timestamps are normalized to **UTC** at import. Values with an explicit
  offset (`Z`, `+02:00`) use it; values without one are interpreted in the
  selected source timezone.
* Within a case, events are ordered by **(timestamp, source row number)**.
  `seq` is the resulting 0-based position.
* **Equal timestamps do not establish a causal order.** Ties are kept and
  ordered by source row; the import reports them as a warning, the case
  timeline marks them, and rules whose result depends on the order of tied
  events report *not evaluable*.
* Repeated activities are never removed: they can represent legitimate rework.

## Basic counts

| Metric | Definition |
|---|---|
| Case count | Number of distinct case IDs in the population. |
| Event count | Number of retained (valid, non-quarantined) unique events of those cases (an event shared by several cases counts once). |
| Object-event rows | Number of (case, event) pairs; equals the event count when no event is shared by two cases of the lead type. |
| Activity count | Number of distinct activity names among those events. |
| Variant | The ordered tuple of activity names of a case. Its ID is the first 12 hex characters of SHA-1 over the names joined by U+001F. |
| Variant frequency | Cases following the variant, and that number divided by the population case count. Variants are ranked by case count (desc), then label (asc); cumulative share follows this rank. |
| Rework case | A case in which at least one activity occurs more than once. |
| Repeated-activity count | Σ over activities of (occurrences − 1) in a case = events − distinct activities. |

## Durations

All durations are seconds in the API. **Mean, median and p90** are reported;
median and p90 use numpy's default **linear interpolation** (position
`q·(n−1)` in the sorted values). Every statistic carries its sample size `n`.

| Metric | Definition | Population |
|---|---|---|
| Observed case duration | last event timestamp − first event timestamp | every case (0 for single-event cases) |
| Completed throughput | observed duration of a case whose status is *completed* | completed cases only |
| Open-case age | reference time − first event timestamp | *incomplete* and *unknown* cases |
| Inter-event elapsed time | timestamp of an event − timestamp of the preceding event in the same case | consecutive event pairs |
| Activity execution duration | completion timestamp − start timestamp of an event | only events that have both |

Inter-event elapsed time is **not** labeled waiting time or processing time:
event timestamps alone cannot distinguish the two.

### Completion status

The last observed event is never taken as proof that a case is finished. Each
dataset has an explicit completion rule (editable on the Datasets page):

* `none` — every case is **unknown**; completed-throughput statistics are empty.
* `end_activity` — **completed** if the case's last event (by the ordering above)
  is one of the configured end activities; otherwise **incomplete**.
* `case_status` — the case's last non-empty value of the mapped status column:
  **completed** if listed among completed values, **incomplete** if listed among
  open values, otherwise **unknown**.

### Reference time

Open-case age uses the dataset's configured reference time, or — if none is
configured — the latest event timestamp in the dataset. The wall clock is never
used, so results are reproducible.

## Directly-follows graph

* An **edge A → B** exists for every pair of consecutive events (A then B) in the
  same case. Self-loops (A → A) are edges too.
* **Edge occurrence count** — total number of such pairs.
* **Edge case count** — number of distinct cases containing the pair at least once.
* Elapsed statistics on an edge are over its occurrences.
* Synthetic **start → first activity** and **last activity → end** edges count
  cases; they have no elapsed time.
* Node occurrences = events with that activity; node case count = distinct cases.
* The graph is **not a BPMN model** and shows no causality, concurrency or
  decision logic. Visibility thresholds in the UI only hide elements in the
  drawing; they never change the analysed population.

## Filters

Filtering is **case-level**: a filter selects cases, and every selected case keeps
its complete event history. All criteria combine with **AND**, and the same
filter applies to KPIs, graph, variants, cases, performance, cohort comparisons,
conformance evaluations and exports.

| Parameter | Semantics |
|---|---|
| `start_from`, `start_to` | Calendar date of the case's first event in the dataset **display timezone**; both bounds inclusive. |
| `activity` (repeatable) | Case contains **every** listed activity at least once. |
| `exclude_activity` | Case contains **none** of the listed activities. |
| `resource`, `department` | **At least one event** of the case has **any** of the listed values. |
| `variant` | Case follows any listed variant ID. |
| `min_duration_h`, `max_duration_h` | Observed duration in hours, inclusive bounds. |
| `rework` | `true`: rework cases; `false`: cases without repeated activities. |
| `status` | Completion status in the listed values. |
| `conformance_rule` + `conformance_result` | Case's result for that rule is one of the listed results. |
| `transition_from` + `transition_to` | Case contains that directly-follows pair (`__start__`/`__end__` = markers). |

There is no event-level filtering mode in this version; it would change the
reconstructed traces and is therefore not offered implicitly.

## Rule-based conformance

Each rule yields **compliant**, **violating** or **not evaluable** per case, with an
explanation. A result is reported as final only when later events could not
change it. A case that is not known to be complete (*incomplete* or *unknown*)
gets *not evaluable* whenever its result could still change, unless the rule
sets `assume_complete`.

| Rule | Compliant | Violating | Not evaluable |
|---|---|---|---|
| Required activity X | X occurs | X absent, case complete | X absent, case open |
| Forbidden activity X | X absent, case complete | X occurs (any number of times) | X absent, case open |
| A must precede B | every B has an A with a strictly earlier timestamp; or no B and case complete; or no B, open case, A already occurred | some B has no earlier and no tied A | some B is tied with an A (order unknown); or no A and no B in an open case |
| Max occurrences of X ≤ k | count ≤ k, case complete | count > k | count ≤ k, case open |
| Max duration — completed throughput | completed case within limit | completed case over limit | case not completed |
| Max duration — observed duration | observed duration within limit (for open cases: so far) | observed duration over limit | — |
| Expected start activity | first event in the set (or all events tied at the first timestamp are) | first event not in the set (or none of the tied ones are) | tied first events, only some in the set |
| Expected end activity | complete case, last event in the set | complete case, last event not in the set | case open; or tied last events with mixed membership |

This is not Petri-net replay or alignment-based conformance.

## Alerts

* Rule types: duration threshold (observed duration, completed throughput or
  open-case age), rework (any or a specific activity), conformance violation.
* One alert per **(alert rule, case)**, enforced by a database unique constraint.
  Re-evaluation updates the message and `condition_active`; it never duplicates
  alerts and never changes a status set by the user.
* Evaluation runs only on demand and after successful imports. There is no
  scheduler, and nothing is sent outside the application.
