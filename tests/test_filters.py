"""Case-level filter semantics, applied identically across endpoints."""

from __future__ import annotations

import csv
import io

import pytest


def population(client, ds, path="overview", **params):
    r = client.get(f"/api/datasets/{ds}/{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_activity_filter_keeps_complete_traces(client, hand_dataset):
    res = population(client, hand_dataset, "cases", activity="B")
    ids = sorted(c["case_id"] for c in res["data"]["items"])
    assert ids == ["C1", "C2", "C3"]
    # Events of other activities (A, C, D) are still there: 4 + 5 + 4 = 13 events
    ov = population(client, hand_dataset, activity="B")["data"]
    assert ov["event_count"] == 13
    assert ov["activity_count"] == 4
    g = population(client, hand_dataset, "graph", activity="B")["data"]
    assert {n["id"] for n in g["nodes"]} == {"A", "B", "C", "D"}


def test_multiple_activities_mean_all_of_them(client, hand_dataset):
    assert population(client, hand_dataset, activity=["A", "C"])["population"]["case_count"] == 4
    assert population(client, hand_dataset, activity=["B", "D"])["population"]["case_count"] == 3
    assert population(client, hand_dataset, exclude_activity="B")["population"]["case_count"] == 2


def test_resource_and_department_any_event(client, hand_dataset):
    assert population(client, hand_dataset, resource="r4")["population"]["case_count"] == 1  # only C3
    assert population(client, hand_dataset, resource=["r4", "r3"])["population"]["case_count"] == 3
    assert population(client, hand_dataset, department="D2")["population"]["case_count"] == 4


def test_date_range_inclusive_in_display_timezone(client, hand_dataset):
    p = population(client, hand_dataset, start_from="2024-01-02", start_to="2024-01-03")
    assert p["population"]["case_count"] == 2  # C2 and C3; both bounds inclusive
    # With a display timezone of UTC-10, C1 (2024-01-01 08:00Z) starts on 2023-12-31 locally.
    client.patch(f"/api/datasets/{hand_dataset}", json={"display_timezone": "Pacific/Honolulu"})
    p2 = population(client, hand_dataset, start_from="2023-12-31", start_to="2023-12-31")
    assert p2["population"]["case_count"] == 1


def test_duration_bounds_inclusive(client, hand_dataset):
    assert population(client, hand_dataset, min_duration_h=2, max_duration_h=4)["population"]["case_count"] == 2
    assert population(client, hand_dataset, max_duration_h=0)["population"]["case_count"] == 1  # single-event case


def test_rework_status_variant_and_transition(client, hand_dataset):
    assert population(client, hand_dataset, rework="true")["population"]["case_count"] == 1
    assert population(client, hand_dataset, rework="false")["population"]["case_count"] == 4
    assert population(client, hand_dataset, status=["incomplete", "unknown"])["population"]["case_count"] == 2
    top = population(client, hand_dataset, "variants")["data"]["items"][0]
    assert population(client, hand_dataset, variant=top["variant_id"])["population"]["case_count"] == 2
    assert population(client, hand_dataset, transition_from="B", transition_to="B")["population"]["case_count"] == 1
    assert population(client, hand_dataset, transition_from="__start__", transition_to="A")["population"]["case_count"] == 5
    assert population(client, hand_dataset, transition_from="C", transition_to="__end__")["population"]["case_count"] == 1


def test_empty_result_everywhere(client, hand_dataset):
    params = {"activity": "does-not-exist"}
    ov = population(client, hand_dataset, **params)["data"]
    assert ov["case_count"] == 0 and ov["event_count"] == 0
    assert ov["observed_duration"]["median"] is None
    assert population(client, hand_dataset, "graph", **params)["data"]["nodes"] == []
    assert population(client, hand_dataset, "variants", **params)["data"]["items"] == []
    assert population(client, hand_dataset, "cases", **params)["data"]["total"] == 0
    assert population(client, hand_dataset, "performance", **params)["data"]["transitions"] == []
    csv_text = client.get(f"/api/datasets/{hand_dataset}/exports/cases.csv", params=params).text
    assert csv_text.strip().count("\n") == 0  # header only


@pytest.mark.parametrize("params", [{"department": "D1", "rework": "false"}, {"activity": "C"}, {}])
def test_same_population_across_endpoints_and_exports(client, hand_dataset, params):
    n = population(client, hand_dataset, **params)["population"]["case_count"]
    assert population(client, hand_dataset, "graph", **params)["data"]["case_count"] == n
    assert population(client, hand_dataset, "cases", **params)["data"]["total"] == n
    assert sum(v["case_count"] for v in population(client, hand_dataset, "variants", **params)["data"]["items"]) == n
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/datasets/{hand_dataset}/exports/cases.csv", params=params).text)))
    assert len(rows) == n
    events = list(csv.DictReader(io.StringIO(client.get(f"/api/datasets/{hand_dataset}/exports/events.csv", params=params).text)))
    assert len({e["case_id"] for e in events}) == n


def test_invalid_filters_are_rejected(client, hand_dataset):
    r = client.get(f"/api/datasets/{hand_dataset}/overview", params={"start_from": "2024-02-01", "start_to": "2024-01-01"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_filter"
    r = client.get(f"/api/datasets/{hand_dataset}/overview", params={"conformance_result": "violating"})
    assert r.status_code == 422
    r = client.get(f"/api/datasets/{hand_dataset}/overview", params={"conformance_rule": 999, "conformance_result": "violating"})
    assert r.status_code == 422


def test_cohort_comparison(client, hand_dataset):
    r = client.post(f"/api/datasets/{hand_dataset}/performance/compare",
                    json={"a": {"rework": True}, "b": {"rework": False}, "label_a": "R", "label_b": "N"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (d["a"]["case_count"], d["b"]["case_count"], d["overlapping_cases"]) == (1, 4, 0)
    assert d["a"]["label"] == "R"
    # cohorts are intersected with the global filters
    r2 = client.post(f"/api/datasets/{hand_dataset}/performance/compare", params={"status": "completed"},
                     json={"a": {"rework": True}, "b": {"rework": False}})
    d2 = r2.json()["data"]
    assert (d2["base_case_count"], d2["a"]["case_count"], d2["b"]["case_count"]) == (3, 1, 2)
