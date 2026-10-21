"""Process-discovery, formal conformance and comparison runs (background, cancellable)."""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import filter_params, get_dataset
from app.config import get_settings
from app.database import get_db
from app.errors import AppError, not_found
from app.mining import registry, runs
from app.models import Dataset, MiningArtifact, MiningRun, ReferenceModel
from app.schemas.filters import FilterSpec
from app.services.exports import safe_cell

router = APIRouter(prefix="/api", tags=["mining"])


def _request_error(exc: runs.RunRequestError) -> AppError:
    return AppError(422, exc.key.split(".")[-1], key=exc.key, **exc.params)


def _param_error(exc: registry.ParamError) -> AppError:
    return AppError(422, "invalid_parameters", key="run.err.invalid_parameters", details=exc.errors,
                    detail="; ".join(f"{e['param']}: {e['problem']}" for e in exc.errors))


def _accepted(run: MiningRun, cached: bool) -> JSONResponse:
    return JSONResponse({"run": runs.run_out(run), "cached": cached}, status_code=200 if cached else 202)


def _run(db: Session, run_id: str) -> MiningRun:
    run = db.get(MiningRun, run_id) if run_id.isalnum() else None
    if run is None:
        raise not_found("run")
    return run


# --------------------------------------------------------------------------- registry
@router.get("/mining/algorithms", summary="Algorithm registry with availability, parameters and limitations")
def algorithms():
    return {"algorithms": [registry.describe(a) for a in registry.ALGORITHMS.values()],
            "provider_versions": {"pm4py": registry.provider_version("pm4py")},
            "visualization_exports": {"png": {"available": True, "where": "browser (Cytoscape.js)"},
                                      "svg": {"available": False, "reason_key": "algo.unavailable.svg"}}}


# --------------------------------------------------------------------------- runs
class DiscoveryRequest(BaseModel):
    algorithm_id: str
    params: dict = Field(default_factory=dict)
    timeout_s: float | None = Field(None, gt=0)
    force: bool = False


@router.post("/datasets/{dataset_id}/mining/discover", summary="Start a discovery run (202) or return a cached one (200)")
def discover(body: DiscoveryRequest, ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
             db: Session = Depends(get_db)):
    try:
        run, cached = runs.create_discovery(db, ds, spec, body.algorithm_id, body.params, body.timeout_s, body.force)
    except runs.RunRequestError as exc:
        raise _request_error(exc) from None
    except registry.ParamError as exc:
        raise _param_error(exc) from None
    return _accepted(run, cached)


class ConformanceRequest(BaseModel):
    model: dict = Field(description='{"run_id": "..."} for a discovered model or {"reference_model_id": 1}')
    methods: list[str]
    limits: dict = Field(default_factory=dict)
    timeout_s: float | None = Field(None, gt=0)
    force: bool = False


@router.post("/datasets/{dataset_id}/mining/conformance", summary="Start a formal conformance run")
def conformance(body: ConformanceRequest, ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
                db: Session = Depends(get_db)):
    try:
        run, cached = runs.create_conformance(db, ds, spec, body.model, body.methods, body.limits, body.timeout_s, body.force)
    except runs.RunRequestError as exc:
        raise _request_error(exc) from None
    return _accepted(run, cached)


class ComparisonRequest(BaseModel):
    algorithms: list[dict]
    split: dict | None = None
    evaluation: dict = Field(default_factory=dict)
    timeout_s: float | None = Field(None, gt=0)
    force: bool = False


@router.post("/datasets/{dataset_id}/mining/compare", summary="Run several algorithms on the same snapshot and compare")
def compare(body: ComparisonRequest, ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
            db: Session = Depends(get_db)):
    try:
        run, cached = runs.create_comparison(db, ds, spec, body.algorithms, body.split, body.evaluation,
                                             body.timeout_s, body.force)
    except runs.RunRequestError as exc:
        raise _request_error(exc) from None
    except registry.ParamError as exc:
        raise _param_error(exc) from None
    return _accepted(run, cached)


class SleepRequest(BaseModel):
    seconds: float = Field(gt=0, le=60)
    timeout_s: float = Field(gt=0, le=60)


@router.post("/datasets/{dataset_id}/mining/_diagnostic-sleep", include_in_schema=False)
def diagnostic_sleep(body: SleepRequest, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    if not get_settings().enable_test_tasks:
        raise not_found("run")
    return _accepted(runs.create_test_sleep(db, ds, body.seconds, body.timeout_s), False)


@router.get("/mining/runs", summary="Run history (model/run provenance)")
def list_runs(dataset_id: int | None = None, kind: list[str] = Query(default=[]), status: list[str] = Query(default=[]),
              page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), db: Session = Depends(get_db)):
    q = select(MiningRun)
    if dataset_id is not None:
        q = q.where(MiningRun.dataset_id == dataset_id)
    if kind:
        q = q.where(MiningRun.kind.in_(kind))
    if status:
        q = q.where(MiningRun.status.in_(status))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(MiningRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [runs.run_out(r) for r in items], "total": total, "page": page, "page_size": page_size}


