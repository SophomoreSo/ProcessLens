"""Rule-based conformance semantics, including 'not evaluable' cases."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.conformance import evaluate
from app.services.eventlog import build_event_log

T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def make_log(traces: dict[str, list], status: dict[str, str] | None = None):
    """traces: case -> list of activity or (activity, hour_offset). status: case -> Completed/Open."""
    rows = []
    row = 0
    for case, events in traces.items():
        for seq, ev in enumerate(events):
            act, hour = (ev, seq) if isinstance(ev, str) else ev
            row += 1
            rows.append({"case_id": case, "activity": act, "ts": T0 + pd.Timedelta(hours=hour), "start_ts": pd.NaT,
                         "resource": None, "department": None, "cost": float("nan"),
                         "case_status": (status or {}).get(case, "Completed"), "event_id": None,
                         "source_row": row, "seq": seq})
    df = pd.DataFrame(rows)
    df["ts"] = df["ts"].astype("datetime64[us, UTC]")
    df["start_ts"] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[us, UTC]")
    ds = SimpleNamespace(id=1, version=1, settings_revision=1, completion_mode="case_status",
                         completion_activities=[], completed_status_values=["Completed"],
                         open_status_values=["Open"], reference_time=None, display_timezone="UTC")
    return build_event_log(ds, df)


def results(log, rule_type, **params) -> dict[str, str]:
    return evaluate(log, rule_type, params)["result"].to_dict()


def test_required_activity():
    log = make_log({"ok": ["A", "B"], "miss": ["A"], "open": ["A"]}, {"open": "Open"})
    r = results(log, "required_activity", activity="B")
    assert r == {"ok": "compliant", "miss": "violating", "open": "not_evaluable"}
    r2 = results(log, "required_activity", activity="B", assume_complete=True)
    assert r2["open"] == "violating"


def test_forbidden_activity():
    log = make_log({"bad": ["A", "X"], "fine": ["A"], "open": ["A"], "open_bad": ["X"]},
                   {"open": "Open", "open_bad": "Open"})
    r = results(log, "forbidden_activity", activity="X")
    assert r == {"bad": "violating", "fine": "compliant", "open": "not_evaluable", "open_bad": "violating"}


def test_precedence_including_ties_and_repeats():
    log = make_log({
        "ok": ["A", "B"],
        "violate": ["B", "A"],
        "repeat_ok": ["A", "B", "B"],
        "second_b_ok": ["A", "B", "A", "B"],
        "no_b": ["A", "C"],
        "tie": [("A", 1), ("B", 1)],             # same timestamp: order unknown
        "tie_b_first": [("B", 1), ("A", 1)],     # still a tie regardless of row order
        "open_no_a": ["C"],
        "open_with_a": ["A"],
    }, {"open_no_a": "Open", "open_with_a": "Open"})
    r = results(log, "precedence", activity_a="A", activity_b="B")
    assert r == {"ok": "compliant", "violate": "violating", "repeat_ok": "compliant", "second_b_ok": "compliant",
                 "no_b": "compliant", "tie": "not_evaluable", "tie_b_first": "not_evaluable",
                 "open_no_a": "not_evaluable", "open_with_a": "compliant"}


def test_max_occurrences():
    log = make_log({"once": ["A", "R"], "twice": ["A", "R", "R"], "none": ["A"], "open": ["R"]}, {"open": "Open"})
    r = results(log, "max_occurrences", activity="R", max_count=1)
    assert r == {"once": "compliant", "twice": "violating", "none": "compliant", "open": "not_evaluable"}


def test_max_duration_metrics():
    log = make_log({"fast": [("A", 0), ("B", 2)], "slow": [("A", 0), ("B", 10)], "open_slow": [("A", 0), ("B", 10)],
                    "open_fast": [("A", 0), ("B", 1)]}, {"open_slow": "Open", "open_fast": "Open"})
    thr = results(log, "max_duration", max_hours=5, metric="completed_throughput")
    assert thr == {"fast": "compliant", "slow": "violating", "open_slow": "not_evaluable", "open_fast": "not_evaluable"}
    obs = results(log, "max_duration", max_hours=5, metric="observed_duration")
    assert obs == {"fast": "compliant", "slow": "violating", "open_slow": "violating", "open_fast": "compliant"}
    expl = evaluate(log, "max_duration", {"max_hours": 5, "metric": "observed_duration"})["explanation"]
    assert "not known to be complete" in expl["open_fast"]


def test_start_and_end_activity_with_ties():
    log = make_log({
        "good": ["S", "X", "E"],
        "bad_start": ["X", "E"],
        "tie_mixed": [("S", 0), ("X", 0), ("E", 1)],
        "tie_all_ok": [("S", 0), ("S", 0), ("E", 1)],
        "single": ["S"],
        "open": ["S", "X"],
    }, {"open": "Open"})
    start = results(log, "start_activity", activities=["S"])
    assert start == {"good": "compliant", "bad_start": "violating", "tie_mixed": "not_evaluable",
                     "tie_all_ok": "compliant", "single": "compliant", "open": "compliant"}
    end = results(log, "end_activity", activities=["E"])
    assert end == {"good": "compliant", "bad_start": "compliant", "tie_mixed": "compliant", "tie_all_ok": "compliant",
                   "single": "violating", "open": "not_evaluable"}


def test_unknown_rule_type_raises():
    log = make_log({"a": ["A"]})
    with pytest.raises(ValueError):
        evaluate(log, "nonsense", {})


def test_empty_log():
    log = make_log({"a": ["A"]})
    log.cases = log.cases.iloc[0:0]
    log.events = log.events.iloc[0:0]
    assert evaluate(log, "required_activity", {"activity": "A"}).empty


def test_conformance_api_crud_and_evaluation(client, hand_dataset):
    base = f"/api/datasets/{hand_dataset}/conformance-rules"
    r = client.post(base, json={"name": "B required", "rule_type": "required_activity", "params": {"activity": "B"}})
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["description"] == "'B' must occur"
    ev = client.get(f"{base}/{rule['id']}/evaluation").json()
    # C1-C3 contain B; C4 is open (not evaluable); 007 has unknown status (not evaluable)
    assert ev["summary"] == {"population_case_count": 5, "compliant": 3, "violating": 0, "not_evaluable": 2}
    assert ev["cases"]["items"][0]["result"] == "not_evaluable"
    # invalid params rejected with a uniform error body
    bad = client.post(base, json={"name": "x", "rule_type": "precedence", "params": {"activity_a": "A"}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    # update bumps revision and changes results (cache must not serve stale results)
    up = client.put(f"{base}/{rule['id']}", json={"name": "B required", "rule_type": "required_activity",
                                                  "params": {"activity": "B", "assume_complete": True}})
    assert up.json()["revision"] == 2
    ev2 = client.get(f"{base}/{rule['id']}/evaluation").json()
    assert ev2["summary"]["violating"] == 2 and ev2["summary"]["not_evaluable"] == 0
    # conformance result as a case filter
    o = client.get(f"/api/datasets/{hand_dataset}/overview",
                   params={"conformance_rule": rule["id"], "conformance_result": "violating"}).json()
    assert o["population"]["case_count"] == 2
    assert client.delete(f"{base}/{rule['id']}").status_code == 204
    assert client.get(f"{base}/{rule['id']}").status_code == 404
