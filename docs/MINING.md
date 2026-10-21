# Process discovery, formal conformance and algorithm comparison

This document describes the mining features added on top of the case-centric analytics:
the algorithm registry, discovery runs, model representations and exports, formal
conformance checking, the comparison workspace and the run (job) system. Object-centric
mining is described in [OBJECT_CENTRIC.md](OBJECT_CENTRIC.md), task mining in
[TASK_MINING.md](TASK_MINING.md) and guided analysis in [GUIDED_ANALYSIS.md](GUIDED_ANALYSIS.md).

Pages: **Model discovery** (`/discovery`) and **Algorithm comparison** (`/compare`).
All algorithms run in PM4Py **2.7.23.11** inside an isolated worker process.

## Principles

* **No silent substitution.** If an algorithm or export is not available, it is shown as
  unavailable with the reason, and a request for it is rejected. ProcessLens never runs a
  different algorithm in its place.
* **Only supported parameters.** Each adapter declares the parameters the implementation
  actually supports, with type, default, range and description. Unknown parameters are
  rejected (HTTP 422 with a per-parameter error list), never ignored.
* **Native representation first.** Each run stores the model in the representation the
  algorithm produces. Other representations are added only when a semantics-preserving
  conversion exists (for example process tree → Petri net → BPMN).
* **Explicit evaluation status.** Every evaluation method reports `computed`, `unsupported`,
  `not_evaluable` or `failed` (and the run itself can be `cancelled` or `timed_out`). A
  missing metric is never shown as 0.
* **Reproducibility.** Every run records dataset id, version and settings revision, the
  normalized filters and a SHA-256 of the selected case ids, the split (if any), the
  algorithm, validated parameters and the implementation (provider, provider version,
  adapter version, ProcessLens version).

## Algorithm support matrix

| Algorithm | Registry id | Native output | Derived representations | Evaluations | Exports | Status |
|---|---|---|---|---|---|---|
| Inductive Miner (IM) | `inductive` | process tree | Petri net, BPMN | token replay, alignments, soundness | PTML, PNML, BPMN, JSON, PNG | available |
| Inductive Miner – infrequent (IMf) | `inductive_infrequent` | process tree | Petri net, BPMN | token replay, alignments, soundness | PTML, PNML, BPMN, JSON, PNG | available |
| Inductive Miner – directly-follows (IMd) | `inductive_directly_follows` | process tree | Petri net, BPMN | token replay, alignments, soundness | PTML, PNML, BPMN, JSON, PNG | available |
| Heuristics Miner (classic) | `heuristics` | heuristics net | Petri net | token replay, alignments, soundness | PNML, JSON, PNG | available |
| Heuristics Miner++ | `heuristics_plusplus` | heuristics net | Petri net | token replay, alignments, soundness | PNML, JSON, PNG | available |
| Alpha Miner | `alpha` | Petri net | — | token replay, alignments, soundness | PNML, JSON, PNG | available |
| Alpha+ Miner | `alpha_plus` | Petri net | — | token replay, alignments, soundness | PNML, JSON, PNG | available |
| POWL discovery | `powl` | POWL model | Petri net | token replay, alignments, soundness | PNML, JSON, PNG | available |
| DECLARE discovery | `declare` | DECLARE constraints | — | DECLARE conformance | JSON | available |
| Log Skeleton discovery | `log_skeleton` | log skeleton | — | log-skeleton conformance | JSON | available |
| Transition-system discovery | `transition_system` | transition system | — | — | JSON, PNG | available |
| Temporal-profile discovery | `temporal_profile` | temporal profile | — | temporal-profile conformance | JSON | available |
| ILP Miner (region-based) | `ilp` | Petri net | — | token replay, alignments, soundness | PNML, JSON, PNG | available (research) |
| Genetic Miner | `genetic` | Petri net | — | token replay, alignments, soundness | PNML, JSON, PNG | available (research, randomized) |
| Split Miner | `split_miner` | — | — | — | — | **unavailable** |
| Fuzzy Miner | `fuzzy_miner` | — | — | — | — | **unavailable** |
| Object-centric Petri net | `oc_petri_net` | OC Petri net | — | — | JSON, PNG | available (OCEL logs only) |

