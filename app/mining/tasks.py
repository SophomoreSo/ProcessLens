"""Mining computations executed inside the isolated worker process.

Every function receives a plain payload (dicts, lists, pandas DataFrames) and
a ``report(stage_key, fraction=None, **params)`` callback, and returns plain
JSON-serializable data plus text artifacts. Nothing here touches the database.

Event logs arrive as DataFrames with PM4Py's standard column names. The
``time:timestamp`` column is a synthetic, strictly increasing order key that
reproduces ProcessLens's event order (timestamp, then source row) exactly,
because PM4Py re-sorts by timestamp and would otherwise reorder ties. Real
timestamps are supplied in ``real:timestamp`` for time-based algorithms.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from app.mining import serialize
from app.mining.registry import (
    ALIGNMENTS,
    DECLARE,
    DECLARE_CONF,
    LOG_SKELETON,
    LOG_SKELETON_CONF,
    SOUNDNESS,
    TEMPORAL_CONF,
    TEMPORAL_PROFILE,
    TOKEN_REPLAY,
)

Report = Callable[..., None]
CASE, ACT, TS, REAL_TS = "case:concept:name", "concept:name", "time:timestamp", "real:timestamp"
KEYS = {"activity_key": ACT, "timestamp_key": TS, "case_id_key": CASE}


class TaskError(Exception):
    """Algorithm-specific failure with a catalog key (shown to the user)."""

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


def _write(fn, suffix: str, *args) -> str:
    """Call a PM4Py writer that needs a file path; return the file content."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        fn(*args, path)
        return Path(path).read_text(encoding="utf-8")
    finally:
        Path(path).unlink(missing_ok=True)


def _read(fn, suffix: str, content: str, **kwargs):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        Path(path).write_text(content, encoding="utf-8")
        return fn(path, **kwargs)
    finally:
        Path(path).unlink(missing_ok=True)


def _time_log(log: pd.DataFrame) -> pd.DataFrame:
    """Log with real timestamps (for temporal algorithms); event order is preserved."""
    out = log[[CASE, ACT, REAL_TS]].rename(columns={REAL_TS: TS})
    return out


