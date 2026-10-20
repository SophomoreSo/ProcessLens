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

from app.models import ConformanceRule
from app.services.cache import cache
from app.services.eventlog import EventLog

COMPLIANT, VIOLATING, NOT_EVALUABLE = "compliant", "violating", "not_evaluable"


def describe_rule(rule_type: str, p: dict) -> str:
    if rule_type == "required_activity":
        return f"'{p['activity']}' must occur"
    if rule_type == "forbidden_activity":
        return f"'{p['activity']}' must not occur"
    if rule_type == "precedence":
        return f"'{p['activity_a']}' must precede every '{p['activity_b']}'"
    if rule_type == "max_duration":
        label = "completed throughput" if p["metric"] == "completed_throughput" else "observed duration"
        return f"{label} must be at most {p['max_hours']:g} h"
    if rule_type == "max_occurrences":
        return f"'{p['activity']}' may occur at most {p['max_count']} time(s)"
    if rule_type == "start_activity":
        return "case must start with " + " or ".join(f"'{a}'" for a in p["activities"])
    if rule_type == "end_activity":
        return "case must end with " + " or ".join(f"'{a}'" for a in p["activities"])
    return rule_type


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h"


def _boundary_check(acts: np.ndarray, ts: np.ndarray, allowed: set[str], at_end: bool, what: str) -> tuple[str, str]:
    edge_ts = ts[-1] if at_end else ts[0]
    tied = acts[ts == edge_ts]
    if len(tied) > 1:
        inside = [a in allowed for a in tied]
        if all(inside):
            return COMPLIANT, f"All {len(tied)} events at the {what} timestamp are allowed ({', '.join(map(str, tied))})."
        if not any(inside):
            return VIOLATING, f"The {what} event(s) {', '.join(map(str, tied))} are not allowed."
        return NOT_EVALUABLE, (f"{len(tied)} events share the {what} timestamp ({', '.join(map(str, tied))}); "
                               "their order cannot be established.")
    act = acts[-1] if at_end else acts[0]
    if act in allowed:
        return COMPLIANT, f"The case {'ends' if at_end else 'starts'} with '{act}'."
    return VIOLATING, f"The case {'ends' if at_end else 'starts'} with '{act}'."


def _precedence(acts: np.ndarray, ts: np.ndarray, a: str, b: str, complete: bool) -> tuple[str, str]:
    a_ts = ts[acts == a]
    b_idx = np.flatnonzero(acts == b)
    if len(b_idx) == 0:
        if complete:
            return COMPLIANT, f"'{b}' does not occur (rule vacuously satisfied)."
        if len(a_ts):
            return COMPLIANT, f"'{a}' already occurred and '{b}' has not occurred yet."
        return NOT_EVALUABLE, f"Neither '{a}' nor '{b}' occurred yet and the case is not known to be complete."
    uncertain = 0
    for i in b_idx:
        tb = ts[i]
        if (a_ts < tb).any():
            continue
        if (a_ts == tb).any():
            uncertain += 1
            continue
        return VIOLATING, f"'{b}' (occurrence {list(b_idx).index(i) + 1}) has no earlier '{a}'."
    if uncertain:
        return NOT_EVALUABLE, (f"{uncertain} occurrence(s) of '{b}' share a timestamp with '{a}'; "
                               "order cannot be established.")
    return COMPLIANT, f"Every '{b}' ({len(b_idx)}) is preceded by '{a}'."


def evaluate(log: EventLog, rule_type: str, params: dict) -> pd.DataFrame:
    """Evaluate a rule over every case of the log. Returns columns result, explanation."""
    cases, ev = log.cases, log.events
    n = len(cases)
    results = np.empty(n, dtype=object)
    expl = np.empty(n, dtype=object)
    status = cases["status"].to_numpy()
    assume = bool(params.get("assume_complete", False))
    complete = (status == "completed") | assume
    open_note = "the case is not known to be complete"

    if rule_type == "max_duration":
        limit = params["max_hours"] * 3600
        dur = cases["duration_s"].to_numpy()
        for i in range(n):
            if params["metric"] == "completed_throughput" and status[i] != "completed":
                results[i], expl[i] = NOT_EVALUABLE, f"Throughput is defined only for completed cases ({status[i]})."
                continue
            over = dur[i] > limit
            suffix = "" if status[i] == "completed" else " (observed so far; case not known to be complete)"
            results[i] = VIOLATING if over else COMPLIANT
            expl[i] = f"{_hours(dur[i])} {'exceeds' if over else 'within'} the {_hours(limit)} limit{suffix}."
        return pd.DataFrame({"result": results, "explanation": expl}, index=cases.index)

    acts_all = ev["activity"].to_numpy()
    ts_all = ev["ts"].to_numpy()
    counts = cases["n_events"].to_numpy()
    bounds = np.concatenate([[0], np.cumsum(counts)])

    for i in range(n):
        lo, hi = bounds[i], bounds[i + 1]
        acts, ts = acts_all[lo:hi], ts_all[lo:hi]
        done = bool(complete[i])
        if rule_type == "required_activity":
            k = int((acts == params["activity"]).sum())
            if k:
                r, e = COMPLIANT, f"'{params['activity']}' occurs {k} time(s)."
            elif done:
                r, e = VIOLATING, f"'{params['activity']}' never occurs."
            else:
                r, e = NOT_EVALUABLE, f"'{params['activity']}' has not occurred yet and {open_note}."
        elif rule_type == "forbidden_activity":
            k = int((acts == params["activity"]).sum())
            if k:
                r, e = VIOLATING, f"'{params['activity']}' occurs {k} time(s)."
            elif done:
                r, e = COMPLIANT, f"'{params['activity']}' does not occur."
            else:
                r, e = NOT_EVALUABLE, f"'{params['activity']}' has not occurred so far but {open_note}."
        elif rule_type == "precedence":
            r, e = _precedence(acts, ts, params["activity_a"], params["activity_b"], done)
        elif rule_type == "max_occurrences":
            k = int((acts == params["activity"]).sum())
            mx = params["max_count"]
            if k > mx:
                r, e = VIOLATING, f"'{params['activity']}' occurs {k} time(s); maximum is {mx}."
            elif done:
                r, e = COMPLIANT, f"'{params['activity']}' occurs {k} time(s); maximum is {mx}."
            else:
                r, e = NOT_EVALUABLE, f"'{params['activity']}' occurs {k} time(s) so far and {open_note}."
        elif rule_type == "start_activity":
            r, e = _boundary_check(acts, ts, set(params["activities"]), False, "first")
        elif rule_type == "end_activity":
            if not done:
                r, e = NOT_EVALUABLE, f"The end activity cannot be judged because {open_note}."
            else:
                r, e = _boundary_check(acts, ts, set(params["activities"]), True, "last")
        else:
            raise ValueError(f"Unknown rule type {rule_type!r}")
        results[i], expl[i] = r, e
    return pd.DataFrame({"result": results, "explanation": expl}, index=cases.index)


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
