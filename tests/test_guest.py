"""Admin and guest sites: the guest layout is read-only and enforced on the server."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.__main__ import main as launcher
from app.config import get_settings
from app.main import guest_app
from app.security import csrf_token

ADMIN_ONLY_PAGES = ["/import", "/datasets"]
GUEST_PAGES = ["/", "/guide", "/process", "/variants", "/cases", "/performance", "/conformance", "/alerts",
               "/discovery", "/compare", "/relationships", "/tasks", "/settings"]


@pytest.fixture
def guest(client):  # depends on ``client`` so both sites share the test database
    with TestClient(guest_app) as g:
        g.headers["X-CSRF-Token"] = csrf_token("guest")  # even a valid token does not allow writes
        yield g


def test_guest_layout_has_no_admin_pages_or_write_controls(client, guest, hand_dataset):
    for path in GUEST_PAGES:
        r = guest.get(path)
        assert r.status_code == 200, path
        assert 'data-role="guest"' in r.text
        assert not re.search(r'href="/(import|datasets)"', r.text), path
    for path in ADMIN_ONLY_PAGES:
        assert guest.get(path).status_code == 404
        assert client.get(path).status_code == 200
    assert 'data-role="admin"' in client.get("/").text
    admin_conf, guest_conf = client.get("/conformance").text, guest.get("/conformance").text
    assert 'id="new-rule"' in admin_conf and 'id="new-rule"' not in guest_conf
    assert 'id="run-discovery"' in client.get("/discovery").text
    assert 'id="run-discovery"' not in guest.get("/discovery").text
    assert guest.get("/ocel").status_code == 404 and guest.get("/relationships").status_code == 200
    assert 'id="preview-form"' not in guest.get("/tasks").text
    assert guest.get("/docs").status_code == 404 and guest.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 200


def test_guest_reads_everything_the_admin_has(client, guest, hand_dataset):
    assert [d["id"] for d in guest.get("/api/datasets").json()] == [hand_dataset]
    assert guest.get(f"/api/datasets/{hand_dataset}/overview").json()["data"]["case_count"] == 5
    assert guest.get(f"/api/datasets/{hand_dataset}/exports/cases.csv").status_code == 200
    r = guest.get("/api/guide/answer", params={"question": "rework_variants", "dataset_id": hand_dataset})
    assert r.status_code == 200 and r.json()["status"] == "answered"
    assert guest.get("/api/mining/runs", params={"dataset_id": hand_dataset}).status_code == 200
    # stateless analysis is allowed
    r = guest.post(f"/api/datasets/{hand_dataset}/performance/compare", json={"a": {"rework": True}, "b": {"rework": False}})
    assert r.status_code == 200, r.text
    # new admin data is visible immediately
    client.post(f"/api/datasets/{hand_dataset}/conformance-rules",
                json={"name": "Ends with D", "rule_type": "end_activity", "params": {"activities": ["D"]}})
    assert [x["name"] for x in guest.get(f"/api/datasets/{hand_dataset}/conformance-rules").json()] == ["Ends with D"]


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/datasets/{ds}/conformance-rules", {"name": "x", "rule_type": "end_activity", "params": {"activities": ["D"]}}),
    ("delete", "/api/datasets/{ds}?confirm_name=hand-check", None),
    ("patch", "/api/datasets/{ds}", {"name": "renamed"}),
    ("post", "/api/datasets/{ds}/alert-rules/evaluate", None),
    ("post", "/api/datasets/{ds}/mining/discover", {"algorithm_id": "inductive"}),
    ("post", "/api/demo/load", None),
    ("post", "/api/ocel/demo", None),
    ("post", "/api/tasks/demo", None),
    ("put", "/api/preferences", {"language": "ko"}),
    ("post", "/api/uploads", None),
])
def test_guest_writes_are_refused(client, guest, hand_dataset, method, path, body):
    r = getattr(guest, method)(path.format(ds=hand_dataset), **({"json": body} if body is not None else {}))
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "read_only"
    assert client.get(f"/api/datasets/{hand_dataset}").json()["name"] == "hand-check"
    assert client.get("/api/preferences").json()["language"] == "en"


def test_guest_site_has_its_own_csrf_token(client, guest, hand_dataset):
    page = guest.get("/").text
    assert csrf_token("guest") in page and csrf_token() not in page
    assert csrf_token() in client.get("/").text
    assert guest.get("/api/session").json()["csrf_token"] == csrf_token("guest")
    body = {"a": {"rework": True}, "b": {"rework": False}}
    url = f"/api/datasets/{hand_dataset}/performance/compare"
    assert guest.post(url, json=body, headers={"X-CSRF-Token": csrf_token()}).status_code == 403
    assert client.post(url, json=body, headers={"X-CSRF-Token": csrf_token("guest")}).status_code == 403


def test_guest_blocks_staged_uploads(client, guest):
    up = client.post("/api/uploads", files={"file": ("x.csv", b"case_id,activity,timestamp\n1,A,2024-01-01\n", "text/csv")})
    assert up.status_code == 201
    assert guest.get(f"/api/uploads/{up.json()['upload_id']}").status_code == 403


def test_guest_language_lives_in_a_cookie(client, guest):
    guest.cookies.set("processlens_guest_lang", "ko")
    assert '<html lang="ko"' in guest.get("/").text
    assert '<html lang="en"' in client.get("/").text  # the admin's saved preference is untouched
    assert client.get("/api/preferences").json()["language"] == "en"
    guest.cookies.set("processlens_guest_lang", "xx")  # unknown -> admin's saved language
    assert '<html lang="en"' in guest.get("/").text


def test_launcher_keeps_admin_on_loopback(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    monkeypatch.setattr(settings, "allow_non_loopback_bind", False)
    assert launcher([]) == 2
    assert launcher(["--guest"]) == 2
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "guest_port", settings.port)
    assert launcher(["--guest"]) == 2
