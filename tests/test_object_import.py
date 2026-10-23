"""Object-centric imports through the wizard: object columns (CSV), XES trace and attribute objects."""

from __future__ import annotations

from pathlib import Path

from app.services import jobs

XES = Path(__file__).parent / "fixtures" / "lifecycle.xes"
CSV = b"""order,item,activity,timestamp,amount,cost
O1,I1;I2,place order,2024-01-01T08:00:00Z,100,10
O1,I1,pick,2024-01-01T09:00:00Z,100,1
O1,I2,pick,2024-01-01T10:00:00Z,120,1
O2,I3,place order,2024-01-02T08:00:00Z,50,10
O1;O2,,ship,2024-01-03T08:00:00Z,,5
"""


def upload(client, content: bytes, name: str):
    r = client.post("/api/uploads", files={"file": (name, content, "text/plain")})
    assert r.status_code == 201, r.text
    return r.json()


def run(client, upload_id: str, mapping: dict, expect: str = "succeeded", **options) -> dict:
    r = client.post(f"/api/uploads/{upload_id}/import", json={"mapping": mapping, **options})
    assert r.status_code == 202, r.text
    jobs.wait_for(r.json()["id"])
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == expect, job
    return job


MAPPING = {"case_id": "order", "activity": "activity", "timestamp": "timestamp", "cost": "cost",
           "case_object_type": "order", "case_attributes": ["amount"],
           "object_columns": [{"column": "item", "object_type": "item", "separator": ";"}]}


def test_csv_object_columns_create_shared_events(client):
    up = upload(client, CSV.replace(b"O1;O2,,ship", b"O1,,ship"), "orders.csv")
    v = client.post(f"/api/uploads/{up['upload_id']}/validate", json={"mapping": MAPPING}).json()
    assert v["invalid_rows"] == 0 and v["counts_by_code"]["object_column_empty"] == 1
    job = run(client, up["upload_id"], MAPPING)
    ds = client.get(f"/api/datasets/{job['dataset_id']}").json()
    assert ds["main_object_type"] == "order"
    assert {t["name"]: (t["objects"], t["events"]) for t in ds["object_types"]} == {"order": (2, 5), "item": (3, 4)}
    items = client.get(f"/api/datasets/{ds['id']}/overview", params={"object_type": "item"}).json()["data"]
    assert (items["case_count"], items["event_count"], items["object_event_count"]) == (3, 4, 5)
    assert items["total_cost"] == 22  # place order (10) counted once although it relates to I1 and I2
    o1 = client.get(f"/api/datasets/{ds['id']}/ocel/objects/O1").json()
    assert [(a["name"], a["value"]) for a in o1["attributes"]] == [("amount", "100"), ("amount", "120")]  # history
    i1 = client.get(f"/api/datasets/{ds['id']}/ocel/objects/I1").json()
    assert {(e["event_type"], e["qualifier"]) for e in i1["events"]} == {("place order", "item"), ("pick", "item")}


def test_object_type_conflicts_are_rejected(client):
    up = upload(client, CSV.replace(b"I3", b"O1"), "conflict.csv")
    v = client.post(f"/api/uploads/{up['upload_id']}/validate", json={"mapping": MAPPING}).json()
    assert "object_type_conflict" in v["counts_by_code"] and not v["can_import_with_reject_policy"]
    run(client, up["upload_id"], MAPPING, expect="failed")


def test_case_column_is_single_valued(client):
    up = upload(client, CSV, "multi_case.csv")  # "O1;O2" in the case column is one (odd) id, not two objects
    job = run(client, up["upload_id"], MAPPING)
    ds = client.get(f"/api/datasets/{job['dataset_id']}").json()
    assert {t["name"]: t["objects"] for t in ds["object_types"]} == {"order": 3, "item": 3}


def test_xes_trace_and_attribute_objects(client):
    p = upload(client, XES.read_bytes(), "log.xes")
    assert p["suggested_case_attributes"] == ["case:customer"]
    mapping = {k: v for k, v in p["suggested_mapping"].items() if v}
    mapping |= {"case_object_type": "claim", "case_attributes": p["suggested_case_attributes"],
                "object_columns": [{"column": "org:resource", "object_type": "resource"}]}
    mapping.pop("resource")  # org:resource now identifies objects instead of being the resource field
    job = run(client, p["upload_id"], mapping, dataset_name="claims")
    ds = client.get(f"/api/datasets/{job['dataset_id']}").json()
    assert ds["main_object_type"] == "claim" and ds["import_options"]["source_format"] == "xes"
    assert {t["name"]: t["objects"] for t in ds["object_types"]} == {"claim": 2, "resource": 2}
    ann = client.get(f"/api/datasets/{ds['id']}/cases/ann", params={"object_type": "resource"}).json()
    assert [e["activity"] for e in ann["events"]] == ["A", "A"]  # one complete A in each trace (pairing mode)
    assert ann["events"][0]["start_timestamp"].startswith("2024-01-01T07:00:00")
    claim = client.get(f"/api/datasets/{ds['id']}/ocel/objects/007").json()
    assert [(a["name"], a["value"]) for a in claim["attributes"]] == [("case:customer", "ACME")]
    stats = client.get(f"/api/datasets/{ds['id']}/ocel/stats").json()
    assert stats["shared_events"] == 3  # every event with a resource relates to its claim and that resource
