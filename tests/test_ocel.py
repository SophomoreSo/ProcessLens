"""Object-centric datasets: OCEL 2.0 import through the wizard, shared-event counting, perspectives,
OC-DFG, objects, type graph, rules per object type, OC Petri nets."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from app.database import session_scope
from app.mining import runs
from app.models import MiningRun, OcelE2O, OcelEvent, OcelObject
from app.services.ocel import OcelInvalid, parse
from tests.conftest import import_ocel

FIXTURE = Path(__file__).parent / "fixtures" / "ocel_small.json"


def _dataset(client, main: str = "order") -> int:
    return import_ocel(client, FIXTURE.read_bytes(), main, {"cost": "cost"})["dataset_id"]


def test_upload_preview_suggests_main_type_and_fields(client):
    r = client.post("/api/uploads", files={"file": ("ocel_small.json", FIXTURE.read_bytes(), "application/json")})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["source_format"] == "ocel"
    c = p["conversion"]
    assert c["counts"] == {"events": 8, "objects": 7, "e2o": 16, "o2o": 6, "object_attribute_values": 6}
    assert {t["name"]: (t["objects"], t["events"]) for t in c["object_types"]} == {
        "order": (2, 2), "item": (3, 8), "package": (2, 3)}
    assert c["suggested_main_type"] == "item" and c["suggested_fields"]["cost"] == "cost"


def test_import_stores_shared_events_once(client):
    ds = _dataset(client)
    meta = client.get(f"/api/datasets/{ds}").json()
    assert (meta["event_count"], meta["case_count"]) == (8, 2)  # unique events; objects of the main type
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(OcelEvent)) == 8
        assert db.scalar(select(func.count()).select_from(OcelE2O)) == 16
        assert db.scalar(select(func.count()).select_from(OcelObject)) == 7
    s = client.get(f"/api/datasets/{ds}/ocel/stats").json()
    assert (s["unique_events"], s["unique_objects"], s["e2o_relationships"], s["o2o_relationships"]) == (8, 7, 16, 6)
    assert s["shared_events"] == 5 and s["cost_total_unique_events"] == 33  # per relationship it would be 78
    orders = client.get(f"/api/datasets/{ds}/ocel/stats", params={"object_type": "order"}).json()
    assert (orders["unique_events"], orders["shared_events"], orders["cost_total_unique_events"]) == (2, 0, 20)


def test_perspectives_flatten_by_lead_type_without_double_counting(client):
    ds = _dataset(client)
    order = client.get(f"/api/datasets/{ds}/overview").json()
    assert order["object_type"] == "order"
    assert (order["data"]["case_count"], order["data"]["event_count"]) == (2, 2)
    item = client.get(f"/api/datasets/{ds}/overview", params={"object_type": "item"}).json()
    d = item["data"]
    assert item["object_type"] == "item" and d["case_count"] == 3
    assert (d["event_count"], d["object_event_count"]) == (8, 11)  # e1, e6, e8 relate to two items each
    assert d["total_cost"] == 33  # every event once
    v = client.get(f"/api/datasets/{ds}/variants", params={"object_type": "item"}).json()["data"]["items"]
    assert v[0]["activities"] == ["place order", "pick item", "send package", "deliver package"]
    case = client.get(f"/api/datasets/{ds}/cases/i2", params={"object_type": "item"}).json()
    assert [e["ocel_event_id"] for e in case["events"]] == ["e1", "e4", "e6", "e8"]
    others = {o["object_id"] for o in case["events"][2]["other_objects"]}
    assert others == {"p1", "i3"}
    assert client.get(f"/api/datasets/{ds}/overview", params={"object_type": "car"}).status_code == 422
    csv = client.get(f"/api/datasets/{ds}/exports/events.csv", params={"object_type": "item"}).text
    assert "ocel_event_id" in csv.splitlines()[0].split(",") and len(csv.splitlines()) == 12


def test_ocdfg_and_type_graph(client):
    ds = _dataset(client)
    g = client.get(f"/api/datasets/{ds}/ocel/ocdfg").json()
    edges = {(e["object_type"], e["source"], e["target"]): e for e in g["edges"]}
    e = edges[("item", "send package", "deliver package")]
    assert (e["object_transitions"], e["objects"], e["unique_event_pairs"]) == (2, 2, 1)
    tg = client.get(f"/api/datasets/{ds}/ocel/type-graph").json()
    pairs = {(x["source"], x["target"]): (x["shared_events"], x["object_pairs"]) for x in tg["edges"]}
    assert pairs[("item", "order")] == (2, 3) and pairs[("item", "package")] == (3, 3)
    assert {(x["source"], x["target"], x["qualifier"]) for x in tg["o2o"]} == {
        ("item", "order", "part of"), ("package", "item", "contains")}


def test_object_timeline_attributes_and_related_objects(client):
    ds = _dataset(client)
    d = client.get(f"/api/datasets/{ds}/ocel/objects/o1").json()
    assert [(a["name"], a["value"]) for a in d["attributes"]] == [("status", "placed"), ("status", "shipped")]
    assert {r["object_id"] for r in d["related_via_events"]} == {"i1", "i2"}
    assert client.get(f"/api/datasets/{ds}/ocel/objects/zzz").status_code == 404
    page = client.get(f"/api/datasets/{ds}/ocel/objects", params={"object_type": "item"}).json()
    assert page["total"] == 3
    r = client.get(f"/api/datasets/{ds}/ocel/relationship-delays", params={"object_type": "item"}).json()
    assert r["objects"] == 3


def test_status_history_drives_completion(client):
    ds = import_ocel(client, FIXTURE.read_bytes(), "order", completion={
        "mode": "case_status", "completed_status_values": ["shipped"], "open_status_values": ["placed"]})["dataset_id"]
    d = client.get(f"/api/datasets/{ds}/overview").json()["data"]
    assert d["status_counts"] == {"completed": 1, "incomplete": 1, "unknown": 0}  # o1 shipped, o2 still placed


def test_rules_apply_to_one_object_type(client):
    ds = _dataset(client)
    r = client.post(f"/api/datasets/{ds}/conformance-rules", json={
        "name": "items are delivered", "rule_type": "required_activity", "object_type": "item",
        "params": {"activity": "deliver package", "assume_complete": True}})
    assert r.status_code == 201 and r.json()["object_type"] == "item"
    rid = r.json()["id"]
    on_orders = client.get(f"/api/datasets/{ds}/conformance/summary").json()
    assert on_orders["object_type"] == "order" and on_orders["rules"][0]["applies"] is False
    on_items = client.get(f"/api/datasets/{ds}/conformance/summary", params={"object_type": "item"}).json()
    assert (on_items["rules"][0]["compliant"], on_items["rules"][0]["violating"]) == (2, 1)  # i1 is never delivered
    ev = client.get(f"/api/datasets/{ds}/conformance-rules/{rid}/evaluation").json()
    assert ev["object_type"] == "item" and ev["summary"]["violating"] == 1
    bad = client.get(f"/api/datasets/{ds}/overview", params={"conformance_rule": rid, "conformance_result": "violating"})
    assert bad.status_code == 422  # a filter cannot use a rule of another object type
    assert client.post(f"/api/datasets/{ds}/conformance-rules", json={
        "name": "x", "rule_type": "required_activity", "object_type": "car",
        "params": {"activity": "a"}}).status_code == 422


def test_invalid_ocel_upload_returns_findings(client):
    data = json.loads(FIXTURE.read_text())
    data["events"].append(dict(data["events"][0]))
    data["events"].append({"id": "e99", "type": "unknown", "time": "2024-01-04T00:00:00Z"})
    data["objects"].append({"id": "x1", "type": "car"})
    r = client.post("/api/uploads", files={"file": ("bad.json", json.dumps(data).encode(), "application/json")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "ocel_invalid"
    codes = {f["code"] for f in r.json()["error"]["details"]}
    assert {"duplicate_event_id", "undeclared_event_type", "undeclared_object_type"} <= codes
    assert client.get("/api/datasets").json() == []


def test_unsupported_constructs_are_reported_not_silently_accepted():
    data = json.loads(FIXTURE.read_text())
    data["extensions"] = {"x": 1}
    data["events"][0]["lifecycle"] = "complete"
    data["events"][1]["time"] = "2024-01-01T09:00:00"
    codes = {f["code"] for f in parse(json.dumps(data).encode()).findings}
    assert {"unsupported_top_level", "ignored_key", "naive_times_utc"} <= codes
    try:
        parse(b'{"objectTypes": [], "eventTypes": [], "objects": [], "events": []}')
        raise AssertionError("expected OcelInvalid")
    except OcelInvalid as exc:
        assert exc.findings[-1]["code"] == "no_events"


def test_ocpn_run_and_deletion(client):
    ds = _dataset(client)
    assert client.post(f"/api/datasets/{ds}/ocel/ocpn", json={"params": {"bogus": 1}}).status_code == 422
    r = client.post(f"/api/datasets/{ds}/ocel/ocpn", json={"object_types": ["item", "package"]})
    assert r.status_code == 202, r.text
    run_id = r.json()["run"]["id"]
    runs.wait_for(run_id)
    run = client.get(f"/api/mining/runs/{run_id}").json()["run"]
    assert run["status"] == "succeeded" and run["dataset_id"] == ds, run
    rep = client.get(f"/api/mining/runs/{run_id}/model").json()["representations"]["oc_petri_net"]
    assert {p["object_type"]: p["initial"] for p in rep["places"] if p["source"]} == {"item": 3, "package": 2}
    again = client.post(f"/api/datasets/{ds}/ocel/ocpn", json={"object_types": ["package", "item"]})
    assert again.status_code == 200 and again.json()["cached"]
    name = client.get(f"/api/datasets/{ds}").json()["name"]
    assert client.delete(f"/api/datasets/{ds}", params={"confirm_name": name}).status_code == 204
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(MiningRun)) == 0
        assert db.scalar(select(func.count()).select_from(OcelE2O)) == 0


def test_demo_loads_two_object_centric_datasets(client):
    from app.services import jobs

    r = client.post("/api/demo/load")
    jobs.wait_for(r.json()["id"])
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job
    p2p = client.get(f"/api/datasets/{job['dataset_id']}").json()
    assert p2p["main_object_type"] == "purchase order"
    model = client.get(f"/api/datasets/{p2p['id']}/ocel").json()
    assert {t["name"] for t in model["object_types"]} == {"purchase order", "vendor"}
    vendors = client.get(f"/api/datasets/{p2p['id']}/overview", params={"object_type": "vendor"}).json()["data"]
    assert vendors["case_count"] > 1 and vendors["event_count"] == 5304
    ocel_ds = client.get(f"/api/datasets/{job['result']['ocel_demo_dataset_id']}").json()
    assert ocel_ds["main_object_type"] == "order" and ocel_ds["event_count"] == 527
