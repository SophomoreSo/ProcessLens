"""Object-centric event logs: OCEL 2.0 validation, shared-event counting, OC-DFG, projection, OCPN."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from app.database import session_scope
from app.mining import runs
from app.models import MiningRun, OcelE2O, OcelEvent, OcelObject
from app.services import jobs
from app.services.ocel import OcelInvalid, parse

FIXTURE = Path(__file__).parent / "fixtures" / "ocel_small.json"


def _import(client, content: bytes | None = None, name: str = "ocel_small.json"):
    r = client.post("/api/ocel/upload", files={"file": (name, content or FIXTURE.read_bytes(), "application/json")})
    return r


def _log_id(client) -> int:
    r = _import(client)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_import_persists_events_once_and_counts(client):
    r = _import(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["counts"] == {"events": 8, "objects": 7, "e2o": 16, "o2o": 6, "event_types": 4, "object_types": 3,
                              "object_attribute_values": 6}
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(OcelEvent)) == 8  # shared events are stored once
        assert db.scalar(select(func.count()).select_from(OcelE2O)) == 16
        assert db.scalar(select(func.count()).select_from(OcelObject)) == 7


def test_shared_events_are_counted_once(client):
    lid = _log_id(client)
    s = client.get(f"/api/ocel/{lid}/stats").json()
    assert (s["unique_events"], s["unique_objects"], s["e2o_relationships"], s["o2o_relationships"]) == (8, 7, 16, 6)
    assert s["shared_events"] == 5
    assert s["cost_total_unique_events"] == 33  # a per-relationship sum would double count: 78
    item = next(r for r in s["object_types"] if r["object_type"] == "item")
    assert (item["objects"], item["unique_events"], item["e2o_relationships"]) == (3, 8, 11)
    assert item["events_with_several_objects_of_type"] == 3  # e1, e6, e8

    only_orders = client.get(f"/api/ocel/{lid}/stats", params={"object_type": "order"}).json()
    assert (only_orders["unique_events"], only_orders["e2o_relationships"], only_orders["shared_events"]) == (2, 2, 0)
    assert only_orders["cost_total_unique_events"] == 20
    assert client.get(f"/api/ocel/{lid}/stats", params={"object_type": "nope"}).status_code == 422


def test_ocdfg_distinguishes_object_transitions_from_unique_event_pairs(client):
    lid = _log_id(client)
    g = client.get(f"/api/ocel/{lid}/ocdfg").json()
    edges = {(e["object_type"], e["source"], e["target"]): e for e in g["edges"]}
    e = edges[("item", "place order", "pick item")]
    assert (e["object_transitions"], e["objects"], e["unique_event_pairs"]) == (3, 3, 3)
    e = edges[("item", "send package", "deliver package")]
    assert (e["object_transitions"], e["objects"], e["unique_event_pairs"]) == (2, 2, 1)
    assert edges[("package", "send package", "deliver package")]["object_transitions"] == 1
    assert not [k for k in edges if k[0] == "order"]  # each order has a single event
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["send package"]["unique_events"] == 2
    assert nodes["send package"]["per_type"] == {"item": 2, "package": 2}
    filtered = client.get(f"/api/ocel/{lid}/ocdfg", params={"object_type": "package"}).json()
    assert {e["object_type"] for e in filtered["edges"]} == {"package"}


def test_object_timeline_attributes_and_related_objects(client):
    lid = _log_id(client)
    d = client.get(f"/api/ocel/{lid}/objects/o1").json()
    assert d["object_type"] == "order" and d["event_count"] == 1
    assert [(a["name"], a["value"]) for a in d["attributes"]] == [("status", "placed"), ("status", "shipped")]
    assert {(r["direction"], r["object_id"], r["qualifier"]) for r in d["related_via_o2o"]} == {("in", "i1", "part of"), ("in", "i2", "part of")}
    assert {r["object_id"] for r in d["related_via_events"]} == {"i1", "i2"}
    i2 = client.get(f"/api/ocel/{lid}/objects/i2").json()
    assert [e["event_type"] for e in i2["events"]] == ["place order", "pick item", "send package", "deliver package"]
    assert i2["events"][2]["other_object_count"] == 2  # p1 and i3 share e6
    static = next(a for a in i2["attributes"] if a["name"] == "product")
    assert static["valid_from"] is None
    assert client.get(f"/api/ocel/{lid}/objects/zzz").status_code == 404
    page = client.get(f"/api/ocel/{lid}/objects", params={"object_type": "item"}).json()
    assert page["total"] == 3 and {i["object_id"] for i in page["items"]} == {"i1", "i2", "i3"}


def test_relationship_delays_are_grouped_by_related_count(client):
    lid = _log_id(client)
    r = client.get(f"/api/ocel/{lid}/relationship-delays", params={"object_type": "item"}).json()
    assert r["objects"] == 3
    via_pkg = next(g for g in r["groups"] if g["via"] == "shared_events" and g["related_type"] == "package")
    assert {b["related"]: b["objects"] for b in via_pkg["buckets"]} == {1: 3}


def test_validation_rejects_invalid_files_with_findings(client):
    data = json.loads(FIXTURE.read_text())
    data["events"].append(dict(data["events"][0]))  # duplicate id
    data["events"].append({"id": "e99", "type": "unknown", "time": "2024-01-04T00:00:00Z"})
    data["events"].append({"id": "e98", "type": "pick item", "time": "2024-01-04T00:00:00Z",
                           "relationships": [{"objectId": "ghost"}]})
    data["objects"].append({"id": "x1", "type": "car"})
    r = _import(client, json.dumps(data).encode())
    assert r.status_code == 422
    codes = {f["code"] for f in r.json()["error"]["details"]}
    assert {"duplicate_event_id", "undeclared_event_type", "dangling_e2o", "undeclared_object_type"} <= codes
    assert client.get("/api/ocel").json()["items"] == []  # nothing persisted

    assert _import(client, b"not json").status_code == 422
    assert _import(client, b"{}").json()["error"]["details"][0]["code"] == "missing_key"
    assert _import(client, FIXTURE.read_bytes(), name="log.xml").status_code == 422


def test_unsupported_constructs_are_reported_not_silently_accepted():
    data = json.loads(FIXTURE.read_text())
    data["extensions"] = {"x": 1}
    data["events"][0]["lifecycle"] = "complete"
    data["events"][1]["time"] = "2024-01-01T09:00:00"  # no offset
    parsed = parse(json.dumps(data).encode())
    codes = {f["code"] for f in parsed.findings}
    assert {"unsupported_top_level", "ignored_key", "naive_times_utc"} <= codes
    try:
        parse(b'{"objectTypes": [], "eventTypes": [], "objects": [], "events": []}')
        raise AssertionError("expected OcelInvalid")
    except OcelInvalid as exc:
        assert exc.findings[-1]["code"] == "no_events"


def test_projection_requires_acknowledgement_and_keeps_event_identity(client):
    lid = _log_id(client)
    preview = client.get(f"/api/ocel/{lid}/projection-preview", params={"object_type": "item"}).json()
    assert preview == {"object_type": "item", "cases": 3, "rows": 11, "unique_events": 8, "duplicated_event_rows": 3}
    r = client.post(f"/api/ocel/{lid}/project", json={"object_type": "item"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "flattening_not_acknowledged"
    r = client.post(f"/api/ocel/{lid}/project", json={"object_type": "item", "acknowledge_flattening": True})
    assert r.status_code == 202, r.text
    jobs.wait_for(r.json()["job"]["id"])
    job = client.get(f"/api/jobs/{r.json()['job']['id']}").json()
    assert job["status"] == "succeeded", job
    ds = client.get(f"/api/datasets/{job['dataset_id']}").json()
    assert ds["event_count"] == 11 and ds["case_count"] == 3
    assert "3" in ds["description"] and "item" in ds["description"]
    assert "ocel_event_id" in ds["attribute_columns"]
    cases = client.get(f"/api/datasets/{job['dataset_id']}/cases/i2").json()
    events = cases["events"]
    assert [e["activity"] for e in events] == ["place order", "pick item", "send package", "deliver package"]
    assert events[0]["attributes"]["ocel_event_id"] == "e1"
    assert all(e["cost"] is None for e in events)  # costs are not mapped (would be counted per copy)


def test_ocpn_run_is_cached_validated_and_deleted_with_log(client):
    lid = _log_id(client)
    assert client.post(f"/api/ocel/{lid}/ocpn", json={"params": {"bogus": 1}}).status_code == 422
    assert client.post(f"/api/ocel/{lid}/ocpn", json={"params": {"noise_threshold": 0.2}}).status_code == 422
    assert client.post(f"/api/ocel/{lid}/ocpn", json={"object_types": ["car"]}).status_code == 422
    r = client.post(f"/api/ocel/{lid}/ocpn", json={"object_types": ["item", "package"]})
    assert r.status_code == 202, r.text
    run_id = r.json()["run"]["id"]
    runs.wait_for(run_id)
    run = client.get(f"/api/mining/runs/{run_id}").json()["run"]
    assert run["status"] == "succeeded", run
    assert run["result"]["unique_events"] == 8 and run["result"]["e2o_relationships"] == 14
    rep = client.get(f"/api/mining/runs/{run_id}/model").json()["representations"]["oc_petri_net"]
    assert rep["object_types"] == ["item", "package"]
    assert {p["object_type"] for p in rep["places"]} == {"item", "package"}
    source = {p["object_type"]: p["initial"] for p in rep["places"] if p["source"]}
    assert source == {"item": 3, "package": 2}  # tokens = objects of each type
    labels = {t_["label"] for t_ in rep["transitions"] if not t_["silent"]}
    assert labels == {"place order", "pick item", "send package", "deliver package"}
    assert any(a["variable"] and a["object_type"] == "item" for a in rep["arcs"])  # several items per send/deliver
    assert client.get(f"/api/mining/runs/{run_id}/export/json").status_code == 200
    again = client.post(f"/api/ocel/{lid}/ocpn", json={"object_types": ["package", "item"]})
    assert again.status_code == 200 and again.json()["cached"] and again.json()["run"]["id"] == run_id
    hist = client.get("/api/mining/runs", params={"ocel_log_id": lid}).json()
    assert hist["total"] == 1

    assert client.delete(f"/api/ocel/{lid}").status_code == 204
    assert client.get(f"/api/ocel/{lid}").status_code == 404
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(MiningRun)) == 0
        assert db.scalar(select(func.count()).select_from(OcelE2O)) == 0


def test_demo_log_loads(client):
    r = client.post("/api/ocel/demo")
    assert r.status_code == 201
    body = r.json()
    assert body["is_demo"] and body["counts"]["events"] > 300 and body["findings"] == []
    s = client.get(f"/api/ocel/{body['id']}/stats").json()
    assert s["shared_events"] > 0 and s["unique_events"] == body["counts"]["events"]
    assert client.get("/ocel").status_code == 200
