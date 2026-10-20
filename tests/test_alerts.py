"""Alert rules: creation, idempotent evaluation and status handling."""

from __future__ import annotations

from tests.conftest import HAND_CHECK, HAND_MAPPING, HAND_OPTIONS, run_import


def make_rule(client, ds, **body):
    r = client.post(f"/api/datasets/{ds}/alert-rules", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def evaluate(client, ds):
    r = client.post(f"/api/datasets/{ds}/alert-rules/evaluate")
    assert r.status_code == 200, r.text
    return r.json()


def alerts(client, ds, **params):
    return client.get("/api/alerts", params={"dataset_id": ds, **params}).json()


def test_evaluation_is_idempotent(client, hand_dataset):
    make_rule(client, hand_dataset, name="Long", alert_type="duration_threshold",
              params={"threshold_hours": 3, "metric": "observed_duration"})
    first = evaluate(client, hand_dataset)
    assert (first["created"], first["updated"]) == (2, 0)  # C1 (4 h) and C2 (6 h)
    second = evaluate(client, hand_dataset)
    assert (second["created"], second["updated"]) == (0, 2)
    assert alerts(client, hand_dataset)["total"] == 2


def test_status_survives_reevaluation_and_condition_tracking(client, hand_dataset):
    rule = make_rule(client, hand_dataset, name="Rework", alert_type="rework", params={})
    evaluate(client, hand_dataset)
    items = alerts(client, hand_dataset)["items"]
    assert [a["case_id"] for a in items] == ["C2"]
    a = items[0]
    r = client.patch(f"/api/alerts/{a['id']}", json={"status": "acknowledged", "note": "checking"})
    assert r.status_code == 200 and r.json()["acknowledged_at"]
    evaluate(client, hand_dataset)
    again = alerts(client, hand_dataset)["items"][0]
    assert again["status"] == "acknowledged" and again["note"] == "checking"
    # make the condition stop matching: only alert on repeated 'D'
    client.put(f"/api/datasets/{hand_dataset}/alert-rules/{rule['id']}",
               json={"name": "Rework D", "alert_type": "rework", "params": {"activity": "D"}})
    res = evaluate(client, hand_dataset)
    assert res["no_longer_matching"] == 1
    assert alerts(client, hand_dataset)["items"][0]["condition_active"] is False
    assert alerts(client, hand_dataset, active_only="true")["total"] == 0
    r = client.patch(f"/api/alerts/{a['id']}", json={"status": "resolved"})
    assert r.json()["status"] == "resolved" and r.json()["resolved_at"]
    assert client.get("/api/alerts/counts", params={"dataset_id": hand_dataset}).json() == \
        {"open": 0, "acknowledged": 0, "resolved": 1}


def test_open_case_age_and_conformance_alerts(client, hand_dataset):
    make_rule(client, hand_dataset, name="Old open", alert_type="duration_threshold",
              params={"threshold_hours": 24, "metric": "open_case_age"})
    cr = client.post(f"/api/datasets/{hand_dataset}/conformance-rules",
                     json={"name": "D at end", "rule_type": "end_activity",
                           "params": {"activities": ["D"], "assume_complete": True}}).json()
    make_rule(client, hand_dataset, name="Bad end", alert_type="conformance_violation", conformance_rule_id=cr["id"])
    evaluate(client, hand_dataset)
    by_rule = {}
    for a in alerts(client, hand_dataset)["items"]:
        by_rule.setdefault(a["rule_name"], []).append(a["case_id"])
    assert by_rule["Old open"] == ["C4"]  # 40 h at the reference time; 007 is only 16 h old
    assert sorted(by_rule["Bad end"]) == ["007", "C4"]


def test_conformance_alert_requires_rule(client, hand_dataset):
    r = client.post(f"/api/datasets/{hand_dataset}/alert-rules",
                    json={"name": "x", "alert_type": "conformance_violation"})
    assert r.status_code == 422
    r = client.post(f"/api/datasets/{hand_dataset}/alert-rules",
                    json={"name": "x", "alert_type": "conformance_violation", "conformance_rule_id": 12345})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_reference"


def test_rules_evaluated_after_reimport_without_duplicates(client, hand_dataset):
    make_rule(client, hand_dataset, name="Long", alert_type="duration_threshold",
              params={"threshold_hours": 3, "metric": "observed_duration"})
    evaluate(client, hand_dataset)
    job = run_import(client, HAND_CHECK.read_bytes(), HAND_MAPPING,
                     **{**HAND_OPTIONS, "target_dataset_id": hand_dataset})
    assert job["result"]["version"] == 2
    assert job["result"]["alerts"]["created"] == 0
    assert job["result"]["alerts"]["updated"] == 2
    assert alerts(client, hand_dataset)["total"] == 2