# =========================================================================== discovery
def discover(payload: dict, report: Report) -> dict:
    import pm4py

    aid = payload["algorithm_id"]
    params = payload["params"]
    log: pd.DataFrame = payload["log"]
    if log.empty:
        raise TaskError("algo.err.empty_log")
    warnings: list[dict] = []
    report("run.stage.discovering")
    started = time.perf_counter()
    reps: dict[str, dict] = {}
    artifacts: dict[str, str] = {}
    native_type = None
    net = im = fm = None

    if aid in ("inductive", "inductive_infrequent", "inductive_directly_follows"):
        from pm4py.algo.discovery.inductive import algorithm as inductive_miner

        variant = {"inductive": inductive_miner.Variants.IM, "inductive_infrequent": inductive_miner.Variants.IMf,
                   "inductive_directly_follows": inductive_miner.Variants.IMd}[aid]
        p = {"activity_key": ACT, "timestamp_key": TS, "case_id_key": CASE,
             "noise_threshold": params.get("noise_threshold", 0.0),
             "disable_fallthroughs": params.get("disable_fallthroughs", False)}
        if aid == "inductive_directly_follows":
            from pm4py.objects.dfg.obj import DirectlyFollowsGraph

            dfg, sa, ea = pm4py.discover_dfg(log, **KEYS)
            tree = inductive_miner.apply(DirectlyFollowsGraph(dfg, sa, ea), variant=variant, parameters=p)
        else:
            tree = inductive_miner.apply(log, variant=variant, parameters=p)
        discovery_s = time.perf_counter() - started
        native_type = "process_tree"
        reps["process_tree"] = serialize.process_tree(tree)
        report("run.stage.converting")
        net, im, fm = pm4py.convert_to_petri_net(tree)
        reps["petri_net"] = serialize.petri_net(net, im, fm)
        reps["petri_net"]["derived_from"] = "process_tree"
        bpmn_model = pm4py.convert_to_bpmn(tree)
        reps["bpmn"] = serialize.bpmn(bpmn_model)
        reps["bpmn"]["derived_from"] = "process_tree"
        artifacts["model.ptml"] = _write(lambda t, p: pm4py.write_ptml(t, p, auto_layout=False), ".ptml", tree)
        artifacts["model.bpmn"] = _write(lambda m, p: pm4py.write_bpmn(m, p, auto_layout=False), ".bpmn", bpmn_model)
    elif aid in ("heuristics", "heuristics_plusplus"):
        from pm4py.algo.discovery.heuristics import algorithm as heuristics_miner

        if aid == "heuristics":
            hn = pm4py.discover_heuristics_net(
                log, dependency_threshold=params["dependency_threshold"], and_threshold=params["and_threshold"],
                loop_two_threshold=params["loop_two_threshold"], min_act_count=params["min_act_count"],
                min_dfg_occurrences=params["min_dfg_occurrences"], **KEYS)
        else:
            from pm4py.algo.discovery.heuristics.variants.plusplus import Parameters as HP

            hn = heuristics_miner.apply_heu(log, variant=heuristics_miner.Variants.PLUSPLUS, parameters={
                HP.DEPENDENCY_THRESH: params["dependency_threshold"], HP.AND_MEASURE_THRESH: params["and_threshold"],
                HP.MIN_ACT_COUNT: params["min_act_count"], HP.MIN_DFG_OCCURRENCES: params["min_dfg_occurrences"],
                HP.ACTIVITY_KEY: ACT, HP.TIMESTAMP_KEY: TS, HP.START_TIMESTAMP_KEY: TS, HP.CASE_ID_KEY: CASE})
        discovery_s = time.perf_counter() - started
        native_type = "heuristics_net"
        reps["heuristics_net"] = serialize.heuristics_net(hn)
        report("run.stage.converting")
        net, im, fm = pm4py.convert_to_petri_net(hn)
        reps["petri_net"] = serialize.petri_net(net, im, fm)
        reps["petri_net"]["derived_from"] = "heuristics_net"
        warnings.append({"key": "algo.warn.heuristics_petri"})
    elif aid in ("alpha", "alpha_plus", "ilp", "genetic"):
        if aid == "alpha":
            net, im, fm = pm4py.discover_petri_net_alpha(log, **KEYS)
        elif aid == "alpha_plus":
            net, im, fm = pm4py.discover_petri_net_alpha_plus(log, **KEYS)
        elif aid == "ilp":
            net, im, fm = pm4py.discover_petri_net_ilp(log, alpha=params["alpha"], **KEYS)
        else:
            random.seed(params["seed"])
            net, im, fm = pm4py.discover_petri_net_genetic(
                log, population_size=params["population_size"], elitism_rate=params["elitism_rate"],
                crossover_rate=params["crossover_rate"], mutation_rate=params["mutation_rate"],
                generations=params["generations"], elitism_min_sample=params["elitism_min_sample"], **KEYS)
        discovery_s = time.perf_counter() - started
        native_type = "petri_net"
        reps["petri_net"] = serialize.petri_net(net, im, fm)
    elif aid == "powl":
        from pm4py.algo.discovery.powl.inductive.variants.powl_discovery_varaints import POWLDiscoveryVariant

        model = pm4py.discover_powl(
            log, variant=POWLDiscoveryVariant[params["variant"]],
            filtering_weight_factor=params["filtering_weight_factor"],
            order_graph_filtering_threshold=params.get("order_graph_filtering_threshold"), **KEYS)
        discovery_s = time.perf_counter() - started
        native_type = "powl"
        reps["powl"] = serialize.powl(model)
        report("run.stage.converting")
        net, im, fm = pm4py.convert_to_petri_net(model)
        reps["petri_net"] = serialize.petri_net(net, im, fm)
        reps["petri_net"]["derived_from"] = "powl"
    elif aid == "declare":
        kwargs = {"min_support_ratio": params["min_support_ratio"], "min_confidence_ratio": params["min_confidence_ratio"]}
        if params.get("allowed_templates"):
            kwargs["allowed_templates"] = set(params["allowed_templates"])
        if params.get("considered_activities"):
            kwargs["considered_activities"] = set(params["considered_activities"])
        model = pm4py.discover_declare(log, **kwargs, **KEYS)
        discovery_s = time.perf_counter() - started
        native_type = DECLARE
        reps[DECLARE] = serialize.declare(model)
        if not reps[DECLARE]["constraints"]:
            warnings.append({"key": "algo.warn.no_constraints"})
    elif aid == "log_skeleton":
        model = pm4py.discover_log_skeleton(log, noise_threshold=params["noise_threshold"], **KEYS)
        discovery_s = time.perf_counter() - started
        native_type = LOG_SKELETON
        reps[LOG_SKELETON] = serialize.log_skeleton(model)
    elif aid == "transition_system":
        ts = pm4py.discover_transition_system(log, direction=params["direction"], window=params["window"],
                                              view=params["view"], **KEYS)
        discovery_s = time.perf_counter() - started
        native_type = "transition_system"
        reps["transition_system"] = serialize.transition_system(
            ts, {"direction": params["direction"], "window": params["window"], "view": params["view"]})
    elif aid == "temporal_profile":
        model = pm4py.discover_temporal_profile(_time_log(log), **KEYS)
        discovery_s = time.perf_counter() - started
        native_type = TEMPORAL_PROFILE
        reps[TEMPORAL_PROFILE] = serialize.temporal_profile(model)
    else:
        raise TaskError("algo.err.unknown_algorithm", algorithm=aid)

    report("run.stage.serializing")
    if net is not None:
        artifacts["model.pnml"] = _write(pm4py.write_pnml, ".pnml", net, im, fm)
    for rtype in (DECLARE, LOG_SKELETON, TEMPORAL_PROFILE, "transition_system"):
        if rtype in reps:
            artifacts["model.json"] = json.dumps({"processlens_model": rtype, "format_note": "application-specific JSON",
                                                  "model": reps[rtype]}, ensure_ascii=False)
    sizes = {rtype: serialize.size_of(rep) for rtype, rep in reps.items()}
    return {"native_type": native_type, "representations": reps, "sizes": sizes, "warnings": warnings,
            "artifacts": artifacts, "discovery_seconds": round(discovery_s, 4),
            "log_summary": {"cases": int(log[CASE].nunique()), "events": int(len(log)),
                            "activities": int(log[ACT].nunique())}}


