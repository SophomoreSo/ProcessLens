"""Import workflow, security controls, exports and persistence through the HTTP API."""

from __future__ import annotations

import csv
import io
import json

from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, get_settings
from app.database import session_scope
from app.main import app
from app.models import ImportJob
from app.security import csrf_token
from app.services.jobs import recover_interrupted_jobs
from tests.conftest import HAND_CHECK, HAND_MAPPING, HAND_OPTIONS, run_import, upload

VALIDATION_DEMO = PROJECT_ROOT / "sample_data" / "p2p_validation_demo.csv"
DEMO_MAPPING = {"case_id": "case_id", "activity": "activity", "timestamp": "timestamp", "event_id": "event_id",
                "start_timestamp": "start_timestamp", "resource": "resource", "department": "department",
                "cost": "cost", "case_status": "case_status"}


def test_health_and_session(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["database"] == "ok"
    assert client.get("/api/session").json()["csrf_token"] == csrf_token()
    assert "openapi" in client.get("/openapi.json").json()


def test_csrf_and_origin_required_for_state_changes(client):
    bare = TestClient(app)
    r = bare.post("/api/demo/load")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.post("/api/demo/load", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"
    assert bare.get("/api/datasets").status_code == 200  # reads need no token


def test_untrusted_host_rejected(client):
    r = client.get("/api/health", headers={"Host": "attacker.example"})
    assert r.status_code == 400


def test_security_headers(client):
    r = client.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_upload_preview_and_suggestions(client):
    p = upload(client, HAND_CHECK.read_bytes(), "hand.csv")
    assert p["row_count"] == 16
    assert p["suggested_mapping"]["case_id"] == "case_id"
    assert p["suggested_mapping"]["case_status"] == "status"
    assert p["suggested_timestamp_format"] == "ISO8601"
    assert p["sample_rows"][-1]["case_id"] == "007"  # leading zeros kept in preview


def test_upload_rejections(client):
    r = client.post("/api/uploads", files={"file": ("x.xlsx", b"PK\x03\x04", "application/octet-stream")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_file_type"
    r = client.post("/api/uploads", files={"file": ("x.csv", b"a,b\x00\x00c", "text/csv")})
    assert r.json()["error"]["code"] == "binary_file"
    r = client.post("/api/uploads", files={"file": ("x.csv", b"", "text/csv")})
    assert r.json()["error"]["code"] == "empty_file"
    settings = get_settings()
    old = settings.max_upload_mb
    settings.max_upload_mb = 0
    try:
        r = client.post("/api/uploads", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
        assert r.status_code == 413
    finally:
        settings.max_upload_mb = old
    assert not any((settings.upload_dir).glob("*.csv"))  # rejected files are not kept


def test_generated_storage_names(client):
    p = upload(client, HAND_CHECK.read_bytes(), "../../etc/passwd.csv")
    stored = list(get_settings().upload_dir.glob("*.csv"))
    assert [s.name for s in stored] == [f"{p['upload_id']}.csv"]
    assert p["original_filename"] == "passwd.csv"


def test_validate_endpoint_and_report(client):
    p = upload(client, VALIDATION_DEMO.read_bytes(), "bad.csv")
    body = {"mapping": DEMO_MAPPING, "timestamps": {"format": "ISO8601", "source_timezone": "Europe/Berlin"}}
    v = client.post(f"/api/uploads/{p['upload_id']}/validate", json=body).json()
    assert v["total_rows"] == 17
    assert v["invalid_rows"] >= 8
    for code in ("missing_case_id", "empty_activity", "malformed_timestamp", "duplicate_event_id", "invalid_cost",
                 "start_after_end", "ambiguous_local_time", "nonexistent_local_time", "field_count_mismatch"):
        assert v["counts_by_code"].get(code), code
    assert v["can_import_with_reject_policy"] is False
    rep = client.get(f"/api/uploads/{p['upload_id']}/validation-report", params={"format": "csv"})
    assert rep.status_code == 200 and "attachment" in rep.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(rep.text)))
    assert {"severity", "code", "row", "message"} <= set(rows[0])
    bad = client.post(f"/api/uploads/{p['upload_id']}/validate", json={"mapping": {**DEMO_MAPPING, "case_id": "nope"}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_mapping"


def test_reject_policy_refuses_import(client):
    job = run_import(client, VALIDATION_DEMO.read_bytes(), DEMO_MAPPING, expect="failed",
                     timestamps={"source_timezone": "Europe/Berlin"})
    assert "rows are invalid" in job["error"]
    assert client.get("/api/datasets").json() == []


def test_quarantine_policy_imports_valid_rows(client):
    job = run_import(client, VALIDATION_DEMO.read_bytes(), DEMO_MAPPING, invalid_row_policy="quarantine",
                     timestamps={"source_timezone": "Europe/Berlin"})
    ds = job["dataset_id"]
    meta = client.get(f"/api/datasets/{ds}").json()
    assert meta["source_row_count"] == 17
    assert meta["event_count"] + meta["quarantined_count"] == 17
    q = client.get(f"/api/datasets/{ds}/quarantine").json()
    assert q["total"] == meta["quarantined_count"]
    assert all(item["reasons"] for item in q["items"])
    rep = client.get(f"/api/datasets/{ds}/validation-report").json()
    assert rep["counts_by_code"]["duplicate_event_id"] == 2
    qcsv = client.get(f"/api/datasets/{ds}/quarantine", params={"format": "csv"}).text
    assert "source_row,reasons" in qcsv.splitlines()[0]
    # formula-looking activity was valid and kept, but is neutralized in CSV exports
    events_csv = client.get(f"/api/datasets/{ds}/exports/events.csv").text
    assert "'=HYPERLINK" in events_csv
    events_json = json.loads(client.get(f"/api/datasets/{ds}/exports/events.json").text)
    assert any(e["activity"].startswith("=HYPERLINK") for e in events_json["items"])  # JSON keeps raw value


def test_reimport_creates_new_version_and_invalidates_cache(client, hand_dataset):
    before = client.get(f"/api/datasets/{hand_dataset}/overview").json()
    assert before["dataset_version"] == 1 and before["data"]["case_count"] == 5
    smaller = "\n".join(HAND_CHECK.read_text().splitlines()[:5]) + "\n"
    job = run_import(client, smaller, HAND_MAPPING, **{**HAND_OPTIONS, "target_dataset_id": hand_dataset})
    assert job["dataset_id"] == hand_dataset
    after = client.get(f"/api/datasets/{hand_dataset}/overview").json()
    assert after["dataset_version"] == 2
    assert after["data"]["case_count"] == 3


def test_delete_requires_matching_name(client, hand_dataset):
    r = client.delete(f"/api/datasets/{hand_dataset}", params={"confirm_name": "wrong"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "confirmation_mismatch"
    assert client.delete(f"/api/datasets/{hand_dataset}").status_code == 422  # missing confirmation
    r = client.delete(f"/api/datasets/{hand_dataset}", params={"confirm_name": "hand-check"})
    assert r.status_code == 204
    assert client.get(f"/api/datasets/{hand_dataset}").status_code == 404
    assert client.get(f"/api/datasets/{hand_dataset}/overview").json()["error"]["code"] == "not_found"
    assert not any(get_settings().upload_dir.glob("*.csv"))


def test_exports(client, hand_dataset):
    base = f"/api/datasets/{hand_dataset}/exports"
    cases = list(csv.DictReader(io.StringIO(client.get(f"{base}/cases.csv").text)))
    assert len(cases) == 5 and {"007", "C1"} <= {c["case_id"] for c in cases}
    summary = client.get(f"{base}/summary.json").json()
    assert summary["overview"]["case_count"] == 5 and summary["dataset_version"] == 1
    assert client.get(f"{base}/summary.csv").status_code == 422
    variants = client.get(f"{base}/variants.json").json()
    assert len(variants["items"]) == 4 and variants["filters"] == {}
    trans = list(csv.DictReader(io.StringIO(client.get(f"{base}/transitions.csv").text)))
    assert len(trans) == 5
    assert client.get(f"{base}/conformance.csv").status_code == 422  # rule_id required
    assert client.get(f"{base}/bogus.csv").status_code == 422
    xes = client.get(f"{base}/events.xes", params={"activity": "B"})
    assert xes.status_code == 200 and b"<log" in xes.content and xes.content.count(b"<trace") == 3


def test_csv_formula_escaping():
    from app.services.exports import safe_cell, to_csv
    assert safe_cell("=1+1") == "'=1+1"
    assert safe_cell("+cmd") == "'+cmd"
    assert safe_cell("-x") == "'-x"
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell(-5.0) == -5.0  # numbers untouched
    assert safe_cell("plain") == "plain"
    assert to_csv([{"a": "=2"}], ["a"]).splitlines() == ["a", "'=2"]


def test_validation_error_shape(client):
    r = client.post("/api/uploads/" + "0" * 32 + "/validate", json={"mapping": {}})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and isinstance(err["details"], list)
    assert client.get("/api/datasets/999/overview").status_code == 404


def test_data_persists_across_restart(data_dir):
    with TestClient(app) as c:
        c.headers["X-CSRF-Token"] = csrf_token()
        ds = run_import(c, HAND_CHECK.read_bytes(), HAND_MAPPING, **HAND_OPTIONS)["dataset_id"]
        c.post(f"/api/datasets/{ds}/alert-rules", json={"name": "rw", "alert_type": "rework", "params": {}})
        c.post(f"/api/datasets/{ds}/alert-rules/evaluate")
    from app.services.cache import cache
    cache.clear()  # simulate a fresh process: nothing served from memory
    with TestClient(app) as c:
        assert c.get(f"/api/datasets/{ds}/overview").json()["data"]["case_count"] == 5
        assert c.get("/api/alerts", params={"dataset_id": ds}).json()["total"] == 1


def test_interrupted_jobs_marked_after_restart(client):
    with session_scope() as db:
        db.add(ImportJob(id="a" * 32, status="running", progress=0.4, stage="Writing events", options={}))
    assert recover_interrupted_jobs() == 1
    job = client.get("/api/jobs/" + "a" * 32).json()
    assert job["status"] == "interrupted" and "No data from it was committed" in job["error"]


def test_demo_load(client):
    r = client.post("/api/demo/load")
    assert r.status_code == 202
    from app.services import jobs
    jobs.wait_for(r.json()["id"])
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job
    ds = client.get(f"/api/datasets/{job['dataset_id']}").json()
    assert ds["is_demo"] and "SYNTHETIC" in ds["description"]
    assert ds["case_count"] == 600 and ds["quarantined_count"] == 0
    ov = client.get(f"/api/datasets/{ds['id']}/overview").json()["data"]
    assert ov["status_counts"]["incomplete"] > 0 and ov["rework_case_count"] > 0 and ov["variant_count"] >= 5
    rules = client.get(f"/api/datasets/{ds['id']}/conformance-rules").json()
    assert len(rules) == 6
    assert job["result"]["alerts"]["created"] > 0
