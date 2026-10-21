"""Mining-run orchestration: snapshots, caching, queueing, execution and provenance.

A run captures everything needed to reproduce it: dataset id + version +
settings revision, the normalized case filter together with a hash of the
selected case IDs, an optional case-level train/test split, the algorithm and
validated parameters, and the implementation version. Its SHA-256 over these
inputs is the cache key: an identical request returns the earlier successful
run instead of recomputing.

Runs execute one at a time on a supervisor thread that hands the computation
to the isolated worker process (app/mining/worker.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, session_scope
from app.mining import registry
from app.mining.tasks import ACT, CASE, REAL_TS, TS
from app.mining.worker import TaskCancelled, TaskFailed, TaskTimedOut, worker
from app.models import Dataset, MiningArtifact, MiningRun, ReferenceModel
from app.models.dataset import utcnow
from app.schemas.filters import FilterSpec
from app.services.eventlog import get_event_log
from app.services.filtering import FilteredView, apply_filters

log = logging.getLogger("processlens.mining")

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
SYNTHETIC_BASE = pd.Timestamp("2000-01-01", tz="UTC")


class RunRequestError(ValueError):
    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


# --------------------------------------------------------------------------- inputs
def case_ids_hash(case_ids) -> str:
    h = hashlib.sha256()
    for cid in sorted(map(str, case_ids)):
        h.update(cid.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def snapshot(view: FilteredView) -> dict:
    return {"filters": view.spec.normalized(), "case_count": int(view.case_count),
            "case_ids_sha256": case_ids_hash(view.cases.index)}


def split_cases(case_ids, train_ratio: float, seed: int) -> tuple[list[str], list[str]]:
    """Deterministic case-level split: a case goes entirely to train or to test."""
    train, test = [], []
    for cid in case_ids:
        digest = hashlib.sha256(f"{seed}\x1f{cid}".encode()).digest()
        (train if int.from_bytes(digest[:8], "big") / 2**64 < train_ratio else test).append(str(cid))
    return train, test


def prepare_log(events: pd.DataFrame, case_ids=None) -> pd.DataFrame:
    """PM4Py-style log preserving ProcessLens event order (see app/mining/tasks.py)."""
    ev = events if case_ids is None else events[events["case_id"].isin(set(case_ids))]
    ev = ev.sort_values(["case_id", "seq"], kind="mergesort")
    n = len(ev)
    return pd.DataFrame({
        CASE: ev["case_id"].astype(str).to_numpy(),
        ACT: ev["activity"].astype(str).to_numpy(),
        TS: SYNTHETIC_BASE + pd.to_timedelta(range(n), unit="s"),
        REAL_TS: ev["ts"].to_numpy(),
    })


def cache_key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _impl(algorithm_id: str | None) -> dict:
    if algorithm_id:
        return registry.implementation(registry.get(algorithm_id))
    return {"provider": "pm4py", "provider_version": registry.provider_version("pm4py"),
            "adapter_version": registry.ADAPTER_VERSION}


def _view(db: Session, ds: Dataset, spec: FilterSpec) -> FilteredView:
    from app.api.deps import rule_getter
    from app.services.conformance import lookup_factory

    log_ = get_event_log(db, ds)
    return apply_filters(log_, spec, lookup_factory(log_, rule_getter(db, ds.id)))


def _timeout(requested: float | None) -> float:
    s = get_settings()
    if requested is None:
        return float(s.mining_timeout_s)
    return float(min(max(requested, 1.0), s.mining_max_timeout_s))


def _base_inputs(ds: Dataset, view: FilteredView) -> dict:
    return {"dataset_id": ds.id, "dataset_version": ds.version, "settings_revision": ds.settings_revision,
            "snapshot": snapshot(view)}


def _create(db: Session, *, kind: str, ds: Dataset | None, view: FilteredView | None, key_inputs: dict,
            algorithm_id: str | None = None, params: dict | None = None, split: dict | None = None,
            request: dict | None = None, timeout_s: float, force: bool = False, parent_run_id: str | None = None,
            reference_model_id: int | None = None, ocel_log_id: int | None = None,
            submit: bool = True) -> tuple[MiningRun, bool]:
    impl = _impl(algorithm_id)
    key = cache_key({"kind": kind, **key_inputs, "implementation": impl})
    if not force:
        cached = db.scalars(select(MiningRun).where(MiningRun.cache_key == key, MiningRun.status == "succeeded")
                            .order_by(MiningRun.created_at.desc())).first()
        if cached is not None:
            return cached, True
    run = MiningRun(
        id=uuid.uuid4().hex, kind=kind, dataset_id=ds.id if ds else None, ocel_log_id=ocel_log_id,
        dataset_version=ds.version if ds else None, settings_revision=ds.settings_revision if ds else None,
        filter_snapshot=snapshot(view) if view is not None else None, split=split, algorithm_id=algorithm_id,
        params=params or {}, implementation=impl, request=request or {}, cache_key=key, status="queued",
        timeout_s=timeout_s, parent_run_id=parent_run_id, reference_model_id=reference_model_id,
    )
    db.add(run)
    db.commit()
    if submit:
        supervisor.submit(run.id)
    return run, False


# --------------------------------------------------------------------------- create runs
def create_discovery(db: Session, ds: Dataset, spec: FilterSpec, algorithm_id: str, raw_params: dict,
                     timeout_s: float | None = None, force: bool = False) -> tuple[MiningRun, bool]:
    try:
        aspec = registry.get(algorithm_id)
    except KeyError:
        raise RunRequestError("run.err.unknown_algorithm", algorithm=algorithm_id) from None
    av = registry.availability(aspec)
    if av["status"] != "available":
        raise RunRequestError("run.err.algorithm_unavailable", algorithm=algorithm_id, reason_key=av["reason_key"])
    view = _view(db, ds, spec)
    if view.case_count == 0:
        raise RunRequestError("run.err.empty_population")
    activities = set(view.events["activity"].unique())
    params = registry.validate_params(aspec, raw_params, activities)
    return _create(db, kind="discovery", ds=ds, view=view, algorithm_id=algorithm_id, params=params,
                   key_inputs={**_base_inputs(ds, view), "algorithm_id": algorithm_id, "params": params},
                   timeout_s=_timeout(timeout_s), force=force)


METHODS = {registry.TOKEN_REPLAY, registry.ALIGNMENTS, registry.SOUNDNESS, registry.DECLARE_CONF,
           registry.LOG_SKELETON_CONF, registry.TEMPORAL_CONF}


def _limits(raw: dict | None) -> dict:
    raw = raw or {}
    s = get_settings()
    out = {"precision": bool(raw.get("precision", True)),
           "max_variants": int(min(max(int(raw.get("max_variants", s.mining_max_alignment_variants)), 1),
                                   s.mining_max_alignment_variants)),
           "zeta": float(raw.get("zeta", 2.0))}
    if out["zeta"] <= 0:
        raise RunRequestError("run.err.invalid_zeta")
    return out


def create_conformance(db: Session, ds: Dataset, spec: FilterSpec, model: dict, methods: list[str], limits: dict | None,
                       timeout_s: float | None = None, force: bool = False) -> tuple[MiningRun, bool]:
    methods = sorted(set(methods))
    if not methods or set(methods) - METHODS:
        raise RunRequestError("run.err.invalid_methods", methods=",".join(sorted(set(methods) - METHODS)))
    parent_id = model.get("run_id")
    ref_id = model.get("reference_model_id")
    if bool(parent_id) == bool(ref_id):
        raise RunRequestError("run.err.model_source")
    if parent_id:
        parent = db.get(MiningRun, parent_id)
        if parent is None or parent.dataset_id != ds.id or parent.kind not in ("discovery",) or parent.status != "succeeded":
            raise RunRequestError("run.err.model_run_invalid")
        source = {"type": "discovered", "run_id": parent.id, "algorithm_id": parent.algorithm_id,
                  "native_type": (parent.result or {}).get("native_type")}
    else:
        ref = db.get(ReferenceModel, ref_id)
        if ref is None or ref.dataset_id != ds.id:
            raise RunRequestError("run.err.reference_invalid")
        source = {"type": "reference", "reference_model_id": ref.id, "name": ref.name, "model_type": ref.model_type,
                  "approved_at": ref.approved_at.isoformat()}
    view = _view(db, ds, spec)
    if view.case_count == 0:
        raise RunRequestError("run.err.empty_population")
    lim = _limits(limits)
    request = {"methods": methods, "limits": lim, "model_source": source}
    return _create(db, kind="conformance", ds=ds, view=view, request=request,
                   key_inputs={**_base_inputs(ds, view), **request}, timeout_s=_timeout(timeout_s), force=force,
                   parent_run_id=parent_id, reference_model_id=ref_id)


def create_comparison(db: Session, ds: Dataset, spec: FilterSpec, algorithms: list[dict], split: dict | None,
                      evaluation: dict | None, timeout_s: float | None = None, force: bool = False) -> tuple[MiningRun, bool]:
    if not algorithms or len(algorithms) > 8:
        raise RunRequestError("run.err.comparison_size")
    view = _view(db, ds, spec)
    if view.case_count == 0:
        raise RunRequestError("run.err.empty_population")
    activities = set(view.events["activity"].unique())
    configs = []
    for item in algorithms:
        aid = item.get("algorithm_id")
        try:
            aspec = registry.get(aid)
        except KeyError:
            raise RunRequestError("run.err.unknown_algorithm", algorithm=aid) from None
        if registry.availability(aspec)["status"] != "available":
            raise RunRequestError("run.err.algorithm_unavailable", algorithm=aid,
                                  reason_key=registry.availability(aspec)["reason_key"])
        configs.append({"algorithm_id": aid, "params": registry.validate_params(aspec, item.get("params"), activities),
                        "implementation": registry.implementation(aspec)})
    split_cfg = None
    if split and split.get("enabled"):
        ratio = float(split.get("train_ratio", 0.7))
        if not 0 < ratio < 1:
            raise RunRequestError("run.err.split_ratio")
        seed = int(split.get("seed", 42))
        train, test = split_cases(view.cases.index, ratio, seed)
        if not train or not test:
            raise RunRequestError("run.err.split_empty")
        split_cfg = {"enabled": True, "train_ratio": ratio, "seed": seed, "train_cases": len(train),
                     "test_cases": len(test), "train_sha256": case_ids_hash(train), "test_sha256": case_ids_hash(test)}
    ev = evaluation or {}
    eval_cfg = {"token_replay": bool(ev.get("token_replay", True)), "alignments": bool(ev.get("alignments", False)),
                "soundness": bool(ev.get("soundness", True)), "declarative": bool(ev.get("declarative", True)),
                **_limits(ev)}
    request = {"algorithms": configs, "evaluation": eval_cfg}
    return _create(db, kind="comparison", ds=ds, view=view, split=split_cfg, request=request,
                   key_inputs={**_base_inputs(ds, view), **request, "split": split_cfg},
                   timeout_s=_timeout(timeout_s), force=force)


def create_model_import(db: Session, ds: Dataset, fmt: str, content: str, name: str, filename: str,
                        note: str | None) -> MiningRun:
    if fmt not in ("pnml", "ptml"):
        raise RunRequestError("ref.err.unsupported_format", format=fmt)
    request = {"format": fmt, "name": name, "filename": filename, "note": note,
               "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
    run, _ = _create(db, kind="model_import", ds=ds, view=None, request=request,
                     key_inputs={"dataset_id": ds.id, **request, "nonce": uuid.uuid4().hex},
                     timeout_s=_timeout(60), force=True, submit=False)
    _store_artifact(db, run.id, "upload." + fmt, "application/xml", content)
    db.commit()
    supervisor.submit(run.id)  # only after the uploaded content is stored
    return run


def create_test_sleep(db: Session, ds: Dataset, seconds: float, timeout_s: float) -> MiningRun:
    """Diagnostics run used by the test-suite to exercise cancellation and timeouts."""
    run, _ = _create(db, kind="diagnostic_sleep", ds=ds, view=None, request={"seconds": seconds},
                     key_inputs={"nonce": uuid.uuid4().hex}, timeout_s=timeout_s, force=True)
    return run


def create_ocpn(db: Session, ocel_log, object_types: list[str] | None, raw_params: dict,
                timeout_s: float | None = None, force: bool = False) -> tuple[MiningRun, bool]:
    """Object-centric Petri net discovery on an OCEL log (optionally restricted to object types)."""
    spec = registry.get("oc_petri_net")
    av = registry.availability(spec)
    if av["status"] != "available":
        raise RunRequestError("run.err.algorithm_unavailable", algorithm=spec.id, reason_key=av["reason_key"])
    if not ocel_log.counts.get("e2o"):
        raise RunRequestError("ocel.err.empty_selection")
    params = registry.validate_params(spec, raw_params)
    request = {"ocel_version": ocel_log.version, "object_types": object_types, "ocel_sha256": ocel_log.source_sha256}
    return _create(db, kind="ocpn", ds=None, view=None, algorithm_id=spec.id, params=params, request=request,
                   key_inputs={"ocel_log_id": ocel_log.id, **request, "algorithm_id": spec.id, "params": params},
                   timeout_s=_timeout(timeout_s), force=force, ocel_log_id=ocel_log.id)


def delete_runs(db: Session, run_ids: list[str]) -> None:
    """Delete runs and their artifacts (queued/running runs are cancelled first)."""
    from sqlalchemy import delete

    with _live_lock:
        _cancel.update(run_ids)
    if run_ids:
        db.execute(delete(MiningArtifact).where(MiningArtifact.run_id.in_(run_ids)))
        db.execute(delete(MiningRun).where(MiningRun.id.in_(run_ids)))
        db.commit()


# --------------------------------------------------------------------------- artifacts
def _store_artifact(db: Session, run_id: str, name: str, media_type: str, content: str) -> None:
    existing = db.scalars(select(MiningArtifact).where(MiningArtifact.run_id == run_id, MiningArtifact.name == name)).first()
    if existing:
        existing.content = content
        existing.media_type = media_type
    else:
        db.add(MiningArtifact(run_id=run_id, name=name, media_type=media_type, content=content))


def artifact(db: Session, run_id: str, name: str) -> MiningArtifact | None:
    return db.scalars(select(MiningArtifact).where(MiningArtifact.run_id == run_id, MiningArtifact.name == name)).first()


def artifact_json(db: Session, run_id: str, name: str):
    a = artifact(db, run_id, name)
    return json.loads(a.content) if a else None


MEDIA = {".pnml": "application/xml", ".ptml": "application/xml", ".bpmn": "application/xml",
         ".json": "application/json", ".csv": "text/csv"}


def _media(name: str) -> str:
    for ext, m in MEDIA.items():
        if name.endswith(ext):
            return m
    return "text/plain"


# --------------------------------------------------------------------------- execution
_live: dict[str, dict] = {}
_live_lock = threading.Lock()
_cancel: set[str] = set()


def live(run_id: str) -> dict | None:
    with _live_lock:
        return _live.get(run_id)


def _set_live(run_id: str, stage_key: str, fraction: float | None, params: dict) -> None:
    with _live_lock:
        _live[run_id] = {"stage_key": stage_key, "progress": fraction, "stage_params": params}


def request_cancel(db: Session, run: MiningRun) -> MiningRun:
    if run.status in TERMINAL:
        return run
    with _live_lock:
        _cancel.add(run.id)
    if run.status == "queued":
        run.status = "cancelled"
        run.stage_key = "run.stage.cancelled"
        run.finished_at = utcnow()
        db.commit()
    return run


def _is_cancelled(run_id: str) -> bool:
    with _live_lock:
        return run_id in _cancel


class Supervisor:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, run_id: str) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="processlens-mining-supervisor", daemon=True)
                self._thread.start()
        self._queue.put(run_id)

    def _loop(self) -> None:
        while True:
            run_id = self._queue.get()
            try:
                _execute(run_id)
            except Exception:
                log.exception("Mining run %s crashed in the supervisor", run_id)
                _finish(run_id, status="failed", error_key="run.err.internal")
            finally:
                with _live_lock:
                    _live.pop(run_id, None)
                    _cancel.discard(run_id)
                self._queue.task_done()

    def wait_idle(self, timeout: float = 120) -> None:
        """Block until all submitted runs have finished (used by tests and the CLI)."""
        end = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.05)


supervisor = Supervisor()


def wait_for(run_id: str, timeout: float = 180) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with SessionLocal() as db:
            run = db.get(MiningRun, run_id)
            if run is None or run.status in TERMINAL:
                return
        time.sleep(0.1)
    raise TimeoutError(run_id)


def jsonable(obj):
    """Recursively convert to JSON-safe data (string keys; unknown objects as text)."""
    if isinstance(obj, dict):
        return {str(getattr(k, "name", k)): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"), float("-inf")) else None
    return str(obj)


def _finish(run_id: str, **fields) -> None:
    fields = {k: (jsonable(v) if k in ("result", "error_params") else v) for k, v in fields.items()}
    with session_scope() as db:
        run = db.get(MiningRun, run_id)
        if run is None:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        run.finished_at = utcnow()
        if run.started_at:
            run.runtime_s = round((run.finished_at - run.started_at).total_seconds(), 3)


class _StaleSnapshot(Exception):
    pass


def _rebuild_view(db: Session, run: MiningRun) -> tuple[Dataset, FilteredView]:
    ds = db.get(Dataset, run.dataset_id)
    if ds is None:
        raise TaskFailed("run.err.dataset_deleted")
    if ds.version != run.dataset_version or ds.settings_revision != run.settings_revision:
        raise TaskFailed("run.err.dataset_changed")
    view = _view(db, ds, FilterSpec.model_validate(run.filter_snapshot["filters"]))
    if case_ids_hash(view.cases.index) != run.filter_snapshot["case_ids_sha256"]:
        raise TaskFailed("run.err.snapshot_changed")
    return ds, view


def _call(run: MiningRun, kind: str, payload: dict, deadline: float, label: str | None = None) -> dict:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TaskTimedOut()

    def progress(stage_key, fraction, params):
        _set_live(run.id, stage_key, fraction, {**params, **({"step": label} if label else {})})

    return worker.run(kind, payload, remaining, lambda: _is_cancelled(run.id), progress)


def _execute(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(MiningRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status = "running"
        run.started_at = utcnow()
        run.stage_key = "run.stage.preparing"
        db.commit()
        _set_live(run.id, "run.stage.preparing", None, {})
        deadline = time.monotonic() + run.timeout_s
        try:
            if run.kind == "discovery":
                result = _exec_discovery(db, run, deadline)
            elif run.kind == "conformance":
                result = _exec_conformance(db, run, deadline)
            elif run.kind == "comparison":
                result = _exec_comparison(db, run, deadline)
            elif run.kind == "model_import":
                result = _exec_model_import(db, run, deadline)
            elif run.kind == "diagnostic_sleep":
                result = _call(run, "sleep", dict(run.request), deadline)
            elif run.kind == "ocpn":
                from app.services.ocel import exec_ocpn

                result = exec_ocpn(db, run, lambda kind, payload: _call(run, kind, payload, deadline))
            else:
                raise TaskFailed("run.err.unknown_kind")
            db.commit()
            _finish(run.id, status="succeeded", stage_key="run.stage.completed", progress=1.0, result=result)
        except TaskCancelled:
            db.rollback()
            _finish(run.id, status="cancelled", stage_key="run.stage.cancelled")
        except TaskTimedOut:
            db.rollback()
            _finish(run.id, status="timed_out", stage_key="run.stage.timed_out", error_key="run.err.timeout",
                    error_params={"seconds": run.timeout_s})
        except TaskFailed as exc:
            db.rollback()
            _finish(run.id, status="failed", stage_key="run.stage.failed", error_key=exc.key, error_params=exc.params)


def _exec_discovery(db: Session, run: MiningRun, deadline: float) -> dict:
    ds, view = _rebuild_view(db, run)
    log_df = prepare_log(view.events)
    out = _call(run, "discover", {"algorithm_id": run.algorithm_id, "params": run.params, "log": log_df}, deadline)
    for name, content in out.pop("artifacts").items():
        _store_artifact(db, run.id, name, _media(name), content)
    _store_artifact(db, run.id, "representations.json", "application/json",
                    json.dumps(out.pop("representations"), ensure_ascii=False))
    return out


def _model_inputs(db: Session, run: MiningRun) -> dict:
    """Model to evaluate: from a discovery run or from an approved reference model."""
    src = run.request["model_source"]
    if src["type"] == "discovered":
        parent = db.get(MiningRun, src["run_id"])
        if parent is None or parent.status != "succeeded":
            raise TaskFailed("run.err.model_run_invalid")
        reps = artifact_json(db, parent.id, "representations.json") or {}
        native = (parent.result or {}).get("native_type")
        pn = artifact(db, parent.id, "model.pnml")
        return {"model_type": native, "model": reps.get(native), "pnml": pn.content if pn else None}
    ref = db.get(ReferenceModel, src["reference_model_id"])
    if ref is None:
        raise TaskFailed("run.err.reference_invalid")
    return {"model_type": ref.model_type, "model": ref.representation, "pnml": ref.pnml}


def _exec_conformance(db: Session, run: MiningRun, deadline: float) -> dict:
    ds, view = _rebuild_view(db, run)
    model = _model_inputs(db, run)
    log_df = prepare_log(view.events)
    out = _call(run, "evaluate", {**model, "log": log_df, "methods": run.request["methods"],
                                  "limits": run.request["limits"]}, deadline)
    summary = {}
    for method, res in out.items():
        cases = res.pop("cases", None)
        if cases is not None:
            _store_artifact(db, run.id, f"cases.{method}.json", "application/json", json.dumps(cases, ensure_ascii=False))
            res["case_count"] = len(cases)
            res["deviating_cases"] = sum(1 for c in cases if not c.get("is_fit", True))
        summary[method] = res
    return {"methods": summary, "model_source": run.request["model_source"],
            "evaluated_cases": int(view.case_count)}


def _exec_comparison(db: Session, run: MiningRun, deadline: float) -> dict:
    ds, view = _rebuild_view(db, run)
    split = run.split
    if split:
        train, test = split_cases(view.cases.index, split["train_ratio"], split["seed"])
        if case_ids_hash(train) != split["train_sha256"] or case_ids_hash(test) != split["test_sha256"]:
            raise TaskFailed("run.err.snapshot_changed")
        train_log, test_log = prepare_log(view.events, train), prepare_log(view.events, test)
        sample = "held_out"
    else:
        train_log = test_log = prepare_log(view.events)
        sample = "in_sample"
    ev = run.request["evaluation"]
    rows = []
    configs = run.request["algorithms"]
    for idx, cfg in enumerate(configs):
        label = f"{idx + 1}/{len(configs)} {cfg['algorithm_id']}"
        row = {"index": idx, "algorithm_id": cfg["algorithm_id"], "params": cfg["params"],
               "implementation": cfg["implementation"], "status": "succeeded", "sample": sample}
        started = time.monotonic()
        step_deadline = min(deadline, time.monotonic() + run.timeout_s / max(1, len(configs)) * 2)
        try:
            out = _call(run, "discover", {"algorithm_id": cfg["algorithm_id"], "params": cfg["params"], "log": train_log},
                        step_deadline, label)
            row["discovery_seconds"] = out["discovery_seconds"]
            row["wall_seconds"] = round(time.monotonic() - started, 3)
            row["native_type"] = out["native_type"]
            row["representations"] = sorted(out["representations"])
            row["sizes"] = out["sizes"]
            row["warnings"] = out["warnings"]
            _store_artifact(db, run.id, f"model{idx}.representations.json", "application/json",
                            json.dumps(out["representations"], ensure_ascii=False))
            for name, content in out["artifacts"].items():
                _store_artifact(db, run.id, f"model{idx}.{name.split('.', 1)[1]}", _media(name), content)
            methods = []
            has_pn = "model.pnml" in out["artifacts"]
            if has_pn and ev["token_replay"]:
                methods.append(registry.TOKEN_REPLAY)
            if has_pn and ev["alignments"]:
                methods.append(registry.ALIGNMENTS)
            if has_pn and ev["soundness"]:
                methods.append(registry.SOUNDNESS)
            if ev["declarative"]:
                methods += [m for m in registry.evaluations_for(out["native_type"], False)]
            native = out["native_type"]
            if methods:
                res = _call(run, "evaluate", {"model_type": native, "model": out["representations"].get(native),
                                              "pnml": out["artifacts"].get("model.pnml"), "log": test_log,
                                              "methods": methods, "limits": ev}, step_deadline, label)
                for m in res.values():
                    m.pop("cases", None)
                row["evaluation"] = res
            else:
                row["evaluation"] = {}
                row["evaluation_note"] = "run.note.no_evaluation_method"
        except TaskTimedOut:
            if time.monotonic() >= deadline:
                raise
            row["status"] = "timed_out"
        except TaskFailed as exc:
            row["status"] = "failed"
            row["error_key"], row["error_params"] = exc.key, exc.params
        rows.append(row)
        _set_live(run.id, "run.stage.compared", (idx + 1) / len(configs), {"done": idx + 1, "total": len(configs)})
    return {"rows": rows, "split": split, "sample": sample, "evaluated_cases": int(len(test_log[CASE].unique()) if len(test_log) else 0),
            "training_cases": int(len(train_log[CASE].unique()) if len(train_log) else 0)}


def _exec_model_import(db: Session, run: MiningRun, deadline: float) -> dict:
    req = run.request
    upload = artifact(db, run.id, "upload." + req["format"])
    out = _call(run, "parse_model", {"format": req["format"], "content": upload.content}, deadline)
    ref = ReferenceModel(dataset_id=run.dataset_id, name=req["name"], model_type=out["model_type"],
                         representation=out["representation"], pnml=out["pnml"],
                         source={"type": "upload", "format": req["format"], "filename": req["filename"], "run_id": run.id},
                         note=req.get("note"))
    db.add(ref)
    db.flush()
    return {"reference_model_id": ref.id, "model_type": out["model_type"]}


def approve_reference(db: Session, ds: Dataset, run_id: str, name: str, note: str | None) -> ReferenceModel:
    run = db.get(MiningRun, run_id)
    if run is None or run.dataset_id != ds.id or run.kind != "discovery" or run.status != "succeeded":
        raise RunRequestError("run.err.model_run_invalid")
    native = run.result["native_type"]
    reps = artifact_json(db, run.id, "representations.json") or {}
    pn = artifact(db, run.id, "model.pnml")
    ref = ReferenceModel(dataset_id=ds.id, name=name, model_type=native, representation=reps.get(native),
                         pnml=pn.content if pn else None,
                         source={"type": "run", "run_id": run.id, "algorithm_id": run.algorithm_id,
                                 "params": run.params, "implementation": run.implementation,
                                 "dataset_version": run.dataset_version, "filter_snapshot": run.filter_snapshot},
                         note=note)
    db.add(ref)
    db.commit()
    return ref


def recover_interrupted_runs() -> int:
    with session_scope() as db:
        runs = db.scalars(select(MiningRun).where(MiningRun.status.in_(["queued", "running"]))).all()
        for r in runs:
            r.status = "interrupted"
            r.stage_key = "run.stage.interrupted"
            r.error_key = "run.err.interrupted"
            r.finished_at = utcnow()
        return len(runs)


def run_out(run: MiningRun) -> dict:
    data = {c: getattr(run, c) for c in (
        "id", "kind", "dataset_id", "ocel_log_id", "dataset_version", "settings_revision", "filter_snapshot", "split",
        "algorithm_id", "params", "implementation", "request", "status", "stage_key", "stage_params", "progress",
        "error_key", "error_params", "result", "timeout_s", "runtime_s", "parent_run_id", "reference_model_id")}
    for c in ("created_at", "started_at", "finished_at"):
        v: datetime | None = getattr(run, c)
        data[c] = v.isoformat() if v else None
    if run.status in ("queued", "running"):
        lv = live(run.id)
        if lv:
            data.update(lv)
    return data