# =========================================================================== evaluation
def _status(status: str, **extra) -> dict:
    return {"status": status, **extra}


def _petri_from(payload: dict):
    import pm4py

    pnml = payload.get("pnml")
    if not pnml:
        return None
    net, im, fm = _read(pm4py.read_pnml, ".pnml", pnml)
    return net, im, fm


def _is_workflow_net(net, im, fm) -> bool:
    if len(im) != 1 or len(fm) != 1:
        return False
    (src,), (snk,) = list(im.keys()), list(fm.keys())
    return im[src] == 1 and fm[snk] == 1 and not src.in_arcs and not snk.out_arcs


def evaluate(payload: dict, report: Report) -> dict:
    """Evaluate one model against a log. Each method yields its own explicit status."""
    import pm4py

    log: pd.DataFrame = payload["log"]
    methods: list[str] = payload["methods"]
    limits: dict = payload.get("limits", {})
    model_type = payload["model_type"]
    results: dict = {}
    case_order = list(pd.unique(log[CASE])) if not log.empty else []
    if log.empty:
        return {m: _status("not_evaluable", reason_key="conf.err.empty_log") for m in methods}

    pn = _petri_from(payload) if payload.get("pnml") else None
    for i, method in enumerate(methods):
        report("run.stage.evaluating", fraction=i / max(len(methods), 1), method=method)
        started = time.perf_counter()
        try:
            if method in (TOKEN_REPLAY, ALIGNMENTS, SOUNDNESS):
                if pn is None:
                    results[method] = _status("unsupported", reason_key="conf.err.needs_petri_net")
                    continue
                net, im, fm = pn
                if len(im) == 0 or len(fm) == 0:
                    results[method] = _status("not_evaluable", reason_key="conf.err.no_markings")
                    continue
                if method == TOKEN_REPLAY:
                    results[method] = _token_replay(pm4py, log, net, im, fm, case_order, limits)
                elif method == ALIGNMENTS:
                    results[method] = _alignments(pm4py, log, net, im, fm, limits, report)
                else:
                    if not _is_workflow_net(net, im, fm):
                        results[method] = _status("not_evaluable", reason_key="conf.err.not_workflow_net")
                        continue
                    sound, diag = pm4py.check_soundness(net, im, fm)
                    results[method] = _status("computed", sound=bool(sound),
                                              diagnostics=_safe_soundness_diag(diag))
            elif method == DECLARE_CONF:
                if model_type != DECLARE:
                    results[method] = _status("unsupported", reason_key="conf.err.wrong_model_type")
                    continue
                model = serialize.declare_to_native(payload["model"])
                out = pm4py.conformance_declare(log, model, **KEYS)
                results[method] = _declarative_result(out, case_order)
            elif method == LOG_SKELETON_CONF:
                if model_type != LOG_SKELETON:
                    results[method] = _status("unsupported", reason_key="conf.err.wrong_model_type")
                    continue
                model = serialize.log_skeleton_to_native(payload["model"])
                out = pm4py.conformance_log_skeleton(log, model, **KEYS)
                results[method] = _declarative_result(out, case_order)
            elif method == TEMPORAL_CONF:
                if model_type != TEMPORAL_PROFILE:
                    results[method] = _status("unsupported", reason_key="conf.err.wrong_model_type")
                    continue
                zeta = float(limits.get("zeta", 2.0))
                model = serialize.temporal_profile_to_native(payload["model"])
                out = pm4py.conformance_temporal_profile(_time_log(log), model, zeta=zeta, **KEYS)
                cases = []
                for cid, devs in zip(case_order, out):
                    cases.append({"case_id": cid, "is_fit": not devs, "deviation_count": len(devs),
                                  "deviations": [{"source": a, "target": b, "seconds": float(d), "z": round(float(z), 3)}
                                                 for a, b, d, z in devs][:50]})
                fitting = sum(1 for c in cases if c["is_fit"])
                results[method] = _status("computed", zeta=zeta, cases=cases,
                                          metrics={"cases": len(cases), "cases_without_deviation": fitting,
                                                   "share_without_deviation": fitting / len(cases)})
            else:
                results[method] = _status("unsupported", reason_key="conf.err.unknown_method")
        except TaskError as exc:
            results[method] = _status("failed", reason_key=exc.key, reason_params=exc.params)
        except Exception as exc:  # algorithm-specific failure, reported per method
            results[method] = _status("failed", reason_key="conf.err.exception",
                                      reason_params={"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        results[method]["seconds"] = round(time.perf_counter() - started, 4)
    return results


def _safe_soundness_diag(diag: dict) -> dict:
    out = {}
    for k, v in (diag or {}).items():
        key = str(getattr(k, "value", k))
        if isinstance(v, (bool, int, float, str)) or v is None:
            out[key] = v
        else:
            out[key] = str(v)[:300]
    return out


def _token_replay(pm4py, log, net, im, fm, case_order, limits) -> dict:
    diag = pm4py.conformance_diagnostics_token_based_replay(log, net, im, fm, **KEYS)
    cases = []
    for cid, d in zip(case_order, diag):
        cases.append({"case_id": cid, "is_fit": bool(d["trace_is_fit"]), "fitness": float(d["trace_fitness"]),
                      "missing": int(d["missing_tokens"]), "remaining": int(d["remaining_tokens"]),
                      "consumed": int(d["consumed_tokens"]), "produced": int(d["produced_tokens"]),
                      "problem_transitions": sorted({str(t.label or "τ") for t in d.get("transitions_with_problems", [])})})
    fit = pm4py.fitness_token_based_replay(log, net, im, fm, **KEYS)
    metrics = {"log_fitness": float(fit["log_fitness"]), "average_trace_fitness": float(fit["average_trace_fitness"]),
               "percentage_of_fitting_traces": float(fit["percentage_of_fitting_traces"])}
    precision: dict
    if limits.get("precision", True):
        precision = _status("computed", value=float(pm4py.precision_token_based_replay(log, net, im, fm, **KEYS)))
    else:
        precision = _status("not_evaluable", reason_key="conf.err.precision_disabled")
    return _status("computed", metrics=metrics, precision=precision, cases=cases)


def _alignments(pm4py, log, net, im, fm, limits, report) -> dict:
    """Optimal alignments, computed once per variant and mapped back to every case."""
    from pm4py.algo.evaluation.replay_fitness.variants import alignment_based

    seqs = log.groupby(CASE, sort=False)[ACT].agg(tuple)
    variants = list(dict.fromkeys(seqs.tolist()))
    max_variants = int(limits.get("max_variants", 2000))
    if len(variants) > max_variants:
        return _status("not_evaluable", reason_key="conf.err.variant_limit",
                       reason_params={"variants": len(variants), "limit": max_variants})
    rows = []
    for i, v in enumerate(variants):
        for j, a in enumerate(v):
            rows.append({CASE: f"v{i}", ACT: a, TS: pd.Timestamp("2000-01-01", tz="UTC") + pd.Timedelta(seconds=len(rows))})
    vlog = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[CASE, ACT, TS])
    aligned: list = []
    chunk = 25
    for start in range(0, len(variants), chunk):
        ids = {f"v{i}" for i in range(start, min(start + chunk, len(variants)))}
        part = vlog[vlog[CASE].isin(ids)]
        aligned.extend(pm4py.conformance_diagnostics_alignments(part, net, im, fm, **KEYS))
        report("run.stage.aligning", fraction=min(1.0, (start + chunk) / len(variants)),
               done=min(start + chunk, len(variants)), total=len(variants))
    by_variant = dict(zip(variants, aligned))
    cases, per_case_alignments = [], []
    for cid, seq in seqs.items():
        a = by_variant[seq]
        per_case_alignments.append(a)
        moves = []
        for log_move, model_move in a["alignment"] or []:
            if log_move == ">>" and model_move is not None:
                moves.append({"type": "model_move", "activity": model_move})
            elif model_move == ">>":
                moves.append({"type": "log_move", "activity": log_move})
        cases.append({"case_id": cid, "is_fit": a["fitness"] == 1.0, "fitness": float(a["fitness"]),
                      "cost": int(a["cost"]), "deviations": moves[:50]})
    fitness = alignment_based.evaluate(per_case_alignments)
    metrics = {"log_fitness": float(fitness["log_fitness"]), "average_trace_fitness": float(fitness["average_trace_fitness"]),
               "percentage_of_fitting_traces": float(fitness["percentage_of_fitting_traces"]),
               "variants_aligned": len(variants)}
    if limits.get("precision", True):
        report("run.stage.precision")
        precision = _status("computed", value=float(pm4py.precision_alignments(log, net, im, fm, **KEYS)))
    else:
        precision = _status("not_evaluable", reason_key="conf.err.precision_disabled")
    return _status("computed", metrics=metrics, precision=precision, cases=cases)


def _declarative_result(out: list, case_order: list) -> dict:
    cases = []
    for cid, d in zip(case_order, out):
        devs = [_dev_text(x) for x in d.get("deviations", [])][:50]
        cases.append({"case_id": cid, "is_fit": bool(d["is_fit"]), "fitness": float(d["dev_fitness"]),
                      "deviation_count": int(d["no_dev_total"]), "constraints": int(d["no_constr_total"]),
                      "deviations": devs})
    n = len(cases)
    fitting = sum(1 for c in cases if c["is_fit"])
    avg = sum(c["fitness"] for c in cases) / n if n else None
    return _status("computed", cases=cases, metrics={"cases": n, "fitting_cases": fitting,
                                                     "share_fitting": fitting / n if n else None,
                                                     "average_fitness": avg})


def _dev_text(x) -> str:
    if isinstance(x, (tuple, list)):
        return " ".join(_dev_text(y) for y in x)
    return str(x)


# =========================================================================== model import
def parse_model(payload: dict, report: Report) -> dict:
    """Parse an uploaded reference model (PNML or PTML) and return its representation."""
    import pm4py

    fmt = payload["format"]
    content = payload["content"]
    report("run.stage.parsing")
    if fmt == "pnml":
        net, im, fm = _read(pm4py.read_pnml, ".pnml", content)
        rep = serialize.petri_net(net, im, fm)
        return {"model_type": "petri_net", "representation": rep, "pnml": _write(pm4py.write_pnml, ".pnml", net, im, fm)}
    if fmt == "ptml":
        tree = _read(pm4py.read_ptml, ".ptml", content)
        net, im, fm = pm4py.convert_to_petri_net(tree)
        return {"model_type": "process_tree", "representation": serialize.process_tree(tree),
                "pnml": _write(pm4py.write_pnml, ".pnml", net, im, fm)}
    raise TaskError("ref.err.unsupported_format", format=fmt)


# =========================================================================== diagnostics task
def sleep(payload: dict, report: Report) -> dict:
    """Test-only task used to exercise cancellation and timeouts."""
    seconds = float(payload.get("seconds", 1))
    end = time.time() + seconds
    while time.time() < end:
        report("run.stage.waiting", fraction=1 - (end - time.time()) / seconds)
        time.sleep(0.05)
    return {"slept": seconds}


# =========================================================================== object-centric Petri net
def ocpn(payload: dict, report: Report) -> dict:
    """Object-centric Petri net (van der Aalst & Berti) via PM4Py's verified implementation.

    The OCEL is passed as unique events, objects and event-object relations;
    shared events stay single events (PM4Py flattens per object type internally)."""
    report("run.stage.loading_library")
    import pm4py
    from pm4py.objects.ocel.obj import OCEL

    ev = payload["events"].rename(columns={"event_id": "ocel:eid", "event_type": "ocel:activity", "ts": "ocel:timestamp"})
    ob = payload["objects"].rename(columns={"object_id": "ocel:oid", "object_type": "ocel:type"})
    rel = payload["relations"].rename(columns={"event_id": "ocel:eid", "object_id": "ocel:oid", "qualifier": "ocel:qualifier",
                                               "object_type": "ocel:type", "event_type": "ocel:activity",
                                               "ts": "ocel:timestamp"})
    if ev.empty or rel.empty:
        raise TaskError("ocel.err.empty_selection")
    log_ = OCEL(events=ev, objects=ob, relations=rel)
    params = payload["params"]
    report("run.stage.discovering")
    t0 = time.perf_counter()
    net = pm4py.discover_oc_petri_net(log_, inductive_miner_variant=params.get("inductive_miner_variant", "im"),
                                      noise_threshold=float(params.get("noise_threshold") or 0.0))
    seconds = time.perf_counter() - t0
    report("run.stage.serializing")
    rep = serialize.oc_petri_net(net)
    return {"native_type": "oc_petri_net", "discovery_seconds": round(seconds, 4), "sizes": serialize.ocpn_size(rep),
            "unique_events": int(len(ev)), "objects": int(len(ob)), "e2o_relationships": int(len(rel)),
            "representation": rep}


TASKS = {"discover": discover, "evaluate": evaluate, "parse_model": parse_model, "sleep": sleep, "ocpn": ocpn}
