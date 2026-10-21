"""Task mining: CSV import with privacy checks, sessionization, variants, sequences, switches, case links."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from app.database import session_scope
from app.models import TaskEvent
from tests.conftest import HAND_CHECK, HAND_MAPPING, HAND_OPTIONS, run_import

FIXTURE = Path(__file__).parent / "fixtures" / "task_events_small.csv"
MAPPING = {"session_id": "session_id", "timestamp": "timestamp", "application": "application", "action": "action",
           "user_id": "pseudonymous_user_id", "business_case_id": "business_case_id", "duration": "duration"}


def _upload(client, content: bytes | None = None, mapping: dict | None = None, **form):
    data = {"mapping": json.dumps(mapping or MAPPING), **{k: str(v) for k, v in form.items()}}
    return client.post("/api/tasks/upload", files={"file": ("tasks.csv", content or FIXTURE.read_bytes(), "text/csv")},
                       data=data)


def _log(client, **form) -> int:
    r = _upload(client, **form)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_preview_suggests_mapping(client):
    r = client.post("/api/tasks/preview", files={"file": ("t.csv", FIXTURE.read_bytes(), "text/csv")})
    assert r.status_code == 200
    assert r.json()["suggested_mapping"] == MAPPING
    assert r.json()["rows"] == 11


def test_import_stores_only_mapped_fields(client):
    r = _upload(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["counts"]["events"] == 11 and body["counts"]["users"] == 2 and body["counts"]["events_with_duration"] == 3
    assert body["ignored_columns"] == ["window_title"]
    assert any(f["code"] == "columns_not_stored" for f in body["findings"])
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(TaskEvent)) == 11
    assert "window_title" not in {c.name for c in TaskEvent.__table__.columns}


def test_privacy_and_validation_errors(client):
    raw = FIXTURE.read_text().replace(",u2,", ",anna@example.com,")
    r = _upload(client, raw.encode(), invalid_row_policy="skip_invalid")
    assert r.status_code == 422  # identifying user ids are never imported, even when skipping invalid rows
    assert any(f["code"] == "user_id_not_pseudonymous" for f in r.json()["error"]["details"])

    raw = FIXTURE.read_text().replace("2024-01-01T09:03:00Z", "yesterday").replace(",30,", ",-3,")
    r = _upload(client, raw.encode())
    assert r.status_code == 422
    codes = {f["code"] for f in r.json()["error"]["details"]}
    assert {"invalid_timestamp", "invalid_duration"} <= codes
    r = _upload(client, raw.encode(), invalid_row_policy="skip_invalid")
    assert r.status_code == 201 and r.json()["counts"]["events"] == 9 and r.json()["counts"]["skipped_rows"] == 2

    assert _upload(client, mapping={"timestamp": "timestamp"}).json()["error"]["code"] == "missing_mapping"
    assert _upload(client, mapping={**MAPPING, "keystrokes": "window_title"}).status_code == 422
    assert _upload(client, source_timezone="Mars/Base").status_code == 422


def test_sessionization_modes(client):
    lid = _log(client)
    count = lambda **p: client.get(f"/api/tasks/{lid}/summary", params=p).json()["sessions"]
    assert count(mode="source") == 3
    assert count(mode="source_and_inactivity", gap_minutes=30) == 4
    assert count(mode="inactivity", gap_minutes=30) == 3
    assert count(mode="inactivity", gap_minutes=120) == 2
    s = client.get(f"/api/tasks/{lid}/summary", params={"mode": "source"}).json()
    assert s["explicit_duration_total_s"] == 45 and s["events_with_explicit_duration"] == 3
    assert s["median_elapsed_span_s"] == 180  # s1 60 min, s2 2 min, s3 3 min
    assert client.get(f"/api/tasks/{lid}/summary", params={"mode": "bogus"}).status_code == 422


def test_naive_timestamps_use_source_timezone(client):
    raw = b"timestamp,application,action,user\n2024-07-01 10:00:00,ERP,Open,u1\n2024-07-01T10:00:00+00:00,ERP,Close,u1\n"
    r = _upload(client, raw, mapping={"timestamp": "timestamp", "application": "application", "action": "action",
                                      "user_id": "user"}, source_timezone="Europe/Berlin")
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    s = client.get(f"/api/tasks/{lid}/summary", params={"mode": "inactivity", "gap_minutes": 180}).json()
    assert s["sessions"] == 1 and s["median_elapsed_span_s"] == 7200  # 08:00Z (Berlin summer time) to 10:00Z


def test_variants_sequences_and_switches(client):
    lid = _log(client)
    v = client.get(f"/api/tasks/{lid}/variants", params={"mode": "source"}).json()
    assert v["variant_count"] == 2
    top = v["variants"][0]
    assert top["sequence"] == ["Email · Open", "PDF · View", "ERP · Enter", "ERP · Post"] and top["sessions"] == 2
    apps = client.get(f"/api/tasks/{lid}/variants", params={"mode": "source", "granularity": "application"}).json()
    assert apps["variant_count"] == 1 and apps["variants"][0]["sequence"] == ["Email", "PDF", "ERP"]

    seq = client.get(f"/api/tasks/{lid}/sequences", params={"mode": "source"}).json()["sequences"]
    assert [(s["sequence"], s["sessions"]) for s in seq] == [
        (["Email · Open", "PDF · View", "ERP · Enter"], 3),
        (["Email · Open", "PDF · View", "ERP · Enter", "ERP · Post"], 2)]
    assert client.get(f"/api/tasks/{lid}/sequences", params={"min_length": 4, "max_length": 3}).status_code == 422

    sw = client.get(f"/api/tasks/{lid}/switches", params={"mode": "source"}).json()
    assert (sw["switches"], sw["transitions"], sw["sessions_with_switch"]) == (6, 8, 3)
    assert {(p["from"], p["to"]): p["switches"] for p in sw["pairs"]} == {("Email", "PDF"): 3, ("PDF", "ERP"): 3}

    dfg = client.get(f"/api/tasks/{lid}/dfg", params={"mode": "source"}).json()
    edges = {(e["source"], e["target"]): e["count"] for e in dfg["edges"]}
    assert edges[("ERP · Enter", "ERP · Post")] == 2
    nodes = {n["id"]: n for n in dfg["nodes"]}
    assert nodes["ERP · Enter"]["mean_explicit_duration_s"] == 30 and nodes["ERP · Enter"]["events_with_duration"] == 1
    assert nodes["PDF · View"]["mean_explicit_duration_s"] is None  # never inferred


def test_business_case_links(client):
    lid = _log(client)
    r = client.get(f"/api/tasks/{lid}/cases", params={"mode": "source"}).json()
    assert r["cases"] == 3 and r["matched_in_dataset"] is None
    c1 = next(c for c in r["items"] if c["business_case_id"] == "C1")
    assert (c1["events"], c1["sessions"], c1["explicit_duration_s"], c1["events_with_duration"]) == (4, 1, 45, 3)
    job = run_import(client, HAND_CHECK.read_bytes(), HAND_MAPPING, **HAND_OPTIONS)
    assert client.put(f"/api/tasks/{lid}/link", json={"dataset_id": job["dataset_id"]}).status_code == 200
    r = client.get(f"/api/tasks/{lid}/cases").json()
    assert r["matched_in_dataset"] == 3 and all(c["in_dataset"] for c in r["items"])
    assert client.put(f"/api/tasks/{lid}/link", json={"dataset_id": 999}).status_code == 404
    ds_name = client.get(f"/api/datasets/{job['dataset_id']}").json()["name"]
    client.delete(f"/api/datasets/{job['dataset_id']}", params={"confirm_name": ds_name})
    assert client.get(f"/api/tasks/{lid}").json()["linked_dataset_id"] is None


def test_demo_and_delete(client):
    r = client.post("/api/tasks/demo")
    assert r.status_code == 201
    lid = r.json()["id"]
    assert r.json()["counts"]["events"] > 1000 and r.json()["findings"] == []
    assert client.get("/tasks").status_code == 200
    assert client.delete(f"/api/tasks/{lid}").status_code == 204
    assert client.get(f"/api/tasks/{lid}").status_code == 404
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(TaskEvent)) == 0
