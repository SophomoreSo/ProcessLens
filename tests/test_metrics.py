"""Exact metrics on the hand-checkable fixture (see tests/fixtures/README.md)."""

from __future__ import annotations

import pandas as pd
import pytest

from tests.conftest import HAND_CHECK


def get(client, ds, path, **params):
    r = client.get(f"/api/datasets/{ds}/{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_overview_counts(client, hand_dataset):
    d = get(client, hand_dataset, "overview")["data"]
    assert d["case_count"] == 5
    assert d["event_count"] == 16
    assert d["activity_count"] == 4
    assert d["variant_count"] == 4
    assert d["status_counts"] == {"completed": 3, "incomplete": 1, "unknown": 1}
    assert d["rework_case_count"] == 1
    assert d["repeated_activity_count"] == 1
    assert d["rework_rate"] == pytest.approx(0.2)
    assert d["total_cost"] == pytest.approx(73.0)


def test_duration_statistics(client, hand_dataset):
    d = get(client, hand_dataset, "overview")["data"]
    obs = d["observed_duration"]
    assert obs["n"] == 5
    assert obs["mean"] == pytest.approx(9360)
    assert obs["median"] == pytest.approx(7200)
    assert obs["p90"] == pytest.approx(18720)
    thr = d["completed_throughput"]
    assert thr["n"] == 3
    assert (thr["mean"], thr["median"]) == (pytest.approx(14400), pytest.approx(14400))
    assert thr["p90"] == pytest.approx(20160)
    age = d["open_case_age"]
    assert age["n"] == 2
    assert (age["min"], age["max"]) == (pytest.approx(57600), pytest.approx(144000))
    assert d["reference_time"] == "2024-01-06T00:00:00+00:00"
    assert d["reference_time_source"] == "configured"


def test_variants(client, hand_dataset):
    d = get(client, hand_dataset, "variants")["data"]
    assert d["total"] == 4
    items = d["items"]
    assert [v["activities"] for v in items] == [
        ["A", "B", "C", "D"], ["A"], ["A", "B", "B", "C", "D"], ["A", "C"],
    ]
    assert [v["case_count"] for v in items] == [2, 1, 1, 1]
    assert items[0]["share"] == pytest.approx(0.4)
    assert items[-1]["cumulative_share"] == pytest.approx(1.0)
    assert [v["has_rework"] for v in items] == [False, False, True, False]
    assert items[0]["observed_duration"]["median"] == pytest.approx(3 * 3600)  # cases of 4 h and 2 h


def edge_map(graph):
    return {(e["source"], e["target"]): e for e in graph["edges"]}


def test_directly_follows_edges(client, hand_dataset):
    g = get(client, hand_dataset, "graph")["data"]
    assert g["kind"] == "directly_follows_graph"
    edges = edge_map(g)
    expected = {("A", "B"): (3, 3), ("B", "B"): (1, 1), ("B", "C"): (3, 3), ("C", "D"): (3, 3), ("A", "C"): (1, 1),
                ("__start__", "A"): (5, 5), ("D", "__end__"): (3, 3), ("C", "__end__"): (1, 1),
                ("A", "__end__"): (1, 1)}
    assert {k: (v["occurrences"], v["case_count"]) for k, v in edges.items()} == expected
    ab = edges[("A", "B")]["elapsed"]
    assert ab["mean"] == pytest.approx(4200)
    assert ab["median"] == pytest.approx(3600)
    assert ab["p90"] == pytest.approx(6480)
    assert edges[("__start__", "A")]["elapsed"] is None
    nodes = {n["id"]: n for n in g["nodes"]}
    assert (nodes["B"]["occurrences"], nodes["B"]["case_count"]) == (4, 3)


def test_dfg_matches_pm4py(client, hand_dataset):
    """Cross-check our edge occurrence counts against PM4Py's DFG discovery."""
    import pm4py

    raw = pd.read_csv(HAND_CHECK, dtype=str, keep_default_na=False)
    df = pd.DataFrame({
        "case:concept:name": raw["case_id"],
        "concept:name": raw["activity"],
        "time:timestamp": pd.to_datetime(raw["timestamp"], utc=True),
    })
    dfg, starts, ends = pm4py.discover_dfg(df, case_id_key="case:concept:name", activity_key="concept:name",
                                            timestamp_key="time:timestamp")
    edges = edge_map(get(client, hand_dataset, "graph")["data"])
    ours = {k: v["occurrences"] for k, v in edges.items() if "__start__" not in k and "__end__" not in k}
    assert ours == dict(dfg)
    assert {k[1]: v["occurrences"] for k, v in edges.items() if k[0] == "__start__"} == dict(starts)
    assert {k[0]: v["occurrences"] for k, v in edges.items() if k[1] == "__end__"} == dict(ends)


def test_cases_table_and_detail(client, hand_dataset):
    d = get(client, hand_dataset, "cases", sort="duration", order="desc")["data"]
    assert d["total"] == 5
    assert [c["case_id"] for c in d["items"]] == ["C2", "C1", "C3", "C4", "007"]
    single = client.get(f"/api/datasets/{hand_dataset}/cases/007").json()
    assert single["case"]["n_events"] == 1
    assert single["case"]["observed_duration_s"] == 0
    assert single["case"]["status"] == "unknown"
    assert single["events"][0]["elapsed_since_previous_s"] is None
    c2 = client.get(f"/api/datasets/{hand_dataset}/cases/C2").json()
    assert [e["activity"] for e in c2["events"]] == ["A", "B", "B", "C", "D"]  # repeated activity kept
    assert c2["case"]["repeated_activities"] == 1
    assert client.get(f"/api/datasets/{hand_dataset}/cases/NOPE").status_code == 404


def test_performance_resources(client, hand_dataset):
    d = get(client, hand_dataset, "performance")["data"]
    res = {r["name"]: r for r in d["resources"]}
    assert res["r1"]["events"] == 5 and res["r1"]["cases"] == 5
    assert res["r1"]["elapsed_before_event"]["n"] == 0  # r1 only performs first events
    assert res["r4"]["cases"] == 1
    assert d["has_start_timestamps"] is False
    assert d["activity_execution"] == []


def test_completion_mode_none_means_unknown(client, hand_dataset):
    r = client.patch(f"/api/datasets/{hand_dataset}", json={"completion_mode": "none"})
    assert r.status_code == 200
    d = get(client, hand_dataset, "overview")["data"]
    assert d["status_counts"] == {"completed": 0, "incomplete": 0, "unknown": 5}
    assert d["completed_throughput"]["n"] == 0
    assert d["completed_throughput"]["median"] is None


def test_end_activity_completion(client, hand_dataset):
    r = client.patch(f"/api/datasets/{hand_dataset}", json={"completion_mode": "end_activity",
                                                            "completion_activities": ["D"]})
    assert r.status_code == 200
    d = get(client, hand_dataset, "overview")["data"]
    assert d["status_counts"] == {"completed": 3, "incomplete": 2, "unknown": 0}


def test_reference_time_defaults_to_latest_event(client, hand_dataset):
    client.patch(f"/api/datasets/{hand_dataset}", json={"clear_reference_time": True})
    d = get(client, hand_dataset, "overview")["data"]
    assert d["reference_time_source"] == "latest_event"
    assert d["reference_time"] == "2024-01-05T08:00:00+00:00"
    assert d["open_case_age"]["min"] == pytest.approx(0)  # 007 starts at the latest event