@router.get("/mining/runs/{run_id}", summary="Run status, provenance and result summary")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = _run(db, run_id)
    db.refresh(run)
    names = db.scalars(select(MiningArtifact.name).where(MiningArtifact.run_id == run.id)).all()
    return {"run": runs.run_out(run), "artifacts": sorted(names)}


@router.post("/mining/runs/{run_id}/cancel", summary="Cancel a queued or running run")
def cancel(run_id: str, db: Session = Depends(get_db)):
    run = runs.request_cancel(db, _run(db, run_id))
    return {"run": runs.run_out(run)}


@router.get("/mining/runs/{run_id}/model", summary="JSON representations of a discovered model")
def run_model(run_id: str, index: int | None = Query(None, ge=0), db: Session = Depends(get_db)):
    run = _run(db, run_id)
    name = "representations.json" if index is None else f"model{index}.representations.json"
    reps = runs.artifact_json(db, run.id, name)
    if reps is None:
        raise not_found("model")
    return {"run_id": run.id, "representations": reps, "native_type": (run.result or {}).get("native_type")}


@router.get("/mining/runs/{run_id}/cases", summary="Case-level conformance diagnostics")
def run_cases(run_id: str, method: str, only_deviating: bool = False, page: int = Query(1, ge=1),
              page_size: int = Query(25, ge=1, le=500), db: Session = Depends(get_db)):
    run = _run(db, run_id)
    cases = runs.artifact_json(db, run.id, f"cases.{method}.json")
    if cases is None:
        raise not_found("diagnostics")
    if only_deviating:
        cases = [c for c in cases if not c.get("is_fit", True)]
    cases.sort(key=lambda c: (c.get("is_fit", True), c.get("fitness", 1.0) if c.get("fitness") is not None else 1.0))
    return {"items": cases[(page - 1) * page_size: page * page_size], "total": len(cases), "page": page,
            "page_size": page_size}


EXPORT_NAMES = {"pnml": "model.pnml", "ptml": "model.ptml", "bpmn": "model.bpmn", "json": "model.json"}