`GET /api/mining/algorithms` returns this registry, including availability (decided from what
is installed), parameter schemas, preconditions, limitations, compatible evaluations and
exports. The pages build their parameter forms from it.

### Unavailable algorithms and exports

| Item | Reason |
|---|---|
| Split Miner | No implementation is installed. The reference implementation is a Java tool and ProcessLens has no verified integration with it. |
| Fuzzy Miner | PM4Py 2.7 provides no Fuzzy Miner, and no other verified Python implementation is installed. |
| SVG export | Server-side rendering needs Graphviz, which is not a dependency. Graph models are exported as **PNG** from the browser (Cytoscape.js) instead. |

### Parameters and limitations

Defaults are those of the registry; ranges are validated on the server.

**Inductive Miner (IM)** — `disable_fallthroughs` (bool, false). Sound, perfectly fitting,
block-structured models; infrequent behaviour is kept, which can make the model imprecise.

**Inductive Miner – infrequent (IMf)** — `noise_threshold` (0–1, 0.2): directly-follows
relations less frequent than this share of an activity's most frequent outgoing relation are
ignored when searching cuts; `disable_fallthroughs` (bool, false). Fitness is no longer
guaranteed.

**Inductive Miner – directly-follows (IMd)** — no parameters. Works from the directly-follows
graph only (scalable) and can lose information present in full traces.

**Heuristics Miner (classic)** — `dependency_threshold` (0–1, 0.5), `and_threshold` (0–1,
0.65), `loop_two_threshold` (0–1, 0.5), `min_act_count` (≥ 1, 1), `min_dfg_occurrences`
(≥ 1, 1). Frequency-based; soundness is not guaranteed. The Petri net is a derived
representation used for replay.

**Heuristics Miner++** — `dependency_threshold`, `and_threshold`, `min_act_count`,
`min_dfg_occurrences` (as above). ProcessLens passes atomic events (start = completion), so
interval-based concurrency detection is not used.

**Alpha / Alpha+** — no parameters. Alpha cannot discover short loops or handle noise; Alpha+
handles short loops but is still noise-sensitive. Both may yield unsound nets.

**POWL** — `variant` (`MAXIMAL`, `DYNAMIC_CLUSTERING`, `BRUTE_FORCE`, `TREE`; default
`MAXIMAL`), `filtering_weight_factor` (0 ≤ x < 1, 0), `order_graph_filtering_threshold`
(0.5 < x ≤ 1, optional, only valid with `DYNAMIC_CLUSTERING`). Partial-order models; the
Petri net is derived for replay.

**DECLARE** — `min_support_ratio` (0–1, 0.3), `min_confidence_ratio` (0–1, 0.9),
`allowed_templates` (subset of 18 templates; empty = all), `considered_activities` (empty =
all). Large logs can produce many constraints; not an executable flow.

**Log Skeleton** — `noise_threshold` (0–1, 0). Captures observed relations and frequency
bounds; does not generalize beyond the observed behaviour.

**Transition system** — `direction` (`forward`/`backward`), `window` (1–10, 2), `view`
(`sequence`/`set`/`multiset`). No conformance method is offered for transition systems.

**Temporal profile** — no parameters. Uses **real** timestamps (mean and standard deviation
of times between activity pairs). Conformance uses the deviation factor `zeta` (default 2).

**ILP Miner** — `alpha` (0–1, 1 = no filtering). Can be slow on large logs.

**Genetic Miner** — `population_size` (2–2000, 100), `generations` (1–1000, 30),
`elitism_rate`, `crossover_rate`, `mutation_rate` (0–1), `elitism_min_sample` (1–1000, 5),
`seed` (default 42). The miner has no seed parameter; ProcessLens seeds Python's random
generator before calling it and runs the worker with `PYTHONHASHSEED=0`, which made results
repeatable in our tests. The adapter is still marked *randomized*.

## Event-log preparation

