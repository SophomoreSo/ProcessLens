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
