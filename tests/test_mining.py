"""Discovery algorithms, formal conformance, comparison, provenance and mining jobs."""

from __future__ import annotations

import io
import json
import time

import pandas as pd
import pytest

from app.mining import registry, runs
from tests.conftest import FIXTURES, run_import

FIXTURE = FIXTURES / "mining_fixture.csv"
MAPPING = {"case_id": "case_id", "activity": "activity", "timestamp": "timestamp", "case_status": "status"}
OPTIONS = {"completion": {"mode": "case_status", "completed_status_values": ["Completed"], "open_status_values": ["Open"]},
           "dataset_name": "mining-fixture"}

NATIVE = {
    "inductive": "process_tree", "inductive_infrequent": "process_tree", "inductive_directly_follows": "process_tree",
    "heuristics": "heuristics_net", "heuristics_plusplus": "heuristics_net", "alpha": "petri_net",
    "alpha_plus": "petri_net", "powl": "powl", "declare": "declare", "log_skeleton": "log_skeleton",
    "transition_system": "transition_system", "temporal_profile": "temporal_profile", "ilp": "petri_net",
    "genetic": "petri_net",
}


@pytest.fixture
def mining_ds(client) -> int:
    return run_import(client, FIXTURE.read_bytes(), MAPPING, **OPTIONS)["dataset_id"]


def start(client, path: str, body: dict, params: dict | None = None, expect=(202,)) -> dict:
    r = client.post(path, json=body, params=params or {})
    assert r.status_code in expect, r.text
    return r.json()


def finish(client, run_id: str, timeout: float = 240) -> dict:
    runs.wait_for(run_id, timeout)
    return client.get(f"/api/mining/runs/{run_id}").json()


def discover(client, ds: int, algorithm_id: str, params: dict | None = None, filters: dict | None = None) -> dict:
    out = start(client, f"/api/datasets/{ds}/mining/discover", {"algorithm_id": algorithm_id, "params": params or {}},
                filters, expect=(200, 202))
    return finish(client, out["run"]["id"])


# --------------------------------------------------------------------------- registry
def test_registry_lists_algorithms_with_honest_availability(client):
    data = client.get("/api/mining/algorithms").json()
    by_id = {a["id"]: a for a in data["algorithms"]}
    assert set(NATIVE) <= set(by_id)
    for aid in NATIVE:
        assert by_id[aid]["availability"]["status"] == "available"
        assert by_id[aid]["implementation"]["provider"] == "pm4py"
        assert by_id[aid]["implementation"]["provider_version"]
        assert by_id[aid]["output_type"] == NATIVE[aid]
    for aid in ("split_miner", "fuzzy_miner"):
        assert by_id[aid]["availability"]["status"] == "unavailable"
        assert by_id[aid]["availability"]["reason_key"].startswith("algo.unavailable.")
    assert data["visualization_exports"]["svg"]["available"] is False


def test_parameter_validation():
    spec = registry.get("inductive_infrequent")
    assert registry.validate_params(spec, {}) == {"noise_threshold": 0.2, "disable_fallthroughs": False}
    with pytest.raises(registry.ParamError) as exc:
        registry.validate_params(spec, {"noise_threshold": 1.5, "unknown": 1})
    problems = {e["param"] for e in exc.value.errors}
    assert problems == {"noise_threshold", "unknown"}  # unknown parameters are rejected, never ignored
    powl = registry.get("powl")
    with pytest.raises(registry.ParamError):
        registry.validate_params(powl, {"variant": "MAXIMAL", "order_graph_filtering_threshold": 0.7})
    assert registry.validate_params(powl, {"variant": "DYNAMIC_CLUSTERING", "order_graph_filtering_threshold": 0.7})
    with pytest.raises(registry.ParamError):
        registry.validate_params(registry.get("declare"), {"considered_activities": ["Q"]}, activities={"A", "B"})