PM4Py re-sorts events by timestamp, which would reorder events that share a timestamp.
ProcessLens therefore passes a synthetic, strictly increasing `time:timestamp` that
reproduces its own event order (timestamp, then source row), and the real timestamps in a
separate column used only by time-based algorithms (temporal profile and its conformance).
Discovery and conformance always use exactly the filtered case population shown on screen.

## Representations and exports

Models are serialized to JSON with stable ids and rendered natively:

* **Process tree** — operators (sequence, xor, parallel, loop) and leaves; tree layout.
* **Petri net** — places with initial/final markings, visible and silent transitions, arcs
  with weights.
* **Heuristics net** — activities with occurrences, dependency arcs with dependency measure
  and frequency, start/end counts.
* **BPMN** — tasks, gateways (exclusive, parallel, inclusive), start/end events, flows.
* **POWL** — nested operators and partial orders (rendered as a structured list).
* **Transition system** — states and labelled transitions.
* **DECLARE / Log skeleton / Temporal profile** — searchable tables.

Downloads (`GET /api/mining/runs/{id}/export/{fmt}`):

| Format | Content |
|---|---|
| `pnml` | Petri net with initial and final markings (PM4Py writer) |
| `ptml` | Process tree (PM4Py writer) |
| `bpmn` | BPMN 2.0 XML (PM4Py writer, without diagram layout) |
| `json` | Application-specific JSON (`processlens_model`, `format_note`, `model`) for declarative and state-based models |
| `representation` | All JSON representations of the run |
| PNG | Rendered in the browser from the graph view |

PNML and PTML files can be uploaded again as reference models (round-trip tested).

## Formal conformance checking

Models evaluated against the current population can be either a **discovered model** (a
successful discovery run) or a **user-approved reference model**. A discovered model becomes
a reference only by an explicit *Approve as reference* action (name and note recorded), or by
uploading a PNML/PTML file. The UI and the API always state which kind of model was used.

| Method | Applies to | Result |
|---|---|---|
| Token-based replay | Petri nets with initial and final marking | log fitness, average trace fitness, % fitting traces; ETConformance precision; per-case fitness, missing/remaining/produced/consumed tokens |
| Alignments | Petri nets with initial and final marking | log fitness, average trace fitness, % fitting traces; alignment-based precision; per-case cost and log/model moves |
| Soundness | workflow nets | sound yes/no with diagnostics (`not_evaluable` when the net is not a workflow net) |
| DECLARE conformance | DECLARE models | fitness per case, violated constraints |
| Log-skeleton conformance | log skeletons | deviations per case |
| Temporal-profile conformance | temporal profiles | deviating activity pairs per case (`zeta` standard deviations) |

Statuses: a method applied to an unsuitable model type is `unsupported` (for example
alignments on DECLARE); a method that cannot run on this input is `not_evaluable` with a
reason (no markings, empty population, not a workflow net, too many variants); an exception
inside PM4Py is `failed` with the algorithm-specific error. Precision can be switched off,
which reports it as `not_evaluable` (never 0).

**Execution limits.** Alignments are computed once per distinct variant, in chunks of 25
variants with progress reported per chunk, and mapped back to every case. If the population
has more variants than `max_variants` (default and maximum
`PROCESSLENS_MINING_MAX_ALIGNMENT_VARIANTS=2000`), alignments are `not_evaluable` instead
of running indefinitely. Each run also has a timeout (below).

**Case diagnostics.** `GET /api/mining/runs/{id}/cases?method=…&only_deviating=true` pages
through per-case results, worst first, with links to the case explorer.

## Comparison workspace

`POST /api/datasets/{id}/mining/compare` discovers and evaluates up to **8** algorithms on the
same population in one run.

* **Train/test split by case.** A case goes to the training set when
  `sha256(seed ␟ case_id)` (first 8 bytes as a fraction) is below the training ratio, so the
  split is deterministic, independent of row order and never splits a case. Models are
  discovered on the training cases and evaluated on the held-out cases; the split sizes,
  ratio, seed and hashes of both case sets are stored. Without a split, evaluation is
  in-sample and labelled as such.
* **Reported per algorithm:** status and algorithm-specific error, native representation,
  discovery runtime, model size (places/transitions/arcs, nodes, constraints…), token-replay
  and alignment fitness and precision, soundness, declarative fit, parameters and warnings.
