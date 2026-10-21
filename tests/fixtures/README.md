# Hand-checkable fixture: `hand_check.csv`

16 events, 5 cases, rows deliberately not in case/time order. Import with
completion mode `case_status` (completed = `Completed`, open = `Open`) and
reference time `2024-01-06T00:00:00Z`.

| Case | Trace | Observed duration | Status |
|---|---|---|---|
| C1 | A B C D | 4 h | completed |
| C2 | A B **B** C D (rework) | 6 h | completed |
| C3 | A B C D | 2 h | completed |
| C4 | A C | 1 h | incomplete (status `Open`) |
| 007 | A (single event; leading zeros) | 0 h | unknown (empty status) |

Expected metrics (all asserted in `tests/test_metrics.py`):

* cases 5, events 16, distinct activities 4, variants 4
  (A→B→C→D: 2 cases = 40 %; A→B→B→C→D, A→C, A: 1 case each)
* rework cases 1 (C2), repeated-activity count 1, rework rate 0.2
* directly-follows edges (occurrences / distinct cases): A→B 3/3, B→B 1/1, B→C 3/3,
  C→D 3/3, A→C 1/1; start→A 5; D→end 3, C→end 1, A→end 1
* A→B elapsed: 1 h, 2 h, 0.5 h → mean 4200 s, median 3600 s, p90 6480 s
* observed duration (all 5): 4, 6, 2, 1, 0 h → mean 9360 s, median 7200 s, p90 18720 s
* completed throughput (C1–C3): 4, 6, 2 h → mean 14400 s, median 14400 s, p90 20160 s
* open-case age at reference time: C4 40 h (144000 s), 007 16 h (57600 s)
* status counts: completed 3, incomplete 1, unknown 1
* total cost 73

p90 uses linear interpolation: sorted values v, position 0.9·(n−1).

# Object-centric fixture: `ocel_small.json` (OCEL 2.0 JSON)

Orders o1 (items i1, i2) and o2 (item i3); packages p1 (i2, i3 — items of two
different orders) and p2 (i1). Eight events:

| Event | Type | Objects | cost |
|---|---|---|---|
| e1 | place order | o1, i1, i2 | 10 |
| e2 | place order | o2, i3 | 10 |
| e3–e5 | pick item | i1 / i2 / i3 | 1 each |
| e6 | send package | p1, i2, i3 | 5 |
| e7 | send package | p2, i1 | 5 |
| e8 | deliver package | p1, i2, i3 | — |

Expected (asserted in `tests/test_ocel.py`): 8 unique events, 7 objects,
16 event-object relationships, 6 object-object relationships, 5 shared events,
cost over unique events 33 (a per-relationship sum would give 78). OC-DFG for
`item`: place order → pick item 3 object transitions, send package → deliver
package 2 object transitions but only 1 unique event pair (e6 → e8). Flattening
by `item`: 3 cases, 11 rows from 8 unique events (3 duplicated rows).
Object o1 changes `status` from `placed` to `shipped`.

# Task-event fixture: `task_events_small.csv`

Two pseudonymous users, 11 events, exported sessions s1 (u1, 4 events, with a
58-minute gap before `ERP Post`), s2 (u1, 3 events) and s3 (u2, 4 events). The
`window_title` column is not mapped and must not be stored. Explicit durations
exist for 3 events (10 + 30 + 5 = 45 s).

Expected (asserted in `tests/test_tasks.py`): sessions — `source` 3,
`source_and_inactivity` (30 min) 4, `inactivity` 30 min 3, 120 min 2.
Variants (source, action level): Email·Open → PDF·View → ERP·Enter → ERP·Post
in 2 sessions, the same without ERP·Post in 1. Repeated sequences (≥ 2
sessions): the 3-step prefix (3 sessions) and the 4-step sequence (2 sessions).
Application switches: 6 of 8 consecutive steps; Email→PDF 3, PDF→ERP 3.