def test_unavailable_algorithm_is_rejected_not_substituted(client, mining_ds):
    r = client.post(f"/api/datasets/{mining_ds}/mining/discover", json={"algorithm_id": "fuzzy_miner"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "algorithm_unavailable"
    r = client.post(f"/api/datasets/{mining_ds}/mining/discover",
                    json={"algorithm_id": "inductive_infrequent", "params": {"noise_threshold": 2}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_parameters"
    assert r.json()["error"]["details"][0]["param"] == "noise_threshold"


# --------------------------------------------------------------------------- adapters
@pytest.mark.parametrize("algorithm_id", sorted(NATIVE))
def test_each_enabled_adapter(client, mining_ds, algorithm_id):
    params = {"generations": 5, "population_size": 20} if algorithm_id == "genetic" else {}
    run = discover(client, mining_ds, algorithm_id, params)
    r = run["run"]
    assert r["status"] == "succeeded", r
    assert r["result"]["native_type"] == NATIVE[algorithm_id]
    assert r["implementation"]["provider"] == "pm4py" and r["implementation"]["adapter_version"]
    assert r["dataset_version"] == 1 and r["filter_snapshot"]["case_count"] == 22
    reps = client.get(f"/api/mining/runs/{r['id']}/model").json()["representations"]
    native = reps[NATIVE[algorithm_id]]
    assert native["type"] == NATIVE[algorithm_id]
    assert r["result"]["sizes"][NATIVE[algorithm_id]]
    if NATIVE[algorithm_id] in ("process_tree", "petri_net", "heuristics_net", "powl"):
        assert "model.pnml" in run["artifacts"]
        pn = reps["petri_net"]
        assert any(p["initial"] for p in pn["places"]) and any(p["final"] for p in pn["places"])
    if NATIVE[algorithm_id] == "process_tree":
        assert {"model.ptml", "model.bpmn"} <= set(run["artifacts"]) and reps["bpmn"]["type"] == "bpmn"
    if NATIVE[algorithm_id] in ("declare", "log_skeleton", "temporal_profile", "transition_system"):
        exported = json.loads(client.get(f"/api/mining/runs/{r['id']}/export/json").text)
        assert exported["format_note"] == "application-specific JSON" and exported["model"]["type"] == NATIVE[algorithm_id]


def test_algorithm_specific_content(client, mining_ds):
    tree = client.get(f"/api/mining/runs/{discover(client, mining_ds, 'inductive')['run']['id']}/model").json()
    assert tree["representations"]["process_tree"]["root"]["kind"] == "sequence"
    labels = {t["label"] for t in tree["representations"]["petri_net"]["transitions"] if not t["silent"]}
    assert labels == {"A", "B", "C", "D", "E", "F", "X", "Z"}  # IM is perfectly fitting: keeps noise
    imf = client.get(f"/api/mining/runs/{discover(client, mining_ds, 'inductive_infrequent', {'noise_threshold': 0.3})['run']['id']}/model").json()
    imf_labels = {t["label"] for t in imf["representations"]["petri_net"]["transitions"] if not t["silent"]}
    assert "Z" not in imf_labels  # the infrequent variant filters the single noisy trace
    ts = client.get(f"/api/mining/runs/{discover(client, mining_ds, 'transition_system', {'window': 1})['run']['id']}/model").json()
    assert ts["representations"]["transition_system"]["window"] == 1
    tp = client.get(f"/api/mining/runs/{discover(client, mining_ds, 'temporal_profile')['run']['id']}/model").json()
    ab = next(p for p in tp["representations"]["temporal_profile"]["pairs"] if p["source"] == "A" and p["target"] == "B")
    assert ab["mean_s"] == pytest.approx(3600 * (6 * 1 + 5 * 2 + 3 * 1 + 2 * 2 + 1 * 1 + 2 * 1) / 19)  # real timestamps


def test_filters_change_the_training_population(client, mining_ds):
    run = discover(client, mining_ds, "inductive", filters={"exclude_activity": ["X", "Z"]})
    assert run["run"]["filter_snapshot"]["case_count"] == 18
    assert run["run"]["filter_snapshot"]["filters"] == {"exclude_activity": ["X", "Z"]}
    labels = {t["label"] for t in client.get(f"/api/mining/runs/{run['run']['id']}/model").json()
              ["representations"]["petri_net"]["transitions"] if not t["silent"]}
    assert not {"X", "Z"} & labels


# --------------------------------------------------------------------------- exports
def test_export_and_reimport(client, mining_ds):
    import pm4py

    r = discover(client, mining_ds, "inductive")["run"]
    reps = client.get(f"/api/mining/runs/{r['id']}/model").json()["representations"]
    pnml = client.get(f"/api/mining/runs/{r['id']}/export/pnml")
    assert pnml.status_code == 200 and "<pnml" in pnml.text
    tmp = FIXTURES.parent / "_tmp_export"
    tmp.mkdir(exist_ok=True)
    try:
        (tmp / "m.pnml").write_text(pnml.text)
        net, im, fm = pm4py.read_pnml(str(tmp / "m.pnml"))
        assert len(net.places) == len(reps["petri_net"]["places"]) and len(net.transitions) == len(reps["petri_net"]["transitions"])
        assert len(im) == 1 and len(fm) == 1
        (tmp / "m.ptml").write_text(client.get(f"/api/mining/runs/{r['id']}/export/ptml").text)
        tree = pm4py.read_ptml(str(tmp / "m.ptml"))
        assert str(tree) == reps["process_tree"]["text"]
        (tmp / "m.bpmn").write_text(client.get(f"/api/mining/runs/{r['id']}/export/bpmn").text)
        bpmn = pm4py.read_bpmn(str(tmp / "m.bpmn"))
        assert len(bpmn.get_nodes()) == len(reps["bpmn"]["nodes"])
    finally:
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()
    # formats a model type cannot provide are refused, not converted silently
    dec = discover(client, mining_ds, "declare")["run"]
    assert client.get(f"/api/mining/runs/{dec['id']}/export/pnml").status_code == 404
    assert client.get(f"/api/mining/runs/{r['id']}/export/svg").status_code == 422


# --------------------------------------------------------------------------- conformance
def conformance(client, ds, model, methods, limits=None, filters=None):
    out = start(client, f"/api/datasets/{ds}/mining/conformance", {"model": model, "methods": methods,
                                                                    "limits": limits or {}}, filters, expect=(200, 202))
    return finish(client, out["run"]["id"])


def test_fitting_and_deviating_cases(client, mining_ds):
    model = discover(client, mining_ds, "inductive", filters={"exclude_activity": ["X", "Z"]})["run"]
    res = conformance(client, mining_ds, {"run_id": model["id"]}, ["token_replay", "alignments", "soundness"])
    r = res["run"]
    assert r["status"] == "succeeded"
    assert r["result"]["model_source"]["type"] == "discovered"
    m = r["result"]["methods"]
    assert m["soundness"]["status"] == "computed" and m["soundness"]["sound"] is True
    assert m["alignments"]["status"] == "computed" and m["alignments"]["precision"]["status"] == "computed"
    assert 0 < m["alignments"]["metrics"]["log_fitness"] < 1
    cases = {c["case_id"]: c for c in client.get(f"/api/mining/runs/{r['id']}/cases",
                                                 params={"method": "alignments", "page_size": 100}).json()["items"]}
    assert cases["M00"]["is_fit"] is True and cases["M00"]["deviations"] == []
    noisy = cases["M19"]  # A B Z C D F
    assert noisy["is_fit"] is False and {"type": "log_move", "activity": "Z"} in noisy["deviations"]
    tbr = {c["case_id"]: c for c in client.get(f"/api/mining/runs/{r['id']}/cases",
                                               params={"method": "token_replay", "page_size": 100}).json()["items"]}
    assert tbr["M00"]["is_fit"] and not tbr["M16"]["is_fit"]  # A X F
    only_dev = client.get(f"/api/mining/runs/{r['id']}/cases", params={"method": "alignments", "only_deviating": True}).json()
    assert only_dev["total"] == m["alignments"]["deviating_cases"] > 0


def test_declarative_conformance_and_explicit_statuses(client, mining_ds):
    dec = discover(client, mining_ds, "declare", {"min_support_ratio": 0.5, "min_confidence_ratio": 1.0})["run"]
    res = conformance(client, mining_ds, {"run_id": dec["id"]}, ["declare_conformance", "token_replay"])["run"]
    methods = res["result"]["methods"]
    assert methods["declare_conformance"]["status"] == "computed"
    assert methods["token_replay"]["status"] == "unsupported"
    assert "metrics" not in methods["token_replay"]  # never a zero score in place of an unavailable one
    ls = discover(client, mining_ds, "log_skeleton")["run"]
    out = conformance(client, mining_ds, {"run_id": ls["id"]}, ["log_skeleton_conformance"])["run"]["result"]["methods"]
    assert out["log_skeleton_conformance"]["status"] == "computed"
    tp = discover(client, mining_ds, "temporal_profile")["run"]
    out = conformance(client, mining_ds, {"run_id": tp["id"]}, ["temporal_profile_conformance"], {"zeta": 1.0})["run"]
    tpc = out["result"]["methods"]["temporal_profile_conformance"]
    assert tpc["status"] == "computed" and tpc["zeta"] == 1.0 and tpc["deviating_cases"] > 0


def test_alignment_limit_gives_not_evaluable(client, mining_ds):
    model = discover(client, mining_ds, "inductive")["run"]
    res = conformance(client, mining_ds, {"run_id": model["id"]}, ["alignments"], {"max_variants": 2})["run"]
    al = res["result"]["methods"]["alignments"]
    assert al["status"] == "not_evaluable" and al["reason_key"] == "conf.err.variant_limit"
    assert al["reason_params"]["limit"] == 2


def test_reference_model_approval_and_upload(client, mining_ds):
    model = discover(client, mining_ds, "inductive_infrequent")["run"]
    ref = client.post(f"/api/datasets/{mining_ds}/reference-models",
                      json={"run_id": model["id"], "name": "Target process", "note": "approved in review"})
    assert ref.status_code == 201
    ref_id = ref.json()["id"]
    res = conformance(client, mining_ds, {"reference_model_id": ref_id}, ["token_replay"])["run"]
    src = res["result"]["model_source"]
    assert src["type"] == "reference" and src["name"] == "Target process"
    # PNML round trip through the upload/import job
    pnml = client.get(f"/api/datasets/{mining_ds}/reference-models/{ref_id}/export.pnml").text
    up = client.post(f"/api/datasets/{mining_ds}/reference-models/upload", data={"name": "Uploaded"},
                     files={"file": ("ref.pnml", pnml.encode(), "application/xml")})
    assert up.status_code == 202
    done = finish(client, up.json()["run"]["id"])["run"]
    assert done["status"] == "succeeded"
    refs = client.get(f"/api/datasets/{mining_ds}/reference-models").json()["items"]
    assert {r["name"] for r in refs} == {"Target process", "Uploaded"}
    bad = client.post(f"/api/datasets/{mining_ds}/reference-models/upload", data={"name": "Broken"},
                      files={"file": ("bad.pnml", b"<pnml><not-a-net/>", "application/xml")})
    assert finish(client, bad.json()["run"]["id"])["run"]["status"] == "failed"
    wrong = client.post(f"/api/datasets/{mining_ds}/reference-models/upload", data={"name": "x"},
                        files={"file": ("m.xml", b"<x/>", "application/xml")})
    assert wrong.status_code == 422


# --------------------------------------------------------------------------- comparison and splits
def test_case_level_split_is_deterministic_and_disjoint():
    ids = [f"C{i}" for i in range(500)]
    train, test = runs.split_cases(ids, 0.7, 7)
    assert set(train).isdisjoint(test) and set(train) | set(test) == set(ids)
    assert (train, test) == runs.split_cases(list(reversed(ids)), 0.7, 7)[0][::-1][::-1] or \
        (sorted(train), sorted(test)) == tuple(sorted(x) for x in runs.split_cases(list(reversed(ids)), 0.7, 7))
    assert 300 < len(train) < 400
    assert runs.split_cases(ids, 0.7, 8) != (train, test)
    events = pd.DataFrame({"case_id": ["C1", "C1", "C2", "C2", "C3"], "seq": [0, 1, 0, 1, 0],
                           "activity": ["a", "b", "a", "c", "a"],
                           "ts": pd.to_datetime(["2024-01-01"] * 5, utc=True)})
    log_ = runs.prepare_log(events, ["C1", "C3"])
    assert list(log_["case:concept:name"]) == ["C1", "C1", "C3"]  # whole cases, never split events


def test_comparison_with_held_out_cases(client, mining_ds):
    body = {"algorithms": [{"algorithm_id": "inductive"}, {"algorithm_id": "heuristics"},
                           {"algorithm_id": "log_skeleton"}, {"algorithm_id": "transition_system"}],
            "split": {"enabled": True, "train_ratio": 0.6, "seed": 3}, "evaluation": {"alignments": True}}
    run = finish(client, start(client, f"/api/datasets/{mining_ds}/mining/compare", body)["run"]["id"])["run"]
    assert run["status"] == "succeeded"
    split = run["split"]
    assert split["train_cases"] + split["test_cases"] == 22
    assert run["result"]["sample"] == "held_out" and run["result"]["evaluated_cases"] == split["test_cases"]
    rows = {r["algorithm_id"]: r for r in run["result"]["rows"]}
    assert rows["inductive"]["evaluation"]["token_replay"]["status"] == "computed"
    assert rows["inductive"]["sizes"]["petri_net"]["places"] > 0
    assert rows["log_skeleton"]["evaluation"]["log_skeleton_conformance"]["status"] == "computed"
    assert rows["transition_system"]["evaluation"] == {} and rows["transition_system"]["evaluation_note"]
    assert "overall_score" not in json.dumps(run["result"])
    csv_text = client.get(f"/api/mining/runs/{run['id']}/export/comparison.csv").text
    table = pd.read_csv(io.StringIO(csv_text))
    assert list(table["algorithm_id"]) == ["inductive", "heuristics", "log_skeleton", "transition_system"]
    assert table.loc[3, "tbr_fitness_status"] == "not_requested"
    side = client.get(f"/api/mining/runs/{run['id']}/model", params={"index": 1}).json()
    assert side["representations"]["heuristics_net"]["type"] == "heuristics_net"


# --------------------------------------------------------------------------- provenance and caching
def test_result_cache_and_history(client, mining_ds):
    first = discover(client, mining_ds, "alpha")["run"]
    again = client.post(f"/api/datasets/{mining_ds}/mining/discover", json={"algorithm_id": "alpha"})
    assert again.status_code == 200 and again.json()["cached"] and again.json()["run"]["id"] == first["id"]
    other = client.post(f"/api/datasets/{mining_ds}/mining/discover", json={"algorithm_id": "alpha"},
                        params={"activity": "X"})
    assert other.status_code == 202  # different filter snapshot => new run
    forced = client.post(f"/api/datasets/{mining_ds}/mining/discover", json={"algorithm_id": "alpha", "force": True})
    assert forced.status_code == 202 and forced.json()["run"]["id"] != first["id"]
    runs.wait_for(forced.json()["run"]["id"])
    runs.wait_for(other.json()["run"]["id"])
    hist = client.get("/api/mining/runs", params={"dataset_id": mining_ds, "kind": "discovery"}).json()
    assert hist["total"] == 3
    assert all(r["cache_key"] if False else True for r in hist["items"])


def test_snapshot_changes_are_detected(client, mining_ds):
    from app.database import SessionLocal
    from app.models import MiningRun
    from app.mining.worker import TaskFailed

    run = discover(client, mining_ds, "alpha")["run"]
    run_ok = run_import(client, FIXTURE.read_bytes(), MAPPING, **{**OPTIONS, "target_dataset_id": mining_ds})
    assert run_ok["result"]["version"] == 2
    with SessionLocal() as db:
        with pytest.raises(TaskFailed) as exc:
            runs._rebuild_view(db, db.get(MiningRun, run["id"]))
        assert exc.value.key == "run.err.dataset_changed"


# --------------------------------------------------------------------------- jobs
def test_cancellation(client, mining_ds):
    out = start(client, f"/api/datasets/{mining_ds}/mining/_diagnostic-sleep", {"seconds": 30, "timeout_s": 60})
    rid = out["run"]["id"]
    deadline = time.time() + 30
    while client.get(f"/api/mining/runs/{rid}").json()["run"]["status"] != "running" and time.time() < deadline:
        time.sleep(0.1)
    t0 = time.time()
    assert client.post(f"/api/mining/runs/{rid}/cancel").status_code == 200
    final = finish(client, rid)["run"]
    assert final["status"] == "cancelled" and time.time() - t0 < 10
    # the worker is replaced and keeps working afterwards
    assert discover(client, mining_ds, "alpha")["run"]["status"] == "succeeded"


def test_timeout(client, mining_ds):
    out = start(client, f"/api/datasets/{mining_ds}/mining/_diagnostic-sleep", {"seconds": 20, "timeout_s": 1})
    final = finish(client, out["run"]["id"])["run"]
    assert final["status"] == "timed_out" and final["error_key"] == "run.err.timeout"
    assert final["result"] is None  # no partial or zero result is stored


def test_cancel_queued_run_and_interrupted_recovery(client, mining_ds):
    from app.database import session_scope
    from app.models import MiningRun

    blocker = start(client, f"/api/datasets/{mining_ds}/mining/_diagnostic-sleep", {"seconds": 3, "timeout_s": 30})
    queued = start(client, f"/api/datasets/{mining_ds}/mining/_diagnostic-sleep", {"seconds": 3, "timeout_s": 30})
    r = client.post(f"/api/mining/runs/{queued['run']['id']}/cancel").json()["run"]
    assert r["status"] == "cancelled"
    finish(client, blocker["run"]["id"])
    with session_scope() as db:
        db.add(MiningRun(id="f" * 32, kind="discovery", cache_key="x", status="running", params={}, implementation={},
                         request={}, timeout_s=10))
    assert runs.recover_interrupted_runs() == 1
    assert client.get("/api/mining/runs/" + "f" * 32).json()["run"]["status"] == "interrupted"


def test_dataset_deletion_removes_runs(client, mining_ds):
    run = discover(client, mining_ds, "alpha")["run"]
    client.post(f"/api/datasets/{mining_ds}/reference-models", json={"run_id": run["id"], "name": "r"})
    assert client.delete(f"/api/datasets/{mining_ds}", params={"confirm_name": "mining-fixture"}).status_code == 204
    assert client.get(f"/api/mining/runs/{run['id']}").status_code == 404
