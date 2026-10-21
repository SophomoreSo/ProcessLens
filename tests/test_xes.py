"""XES import: conversion to the import table, lifecycle handling, safety checks, round trip."""

from __future__ import annotations

import gzip
from pathlib import Path

from app.services import jobs
from tests.conftest import HAND_CHECK, HAND_MAPPING, HAND_OPTIONS, run_import

FIXTURE = Path(__file__).parent / "fixtures" / "lifecycle.xes"


def upload(client, content: bytes, name: str = "log.xes"):
    return client.post("/api/uploads", files={"file": (name, content, "application/xml")})


def import_upload(client, upload_id: str, mapping: dict, **options) -> dict:
    r = client.post(f"/api/uploads/{upload_id}/import", json={"mapping": mapping, **options})
    assert r.status_code == 202, r.text
    jobs.wait_for(r.json()["id"])
    return client.get(f"/api/jobs/{r.json()['id']}").json()


def test_xes_upload_is_converted_and_mapping_suggested(client):
    r = upload(client, FIXTURE.read_bytes())
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["source_format"] == "xes"
    c = p["conversion"]
    assert c["lifecycle_mode"] == "pair_start_complete" and c["lifecycle_mode_chosen"] == "auto_start_events"
    assert (c["traces"], c["events_in_file"], c["rows"], c["empty_traces"]) == (3, 7, 3, 1)
    assert (c["paired_start_events"], c["starts_without_complete"], c["dropped_transitions"]) == (2, 1, {"schedule": 1})
    assert c["complex_attributes_skipped"] == {"tags": 1} and c["meta_attributes_skipped"] == 1
    assert c["global_attributes"] == ["org:resource"] and c["classifiers"] == ["Activity"]
    assert p["columns"][:4] == ["case:concept:name", "concept:name", "time:timestamp", "start_timestamp"]
    assert "case:customer" in p["columns"] and "lifecycle:transition" not in p["columns"]
    m = p["suggested_mapping"]
    assert (m["case_id"], m["activity"], m["timestamp"], m["start_timestamp"], m["resource"], m["cost"]) == (
        "case:concept:name", "concept:name", "time:timestamp", "start_timestamp", "org:resource", "cost:total")
    assert p["row_count"] == 3
    assert {f["key"] for f in p["conversion_findings"]} >= {"xes.finding.dropped_transitions",
                                                              "xes.finding.starts_without_complete"}
    rows = p["sample_rows"]
    assert rows[0]["case:concept:name"] == "007" and rows[0]["start_timestamp"] == "2024-01-01T08:00:00.000+01:00"
    assert rows[1]["concept:name"] == "B" and rows[1]["start_timestamp"] == ""


def test_lifecycle_can_be_switched_and_import_keeps_identifiers(client):
    up = upload(client, FIXTURE.read_bytes()).json()
    r = client.post(f"/api/uploads/{up['upload_id']}/xes-options", json={"lifecycle": "all_events"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["row_count"] == 7 and "lifecycle:transition" in p["columns"] and "start_timestamp" not in p["columns"]
    assert p["conversion"]["lifecycle_mode_chosen"] == "user"
    p = client.post(f"/api/uploads/{up['upload_id']}/xes-options", json={"lifecycle": "pair_start_complete"}).json()
    mapping = {k: v for k, v in p["suggested_mapping"].items() if v} | {"attributes": ["case:customer"]}
    job = import_upload(client, up["upload_id"], mapping, dataset_name="xes")
    assert job["status"] == "succeeded", job
    ds = client.get(f"/api/datasets/{job['dataset_id']}").json()
    assert (ds["event_count"], ds["case_count"]) == (3, 2)
    assert ds["import_options"]["source_format"] == "xes"
    assert ds["import_options"]["xes_conversion"]["lifecycle_mode"] == "pair_start_complete"
    first = client.get(f"/api/datasets/{job['dataset_id']}/cases/007").json()["events"][0]
    assert first["activity"] == "A" and first["timestamp"].startswith("2024-01-01T08:00:00")  # 09:00+01:00 in UTC
    assert first["start_timestamp"].startswith("2024-01-01T07:00:00")
    assert first["resource"] == "ann" and first["cost"] == 12.5
    assert first["attributes"]["case:customer"] == "ACME"


def test_gzip_and_invalid_files(client):
    assert upload(client, gzip.compress(FIXTURE.read_bytes()), "log.xes.gz").status_code == 201
    r = upload(client, b'<?xml version="1.0"?><!DOCTYPE log [<!ENTITY a "aaaa">]><log><trace/></log>')
    assert r.status_code == 422 and r.json()["error"]["code"] == "xes_dtd"
    assert upload(client, b"<log><trace><event>").json()["error"]["code"] == "xes_invalid"
    assert upload(client, b"case_id,activity\n1,A\n").json()["error"]["code"] == "xes_no_log"
    assert upload(client, b'<log xes.version="1.0"></log>').json()["error"]["code"] == "xes_no_traces"
    assert upload(client, b"not gzip", "log.xes.gz").json()["error"]["code"] == "xes_invalid"
    assert upload(client, b"<log/>", "log.xml").json()["error"]["code"] == "unsupported_file_type"
    csv = upload(client, HAND_CHECK.read_bytes(), "x.csv").json()
    assert csv["source_format"] == "csv" and csv["conversion"] is None
    assert client.post(f"/api/uploads/{csv['upload_id']}/xes-options", json={"lifecycle": "all_events"}).status_code == 422


def test_trace_without_name_fails_validation(client):
    xml = FIXTURE.read_text().replace('<string key="concept:name" value="C2"/>', "")
    p = upload(client, xml.encode()).json()
    assert p["conversion"]["traces_without_case_id"] == 1
    assert any(f["severity"] == "error" for f in p["conversion_findings"])
    mapping = {k: v for k, v in p["suggested_mapping"].items() if v}
    v = client.post(f"/api/uploads/{p['upload_id']}/validate", json={"mapping": mapping}).json()
    assert v["invalid_rows"] == 1 and "missing_case_id" in v["counts_by_code"]


def test_round_trip_with_xes_export(client):
    job = run_import(client, HAND_CHECK.read_bytes(), HAND_MAPPING, **HAND_OPTIONS)
    ds = job["dataset_id"]
    xes = client.get(f"/api/datasets/{ds}/exports/events.xes")
    assert xes.status_code == 200
    assert b'value="nan"' not in xes.content and b'value=""' not in xes.content  # missing values are omitted
    p = upload(client, xes.content, "hand.xes").json()
    assert p["conversion"]["lifecycle_mode"] == "all_events" and p["row_count"] == 16
    mapping = {k: v for k, v in p["suggested_mapping"].items() if v}
    job2 = import_upload(client, p["upload_id"], mapping)
    assert job2["status"] == "succeeded", job2
    a = client.get(f"/api/datasets/{ds}/overview").json()["data"]
    b = client.get(f"/api/datasets/{job2['dataset_id']}/overview").json()["data"]
    for key in ("case_count", "event_count", "activity_count", "variant_count", "rework_case_count"):
        assert a[key] == b[key], key
    assert a["total_cost"] == b["total_cost"]
