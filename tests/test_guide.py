"""Guided analysis: each question is answered deterministically with full provenance."""

from __future__ import annotations

from pathlib import Path

from app.services import jobs
from tests.conftest import import_ocel

FIXTURE_OCEL = Path(__file__).parent / "fixtures" / "ocel_small.json"


def ask(client, question: str, **params):
    return client.get("/api/guide/answer", params={"question": question, **params})


def test_question_catalog(client):
    qs = client.get("/api/guide/questions").json()["questions"]
    assert [q["id"] for q in qs] == ["longest_transitions", "rework_variants", "sla_violations",
                                     "department_differences", "object_relationship_delays"]
    assert all(q["definition_key"] and q["metric_key"] for q in qs)
    assert client.get("/guide").status_code == 200


def test_longest_transitions(client, hand_dataset):
    r = ask(client, "longest_transitions", dataset_id=hand_dataset, statistic="mean", min_cases=3)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "answered"
    assert [row["transition"] for row in a["table"]["rows"]] == ["C → D", "A → B", "B → C"]
    assert a["headline"]["params"]["seconds"] == 4800 and a["headline"]["params"]["cases"] == 3
    assert a["chart"]["values"] == [4800, 4200, 4200]
    pv = a["provenance"]
    assert (pv["dataset_id"], pv["dataset_version"], pv["population"]["cases"]) == (hand_dataset, 1, 5)
    assert pv["sample"]["n"] == 9  # occurrences of the three transitions
    assert a["metric"]["definition_key"] == "guide.def.transition_elapsed"
    assert any("/performance?" in link["href"] for link in a["links"])
    # deterministic
    assert ask(client, "longest_transitions", dataset_id=hand_dataset, statistic="mean", min_cases=3).json() == a

    none = ask(client, "longest_transitions", dataset_id=hand_dataset, min_cases=10).json()
    assert none["status"] == "not_answerable" and none["reason"]["params"]["available"] == 5


def test_filters_are_applied_and_reported(client, hand_dataset):
    a = ask(client, "longest_transitions", dataset_id=hand_dataset, statistic="mean", min_cases=1,
            status="completed").json()
    assert a["provenance"]["filters"] == {"status": ["completed"]}
    assert a["provenance"]["population"] == {"cases": 3, "total_cases": 5}
    assert "A → C" not in [row["transition"] for row in a["table"]["rows"]]  # only C4 (open) has A → C
    assert "status=completed" in a["links"][0]["href"]


def test_rework_variants(client, hand_dataset):
    a = ask(client, "rework_variants", dataset_id=hand_dataset).json()
    assert a["status"] == "answered"
    assert a["headline"]["params"] == {"cases": 1, "share": 0.2, "variants": 1}
    assert a["table"]["rows"][0]["variant"].count("B") == 2 and a["table"]["rows"][0]["repeated"] == 1


def test_sla_violations(client, hand_dataset):
    none = ask(client, "sla_violations", dataset_id=hand_dataset).json()
    assert none["status"] == "not_answerable" and none["reason"]["key"] == "guide.r.no_sla"

    a = ask(client, "sla_violations", dataset_id=hand_dataset, max_hours=5).json()
    hp = a["headline"]["params"]
    assert (hp["violating"], hp["evaluable"], hp["not_evaluable"]) == (1, 3, 2)  # only completed cases count
    assert [row["case_id"] for row in a["table"]["rows"]] == ["C2"]
    assert a["chart"]["threshold"] == 5

    b = ask(client, "sla_violations", dataset_id=hand_dataset, max_hours=3, metric="observed_duration").json()
    assert (b["headline"]["params"]["violating"], b["headline"]["params"]["evaluable"]) == (2, 5)

    rule = client.post(f"/api/datasets/{hand_dataset}/conformance-rules", json={
        "name": "SLA", "rule_type": "max_duration", "params": {"max_hours": 3, "metric": "completed_throughput"}})
    assert rule.status_code == 201, rule.text
    c = ask(client, "sla_violations", dataset_id=hand_dataset).json()
    assert c["params"]["max_hours"] == 3 and "guide.sla_from_rule" in c["caveat_keys"]
    assert c["headline"]["params"]["violating"] == 2  # C1 4 h, C2 6 h


def test_department_differences(client, hand_dataset):
    a = ask(client, "department_differences", dataset_id=hand_dataset, min_cases=1).json()
    assert a["status"] == "answered"
    hp = a["headline"]["params"]
    assert (hp["slowest"], hp["slowest_s"], hp["fastest"], hp["fastest_s"]) == ("D2", 10800, "D1", 7200)
    few = ask(client, "department_differences", dataset_id=hand_dataset, min_cases=5).json()
    assert few["status"] == "not_answerable" and few["reason"]["params"]["found"] == 1


def test_object_relationship_delays(client):
    small = import_ocel(client, FIXTURE_OCEL.read_bytes(), "item")["dataset_id"]
    a = ask(client, "object_relationship_delays", dataset_id=small).json()
    assert a["status"] == "not_answerable"  # every item has exactly one order and one package
    r = client.post("/api/demo/load")
    jobs.wait_for(r.json()["id"])
    demo = client.get(f"/api/jobs/{r.json()['id']}").json()["result"]["ocel_demo_dataset_id"]
    b = ask(client, "object_relationship_delays", dataset_id=demo).json()  # main type: order
    assert b["status"] == "answered", b
    assert b["provenance"]["object_type"] == "order" and b["provenance"]["sample"]["n"] == 60
    assert all(row["fewest_objects"] >= 3 and row["most_objects"] >= 3 for row in b["table"]["rows"])
    assert ask(client, "object_relationship_delays", dataset_id=demo, object_type="car").status_code == 422


def test_invalid_requests(client, hand_dataset):
    assert ask(client, "what_is_love", dataset_id=hand_dataset).status_code == 422
    assert ask(client, "rework_variants").status_code == 422  # needs a dataset
    assert ask(client, "longest_transitions", dataset_id=hand_dataset, statistic="mode").status_code == 422
    assert ask(client, "longest_transitions", dataset_id=hand_dataset, bogus=1).status_code == 422
    assert ask(client, "sla_violations", dataset_id=hand_dataset, max_hours=0).status_code == 422
    assert ask(client, "rework_variants", dataset_id=999).status_code == 404
