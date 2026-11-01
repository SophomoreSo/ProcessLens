"""ProcessLens Miner API: packages, knowledge model, queries, views, component data, exchange format."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from miner.main import app as miner_app
from miner.security import csrf_token


@pytest.fixture
def miner(client):
    """Miner client sharing the ProcessLens test database (``client`` sets up the data dir)."""
    with TestClient(miner_app) as c:
        c.headers["X-CSRF-Token"] = csrf_token()
        yield c


@pytest.fixture
def package(miner, hand_dataset) -> dict:
    r = miner.post("/api/packages", json={"name": "Hand check", "dataset_id": hand_dataset})
    assert r.status_code == 201, r.text
    return r.json()


def test_index_and_security(miner):
    r = miner.get("/")
    assert r.status_code == 200 and "ProcessLens Miner" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert miner.get("/vendor/plotly-basic.min.js").status_code == 200
    r = miner.post("/api/packages", json={}, headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = miner.post("/api/packages", json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"


def test_datasets_and_reference(miner, hand_dataset):
    ds = miner.get("/api/datasets").json()
    assert [d["id"] for d in ds] == [hand_dataset] and ds[0]["object_types"] == ["case"]
    ref = miner.get("/api/reference").json()
    assert any(c["name"] == "case.duration_d" for c in ref["case_columns"])
    assert any(f["name"] == "SHARE" for f in ref["aggregations"])


def test_create_package_with_starter_content(miner, package):
    assert package["object_type"] == "case" and package["dataset_name"] == "hand-check"
    keys = {k["key"] for k in package["kpis"]}
    assert {"cases", "avg_throughput_d", "rework_rate", "completion_rate", "cost_per_case"} <= keys
    assert {f["key"] for f in package["filters"]} == {"completed", "rework", "slow"}
    assert [v["name"] for v in package["views"]] == ["Process overview", "Throughput analysis"]
    values = miner.get(f"/api/packages/{package['id']}/kpi-values").json()["values"]
    assert values["cases"]["value"] == 5
    assert values["rework_rate"] == {"value": pytest.approx(0.2), "format": "percent"}
    assert values["completion_rate"]["value"] == pytest.approx(0.6)
    counts = miner.get(f"/api/packages/{package['id']}/filter-counts").json()["counts"]
    assert counts["completed"] == {"matching": 3, "total": 5}


def test_every_starter_component_renders(miner, package):
    pid = package["id"]
    for v in package["views"]:
        view = miner.get(f"/api/packages/{pid}/views/{v['id']}").json()
        for comp in view["components"]:
            r = miner.post(f"/api/packages/{pid}/data", json={"component": comp, "filters": []})
            assert r.status_code == 200, (comp, r.text)


def test_component_data_with_selection(miner, package):
    pid = package["id"]
    comp = {"id": "x", "type": "bar", "config": {"dimension": {"formula": "event.activity"},
                                                 "measures": [{"formula": 'KPI("cases")'}]}}
    r = miner.post(f"/api/packages/{pid}/data", json={"component": comp, "filters": ['FILTER("completed")']}).json()
    assert r["case_count"] == 3 and r["total_case_count"] == 5
    assert r["columns"][1]["format"] == "integer"  # from the KPI
    assert dict((row[0], row[1]) for row in r["rows"]) == {"A": 3, "B": 3, "C": 3, "D": 3}
    kpi = {"id": "k", "type": "kpi", "config": {"measure": {"formula": 'KPI("rework_rate")'}}}
    r = miner.post(f"/api/packages/{pid}/data", json={"component": kpi, "filters": ['CONTAINS("B")']}).json()
    assert r["value"] == pytest.approx(1 / 3) and r["target"] == 0.1 and r["higher_is_better"] is False
    graph = {"id": "g", "type": "process", "config": {"max_activities": 2}}
    r = miner.post(f"/api/packages/{pid}/data", json={"component": graph, "filters": []}).json()
    assert [n["id"] for n in r["nodes"]] == ["A", "B"] and r["hidden_activities"] == 2
    var = {"id": "v", "type": "variants", "config": {"limit": 1}}
    r = miner.post(f"/api/packages/{pid}/data", json={"component": var, "filters": []}).json()
    assert r["total"] == 4 and r["items"][0]["case_count"] == 2


def test_component_errors_are_plql_errors(miner, package):
    comp = {"id": "x", "type": "table", "config": {"dimensions": [{"formula": "event.activty"}], "measures": []}}
    r = miner.post(f"/api/packages/{package['id']}/data", json={"component": comp, "filters": []})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "plql_error" and err["message"].startswith("Dimension 1: Unknown event column")
    assert err["details"]["position"] == 0


def test_query_and_saved_queries(miner, package):
    pid = package["id"]
    spec = {"dimensions": [{"formula": "case.status", "label": "Status"}],
            "measures": [{"formula": "COUNT_CASES()", "label": "Cases"}, {"formula": "SHARE(case.rework)"}],
            "filters": [], "order_by": [[0, False]]}
    r = miner.post(f"/api/packages/{pid}/query", json=spec).json()
    assert [row[:2] for row in r["rows"]] == [["completed", 3], ["incomplete", 1], ["unknown", 1]]
    assert [c["format"] for c in r["columns"]] == ["auto", "integer", "percent"]
    q = miner.post(f"/api/packages/{pid}/queries", json={"name": "By status", "spec": spec})
    assert q.status_code == 201
    qid = q.json()["id"]
    assert miner.get(f"/api/packages/{pid}").json()["queries"][0]["name"] == "By status"
    assert miner.delete(f"/api/packages/{pid}/queries/{qid}").status_code == 204


def test_kpi_lifecycle_and_references(miner, package):
    pid = package["id"]
    bad = miner.post(f"/api/packages/{pid}/kpis", json={"key": "bad", "name": "Bad", "formula": "case.duration_d"})
    assert bad.status_code == 422 and "aggregation" in bad.json()["error"]["message"]
    dup = miner.post(f"/api/packages/{pid}/kpis", json={"key": "cases", "name": "Again", "formula": "COUNT_CASES()"})
    assert dup.status_code == 409
    r = miner.post(f"/api/packages/{pid}/kpis", json={"key": "share_slow", "name": "Slow share",
                                                      "formula": 'SHARE(FILTER("slow"))', "format": "percent"})
    assert r.status_code == 201
    # the starter views use KPI("cases"): it cannot be deleted or re-keyed while in use
    cases = next(k for k in package["kpis"] if k["key"] == "cases")
    r = miner.delete(f"/api/packages/{pid}/kpis/{cases['id']}")
    assert r.status_code == 409 and "view Process overview" in r.json()["error"]["message"]
    r = miner.put(f"/api/packages/{pid}/kpis/{cases['id']}", json={**cases, "key": "n_cases"})
    assert r.status_code == 409
    r = miner.put(f"/api/packages/{pid}/kpis/{cases['id']}", json={**cases, "name": "Objects"})
    assert r.status_code == 200 and r.json()["name"] == "Objects"
    slow = next(f for f in package["filters"] if f["key"] == "slow")
    assert miner.delete(f"/api/packages/{pid}/filters/{slow['id']}").status_code == 409  # used by share_slow
    # testing an unsaved edit uses the new formula for references
    r = miner.post(f"/api/packages/{pid}/evaluate", json={"formula": "COUNT_CASES() * 2", "key": "cases"}).json()
    assert r["value"] == 10
    r = miner.post(f"/api/packages/{pid}/evaluate", json={"formula": "case.rework", "kind": "condition"}).json()
    assert (r["matching"], r["total"]) == (1, 5)


def test_base_filter_applies_everywhere(miner, package):
    pid = package["id"]
    r = miner.patch(f"/api/packages/{pid}", json={"base_filter": "case.events >"})
    assert r.status_code == 422
    r = miner.patch(f"/api/packages/{pid}", json={"base_filter": 'case.status = "completed"'})
    assert r.status_code == 200
    assert miner.get(f"/api/packages/{pid}/kpi-values").json()["values"]["cases"]["value"] == 3


def test_views_crud(miner, package):
    pid = package["id"]
    body = {"name": "Mine", "components": [{"id": "a", "type": "text", "config": {"text": "hi"}},
                                           {"id": "a", "type": "text"}]}
    assert miner.post(f"/api/packages/{pid}/views", json=body).status_code == 422  # duplicate ids
    body["components"].pop()
    v = miner.post(f"/api/packages/{pid}/views", json=body).json()
    copy = miner.post(f"/api/packages/{pid}/views/{v['id']}/duplicate").json()
    assert copy["name"] == "Mine (copy)" and copy["components"][0]["config"]["text"] == "hi"
    body["components"][0]["width"] = 12
    assert miner.put(f"/api/packages/{pid}/views/{v['id']}", json=body).json()["components"][0]["width"] == 12
    assert miner.delete(f"/api/packages/{pid}/views/{copy['id']}").status_code == 204
    assert miner.get(f"/api/packages/{pid}/views/{copy['id']}").status_code == 404


def test_export_import_round_trip(miner, package, hand_dataset):
    pid = package["id"]
    doc = miner.get(f"/api/packages/{pid}/export").json()
    assert doc["format"] == "processlens-miner-package" and doc["package"]["dataset"]["id"] == hand_dataset
    r = miner.post("/api/packages/import", json={"document": doc})
    assert r.status_code == 201
    imported = r.json()
    assert imported["id"] != pid
    assert [k["key"] for k in imported["kpis"]] == [k["key"] for k in package["kpis"]]
    assert len(imported["views"]) == 2
    assert miner.post("/api/packages/import", json={"document": {"format": "x"}}).status_code == 422
    assert miner.delete(f"/api/packages/{pid}").status_code == 204
    assert len(miner.get("/api/packages").json()) == 1


def test_missing_dataset_is_reported(miner, client, package):
    assert client.delete(f"/api/datasets/{package['dataset_id']}?confirm_name=hand-check").status_code == 204
    r = miner.get(f"/api/packages/{package['id']}/kpi-values")
    assert r.status_code == 409 and r.json()["error"]["code"] == "dataset_unavailable"
    assert miner.get("/api/packages").json()[0]["dataset_ready"] is False