* **No combined score.** Metrics are shown side by side; ProcessLens does not rank
  algorithms by an aggregate number.
* Two models can be viewed side by side in any of their representations. Results export as
  CSV and JSON (`…/export/comparison.csv|json`).

## Runs: background execution, cancellation, timeouts, caching

* Discovery, conformance, comparison, model import and OCPN discovery are **mining runs**
  (`mining_runs` table, migration `0003`). Requests return immediately (`202 Accepted`);
  pages poll `GET /api/mining/runs/{id}`.
* Runs execute one at a time on a supervisor thread that hands the computation to a
  **separate worker process** (`python -m app.mining.worker`, length-prefixed pickle over
  stdin/stdout). PM4Py never runs in the request path or in the web process.
* **Cancellation** (`POST /api/mining/runs/{id}/cancel`): a queued run is cancelled at once; a
  running run's worker process is killed and restarted.
* **Timeouts**: default 300 s (`PROCESSLENS_MINING_TIMEOUT_S`), user-selectable up to 3600 s
  (`PROCESSLENS_MINING_MAX_TIMEOUT_S`). On expiry the worker is killed and the run is
  `timed_out`.
* **Persistent status**: `queued`, `running`, `succeeded`, `failed`, `cancelled`,
  `timed_out`, `interrupted`. Runs that were queued or running when the server stopped are
  marked `interrupted` on the next start.
* **Honest progress**: stage text is always shown; a progress fraction is reported only where
  it can be measured (alignment chunks, comparison steps), otherwise the UI shows elapsed time.
* **Caching**: the cache key is a SHA-256 over kind, dataset id, version, settings revision,
  normalized filters, case-id hash, split, algorithm, validated parameters and implementation
  version. An identical request returns the earlier successful run (`200`, `cached: true`);
  `force` recomputes.
* **Stale inputs**: if the dataset was re-imported or its settings changed between queueing
  and execution, or the filter now selects different cases, the run fails with an explicit
  error instead of computing on different data.
* **History**: `GET /api/mining/runs?dataset_id=…&kind=…&status=…` lists runs with their full
  provenance; the discovery and comparison pages show it.

## API summary

| Route | Purpose |
|---|---|
| `GET /api/mining/algorithms` | registry (case-centric `algorithms` and `object_centric`) |
| `POST /api/datasets/{id}/mining/discover` | start discovery (`algorithm_id`, `params`, `timeout_s`, `force`; filter query parameters) |
| `POST /api/datasets/{id}/mining/conformance` | evaluate a discovered (`{"run_id"}`) or reference (`{"reference_model_id"}`) model with chosen `methods` and `limits` |
| `POST /api/datasets/{id}/mining/compare` | comparison run (`algorithms`, `split`, `evaluation`) |
| `GET /api/mining/runs`, `GET /api/mining/runs/{id}` | history, status and result |
| `POST /api/mining/runs/{id}/cancel` | cancel |
| `GET /api/mining/runs/{id}/model` | JSON representations (`index` for comparison rows) |
| `GET /api/mining/runs/{id}/cases` | case-level diagnostics |
| `GET /api/mining/runs/{id}/export/{fmt}` | `pnml`, `ptml`, `bpmn`, `json`, `representation`, `comparison.csv`, `comparison.json` |
| `GET/POST /api/datasets/{id}/reference-models`, `POST …/reference-models/upload`, `GET/DELETE …/reference-models/{rid}`, `GET …/{rid}/export.pnml` | reference models |

## Tests

`tests/test_mining.py` (32 tests) uses the deterministic fixture
`tests/fixtures/mining_fixture.csv` (22 cases, 113 events): every available adapter and its
output type, PNML/PTML/BPMN export and re-import with PM4Py's readers, fitting (perfect
fitness) and deviating cases (log and model moves, non-fitting tokens), declarative statuses,
the alignment variant limit, reference approval and upload, deterministic and isolated
train/test splits, comparison rows, caching, stale-snapshot detection, cancellation of
running and queued runs, timeouts, interrupted-run recovery and cleanup on dataset deletion.
