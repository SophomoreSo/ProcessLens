# Guided analysis

Page: **Guided analysis** (`/guide`). The user picks a predefined question; the answer is
computed by a fixed backend function that reuses the regular analytics. There is no language
model and no free-text interpretation, so the same inputs always give the same answer.

`GET /api/guide/questions` lists the questions with their parameters.
`GET /api/guide/answer?question=…&dataset_id=…` (or `ocel_log_id=…`) answers one; dataset
questions accept the usual filter query parameters, so the answer covers exactly the
population shown on the analysis pages.

| Question | Data | Function | Parameters |
|---|---|---|---|
| Which transitions take the longest? | dataset | directly-follows transitions ranked by elapsed time | `statistic` (median, p90, mean), `min_cases` (5), `limit` (10) |
| Which variants have the most rework? | dataset | variants with a repeated activity, ranked by repeated executions (cases × repeats per case) | `limit` (10) |
| How many cases violate the SLA? | dataset | maximum-duration evaluation (same as the conformance rule) | `max_hours` (default: the dataset's first maximum-duration rule), `metric` (completed throughput, observed duration), `limit` (20) |
| How do departments differ? | dataset | participating-case duration, rework and elapsed time per department | `min_cases` (5) |
| Which object relationships are associated with delays? | OCEL log | object lifetimes grouped by number of related objects of each other type | `object_type` |

Every answer contains:

* a **headline** sentence with the key numbers;
* the **metric** name and its precise **definition**;
* **provenance**: dataset (or OCEL log) id, name and version, settings revision, normalized
  filters, population (cases in the selection of the total) and the **sample size** the
  numbers rest on (for example transition occurrences or evaluable cases);
* the validated **parameters** actually used (including a default SLA taken from a rule);
* a **chart** specification (horizontal bars or a histogram with the SLA threshold) and a
  **table**;
* **caveats** (for example that elapsed time mixes waiting and processing, that department
  comparisons are descriptive, that relationships are associations);
* **links** to the detailed pages with the same dataset and filters.

When the data cannot answer the question, the answer has `status: not_answerable` and a
reason — for example no SLA defined, no department column, fewer than two departments with
enough cases, no transitions seen in enough cases, no relationship groups with at least three
objects — instead of an empty chart or a zero.

## Tests

`tests/test_guide.py` (8 tests) checks every question against the hand-checkable fixture
(`tests/fixtures/hand_check.csv`) and the OCEL fixtures: exact values, determinism, filters
applied and reported, default SLA from a conformance rule, not-answerable cases and invalid
requests.