@router.get("/mining/runs/{run_id}/export/{fmt}", summary="Download a model (pnml, ptml, bpmn, json) or comparison (csv)")
def export_run(run_id: str, fmt: str, index: int | None = Query(None, ge=0), db: Session = Depends(get_db)):
    run = _run(db, run_id)
    if run.kind == "comparison" and fmt in ("comparison.csv", "comparison.json"):
        rows = (run.result or {}).get("rows", [])
        if fmt == "comparison.json":
            body = json.dumps({"processlens_export": "algorithm-comparison", "format_note": "application-specific JSON",
                               "run": runs.run_out(run)}, ensure_ascii=False, indent=2, default=str)
            return Response(body, media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="comparison_{run.id[:8]}.json"'})
        return Response(_comparison_csv(rows, run), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="comparison_{run.id[:8]}.csv"'})
    if fmt == "representation":
        name, media = ("representations.json" if index is None else f"model{index}.representations.json"), "application/json"
    elif fmt in EXPORT_NAMES:
        base = EXPORT_NAMES[fmt]
        name = base if index is None else f"model{index}.{base.split('.', 1)[1]}"
        media = "application/json" if fmt == "json" else "application/xml"
    else:
        raise AppError(422, "unsupported_format", key="run.err.export_format", format=fmt)
    art = runs.artifact(db, run.id, name)
    if art is None:
        raise AppError(404, "not_found", key="run.err.export_unavailable", format=fmt)
    ext = {"representation": "json"}.get(fmt, fmt)
    return Response(art.content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="processlens_{run.id[:8]}{"_" + str(index) if index is not None else ""}.{ext}"'})


def _metric(row: dict, method: str, key: str):
    m = (row.get("evaluation") or {}).get(method)
    if not m:
        return "", "not_requested"
    if m["status"] != "computed":
        return "", m["status"]
    if key == "precision":
        p = m.get("precision") or {}
        return (p.get("value"), "computed") if p.get("status") == "computed" else ("", p.get("status", "not_evaluable"))
    if key == "sound":
        return m.get("sound"), "computed"
    return (m.get("metrics") or {}).get(key), "computed"


def _comparison_csv(rows: list[dict], run: MiningRun) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    header = ["algorithm_id", "status", "native_type", "sample", "discovery_seconds", "sizes", "params",
              "tbr_log_fitness", "tbr_fitness_status", "tbr_precision", "tbr_precision_status",
              "alignment_log_fitness", "alignment_fitness_status", "alignment_precision", "alignment_precision_status",
              "sound", "soundness_status", "warnings", "provider_version", "dataset_version", "filters"]
    w.writerow(header)
    for r in rows:
        tf, tfs = _metric(r, "token_replay", "log_fitness")
        tp, tps = _metric(r, "token_replay", "precision")
        af, afs = _metric(r, "alignments", "log_fitness")
        ap, aps = _metric(r, "alignments", "precision")
        snd, snds = _metric(r, "soundness", "sound")
        w.writerow([safe_cell(x) for x in [
            r["algorithm_id"], r["status"], r.get("native_type", ""), r.get("sample", ""), r.get("discovery_seconds", ""),
            json.dumps(r.get("sizes", {})), json.dumps(r.get("params", {})), tf, tfs, tp, tps, af, afs, ap, aps, snd, snds,
            ";".join(w_["key"] for w_ in r.get("warnings", [])), (r.get("implementation") or {}).get("provider_version", ""),
            run.dataset_version, json.dumps((run.filter_snapshot or {}).get("filters", {}))]])
    return buf.getvalue()


# --------------------------------------------------------------------------- reference models
class ApproveRequest(BaseModel):
    run_id: str
    name: str = Field(min_length=1, max_length=200)
    note: str | None = Field(None, max_length=2000)


def _ref_out(ref: ReferenceModel, full: bool = False) -> dict:
    out = {"id": ref.id, "dataset_id": ref.dataset_id, "name": ref.name, "model_type": ref.model_type,
           "source": ref.source, "note": ref.note, "approved_at": ref.approved_at.isoformat(),
           "has_petri_net": bool(ref.pnml)}
    if full:
        out["representation"] = ref.representation
    return out


@router.get("/datasets/{dataset_id}/reference-models", summary="User-approved reference models")
def list_refs(ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    refs = db.scalars(select(ReferenceModel).where(ReferenceModel.dataset_id == ds.id).order_by(ReferenceModel.id)).all()
    return {"items": [_ref_out(r) for r in refs]}


@router.post("/datasets/{dataset_id}/reference-models", status_code=201,
             summary="Approve a discovered model as reference model")
def approve(body: ApproveRequest, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    try:
        ref = runs.approve_reference(db, ds, body.run_id, body.name, body.note)
    except runs.RunRequestError as exc:
        raise _request_error(exc) from None
    return _ref_out(ref, full=True)


@router.post("/datasets/{dataset_id}/reference-models/upload", status_code=202,
             summary="Import a reference model from PNML or PTML (validated in the worker)")
def upload_ref(file: UploadFile = File(...), name: str = Form(..., min_length=1, max_length=200),
               note: str | None = Form(None), ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    filename = (file.filename or "model").rsplit("/", 1)[-1][:200]
    fmt = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    raw = file.file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise AppError(413, "file_too_large", key="error.file_too_large", limit_mb=5)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AppError(422, "encoding_error", key="ref.err.not_utf8") from None
    try:
        run = runs.create_model_import(db, ds, fmt, content, name, filename, note)
    except runs.RunRequestError as exc:
        raise _request_error(exc) from None
    return {"run": runs.run_out(run)}


@router.get("/datasets/{dataset_id}/reference-models/{ref_id}", summary="Reference model with representation")
def get_ref(ref_id: int, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    ref = db.get(ReferenceModel, ref_id)
    if ref is None or ref.dataset_id != ds.id:
        raise not_found("reference_model")
    return _ref_out(ref, full=True)


@router.get("/datasets/{dataset_id}/reference-models/{ref_id}/export.pnml", summary="Reference model as PNML")
def export_ref(ref_id: int, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    ref = db.get(ReferenceModel, ref_id)
    if ref is None or ref.dataset_id != ds.id:
        raise not_found("reference_model")
    if not ref.pnml:
        raise AppError(404, "not_found", key="run.err.export_unavailable", format="pnml")
    return Response(ref.pnml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="reference_{ref.id}.pnml"'})


@router.delete("/datasets/{dataset_id}/reference-models/{ref_id}", status_code=204, summary="Delete a reference model")
def delete_ref(ref_id: int, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    ref = db.get(ReferenceModel, ref_id)
    if ref is None or ref.dataset_id != ds.id:
        raise not_found("reference_model")
    db.delete(ref)
    db.commit()
    return Response(status_code=204)
