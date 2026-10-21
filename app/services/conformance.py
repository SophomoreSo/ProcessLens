"""Rule-based conformance checking (not Petri-net replay or alignments).

Each rule yields, per case, one of ``compliant``, ``violating`` or
``not_evaluable`` plus a plain-language explanation. A result is reported as
final only when later events could not change it. For cases that are not
known to be complete (status ``incomplete``/``unknown``) a result that could
still change is ``not_evaluable`` unless the rule sets ``assume_complete``.

Rule definitions:

required_activity(activity)
    occurs >= 1 -> compliant; absent & complete -> violating; absent & open -> not_evaluable.
forbidden_activity(activity)
    occurs >= 1 -> violating; absent & complete -> compliant; absent & open -> not_evaluable.
precedence(activity_a, activity_b)  "A must precede B"
    Each occurrence of B needs an occurrence of A with a strictly earlier
    timestamp. If the only candidate A shares B's timestamp, order cannot be
    established -> not_evaluable. Any B without an earlier/tied A -> violating.
    No B in a complete case -> compliant (vacuously). No B in an open case ->
    compliant if A already occurred (any future B follows it), else not_evaluable.
max_occurrences(activity, max_count)
    count > max -> violating; else complete -> compliant; else not_evaluable.
max_duration(max_hours, metric)
    completed_throughput: completed cases compared; other cases not_evaluable.
    observed_duration: every case compared on its observed duration as recorded
    in the data (for open cases this is the duration so far).
start_activity(activities)
    first event's activity in the set -> compliant else violating. If several
    events share the first timestamp and only some are in the set ->
    not_evaluable (their order is unreliable).
end_activity(activities)
    open case -> not_evaluable; otherwise like start_activity on the last timestamp.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from app.i18n import join_or, msg, t
from app.models import ConformanceRule
from app.services.cache import cache
from app.services.eventlog import EventLog

COMPLIANT, VIOLATING, NOT_EVALUABLE = "compliant", "violating", "not_evaluable"
Explained = tuple[str, str, dict]  # (result, message key, message params)


def describe_rule(rule_type: str, p: dict, lang: str = "en") -> str:
    if rule_type == "required_activity":
        return t(lang, "rule.desc.required_activity", activity=p["activity"])
    if rule_type == "forbidden_activity":
        return t(lang, "rule.desc.forbidden_activity", activity=p["activity"])
    if rule_type == "precedence":
        return t(lang, "rule.desc.precedence", a=p["activity_a"], b=p["activity_b"])
    if rule_type == "max_duration":
        key = "rule.desc.max_throughput" if p["metric"] == "completed_throughput" else "rule.desc.max_observed"
        return t(lang, key, hours=float(p["max_hours"]))
    if rule_type == "max_occurrences":
        return t(lang, "rule.desc.max_occurrences", activity=p["activity"], max=p["max_count"])
    if rule_type == "start_activity":
        return t(lang, "rule.desc.start_activity", activities=join_or(lang, p["activities"]))
    if rule_type == "end_activity":
        return t(lang, "rule.desc.end_activity", activities=join_or(lang, p["activities"]))
    return rule_type


def _h(seconds: float) -> str:
    return f"{seconds / 3600:.1f}"


def _boundary_check(acts: np.ndarray, ts: np.ndarray, allowed: set[str], at_end: bool) -> Explained:
    side = "end" if at_end else "start"
    edge_ts = ts[-1] if at_end else ts[0]
    tied = acts[ts == edge_ts]
    if len(tied) > 1:
        names = ", ".join(map(str, tied))
        inside = [a in allowed for a in tied]
        if all(inside):
            return COMPLIANT, f"conf.{side}.tie_ok", {"count": len(tied), "activities": names}
        if not any(inside):
            return VIOLATING, f"conf.{side}.tie_bad", {"activities": names}
        return NOT_EVALUABLE, f"conf.{side}.tie_mixed", {"count": len(tied), "activities": names}
    act = str(acts[-1] if at_end else acts[0])
    if act in allowed:
        return COMPLIANT, f"conf.{side}.ok", {"activity": act}
    return VIOLATING, f"conf.{side}.bad", {"activity": act}


def _precedence(acts: np.ndarray, ts: np.ndarray, a: str, b: str, complete: bool) -> Explained:
    a_ts = ts[acts == a]
    b_idx = np.flatnonzero(acts == b)
    if len(b_idx) == 0:
        if complete:
            return COMPLIANT, "conf.prec.vacuous", {"b": b}
        if len(a_ts):
            return COMPLIANT, "conf.prec.a_done", {"a": a, "b": b}
        return NOT_EVALUABLE, "conf.prec.none_yet", {"a": a, "b": b}
    uncertain = 0
    for n, i in enumerate(b_idx, start=1):
        tb = ts[i]
        if (a_ts < tb).any():
            continue
        if (a_ts == tb).any():
            uncertain += 1
            continue
        return VIOLATING, "conf.prec.violation", {"a": a, "b": b, "occurrence": n}
    if uncertain:
        return NOT_EVALUABLE, "conf.prec.tie", {"a": a, "b": b, "count": uncertain}
    return COMPLIANT, "conf.prec.ok", {"a": a, "b": b, "count": len(b_idx)}


def evaluate(log: EventLog, rule_type: str, params: dict) -> pd.DataFrame:
    """Evaluate a rule over every case. Columns: result, key, params (a translatable explanation)."""
    cases, ev = log.cases, log.events
    n = len(cases)
    results = np.empty(n, dtype=object)
    keys = np.empty(n, dtype=object)
    margs = np.empty(n, dtype=object)
    status = cases["status"].to_numpy()
    assume = bool(params.get("assume_complete", False))
    complete = (status == "completed") | assume

    def out() -> pd.DataFrame:
        return pd.DataFrame({"result": results, "key": keys, "params": margs}, index=cases.index)

    if rule_type == "max_duration":
        limit = params["max_hours"] * 3600
        dur = cases["duration_s"].to_numpy()
        for i in range(n):
            if params["metric"] == "completed_throughput" and status[i] != "completed":
                results[i], keys[i], margs[i] = NOT_EVALUABLE, "conf.dur.not_completed", {"status": msg(f"status.{status[i]}")}
                continue
            over = dur[i] > limit
            suffix = "" if status[i] == "completed" else "_open"
            results[i] = VIOLATING if over else COMPLIANT
            keys[i] = f"conf.dur.{'over' if over else 'within'}{suffix}"
            margs[i] = {"hours": _h(dur[i]), "limit": _h(limit)}
        return out()

    acts_all = ev["activity"].to_numpy()
    ts_all = ev["ts"].to_numpy()
    counts = cases["n_events"].to_numpy()
    bounds = np.concatenate([[0], np.cumsum(counts)])

    for i in range(n):
        lo, hi = bounds[i], bounds[i + 1]
        acts, ts = acts_all[lo:hi], ts_all[lo:hi]
        done = bool(complete[i])
        if rule_type in ("required_activity", "forbidden_activity", "max_occurrences"):
            act = params["activity"]
            k = int((acts == act).sum())
            p = {"activity": act, "count": k}
            if rule_type == "required_activity":
                r, key = (COMPLIANT, "conf.req.present") if k else (
                    (VIOLATING, "conf.req.absent") if done else (NOT_EVALUABLE, "conf.req.absent_open"))
            elif rule_type == "forbidden_activity":
                r, key = (VIOLATING, "conf.forb.present") if k else (
                    (COMPLIANT, "conf.forb.absent") if done else (NOT_EVALUABLE, "conf.forb.absent_open"))
            else:
                p["max"] = params["max_count"]
                r, key = (VIOLATING, "conf.maxocc.over") if k > params["max_count"] else (
                    (COMPLIANT, "conf.maxocc.ok") if done else (NOT_EVALUABLE, "conf.maxocc.open"))
        elif rule_type == "precedence":
            r, key, p = _precedence(acts, ts, params["activity_a"], params["activity_b"], done)
        elif rule_type == "start_activity":
            r, key, p = _boundary_check(acts, ts, set(params["activities"]), False)
        elif rule_type == "end_activity":
            if not done:
                r, key, p = NOT_EVALUABLE, "conf.end.open", {}
            else:
                r, key, p = _boundary_check(acts, ts, set(params["activities"]), True)
        else:
            raise ValueError(f"Unknown rule type {rule_type!r}")
        results[i], keys[i], margs[i] = r, key, p
    return out()


def explain(row, lang: str) -> str:
    """Render the explanation of one evaluation row."""
    return t(lang, row["key"], **(row["params"] or {}))


def evaluate_rule(log: EventLog, rule: ConformanceRule) -> pd.DataFrame:
    key = (*log.key, "conformance", rule.id, rule.revision)
    return cache.get_or_compute(key, lambda: evaluate(log, rule.rule_type, rule.params))


def lookup_factory(log: EventLog, get_rule: Callable[[int], ConformanceRule | None]):
    """Build the ConformanceLookup used by filtering."""

    def lookup(rule_id: int):
        rule = get_rule(rule_id)
        if rule is None:
            raise LookupError(f"Conformance rule {rule_id} not found for this dataset")
        return rule.revision, evaluate_rule(log, rule)["result"]

    return lookup


def summarize(results: pd.DataFrame, case_index: pd.Index) -> dict:
    sub = results.reindex(case_index)
    vc = sub["result"].value_counts()
    return {
        "population_case_count": len(case_index),
        "compliant": int(vc.get(COMPLIANT, 0)),
        "violating": int(vc.get(VIOLATING, 0)),
        "not_evaluable": int(vc.get(NOT_EVALUABLE, 0)),
    }
