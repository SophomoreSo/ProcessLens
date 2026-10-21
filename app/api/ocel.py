"""Object-centric event logs (OCEL 2.0 JSON): import, statistics, OC-DFG, objects, projection, OCPN runs."""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import request_lang
from app.api.imports import _job_out
from app.config import PROJECT_ROOT, get_settings
from app.database import get_db
from app.errors import AppError, not_found
from app.i18n import t
from app.mining import registry, runs
from app.models import MiningRun, OcelLog
from app.schemas.imports import ColumnMapping, ConfirmImportRequest
from app.services import importer, ocel as svc

router = APIRouter(prefix="/api/ocel", tags=["object-centric"])
DEMO_FILE = PROJECT_ROOT / "sample_data" / "ocel_demo.json"


def _log(db: Session, log_id: int) -> OcelLog:
    o = db.get(OcelLog, log_id)
    if o is None:
        raise not_found("ocel_log")
    return o


def _types(object_type: list[str], o: OcelLog) -> list[str] | None:
    known = {t_["name"] for t_ in o.object_types}
    unknown = sorted(set(object_type) - known)
    if unknown:
        raise AppError(422, "unknown_object_type", key="ocel.err.unknown_object_type", types=", ".join(unknown))
    return sorted(set(object_type)) or None


def _out(o: OcelLog) -> dict:
    return {"id": o.id, "name": o.name, "description": o.description, "is_demo": o.is_demo, "version": o.version,
            "source_filename": o.source_filename, "source_sha256": o.source_sha256,
            "source_size_bytes": o.source_size_bytes, "event_types": o.event_types, "object_types": o.object_types,
            "counts": o.counts, "findings": o.findings, "imported_at": o.imported_at.isoformat() + "Z"}


def _import(db: Session, raw: bytes, filename: str, name: str | None, description: str | None = None,
            is_demo: bool = False) -> OcelLog:
    try:
        parsed = svc.parse(raw)
    except svc.OcelInvalid as exc:
        raise AppError(422, "invalid_ocel", key="ocel.err.invalid", details=exc.findings,
                       errors=sum(1 for f in exc.findings if f["severity"] == "error")) from None
    return svc.store(db, parsed, (name or filename.rsplit(".", 1)[0])[:200], filename, raw, description, is_demo)


@router.get("", summary="List imported object-centric event logs")
def list_logs(db: Session = Depends(get_db)):
    return {"items": [_out(o) for o in db.scalars(select(OcelLog).order_by(OcelLog.imported_at.desc())).all()]}


@router.post("/upload", status_code=201, summary="Import an OCEL 2.0 JSON file (validated; errors are returned as findings)")
def upload(file: UploadFile = File(...), name: str | None = Form(None), db: Session = Depends(get_db)):
    limit = get_settings().max_upload_bytes
    raw = file.file.read(limit + 1)
    if len(raw) > limit:
        raise AppError(413, "file_too_large", max_mb=get_settings().max_upload_mb)
    filename = (file.filename or "upload.json").replace("/", "_").replace("\\", "_")[:255]
    if not filename.lower().endswith((".json", ".jsonocel")):
        raise AppError(422, "unsupported_format", key="ocel.err.not_json_file")
    return _out(_import(db, raw, filename, name))


@router.post("/demo", status_code=201, summary="Import the synthetic order-to-delivery OCEL demo log")
def demo(db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    return _out(_import(db, DEMO_FILE.read_bytes(), DEMO_FILE.name, t(lang, "ocel.demo_name"),
                        t(lang, "ocel.demo_description"), is_demo=True))


@router.get("/{log_id}", summary="One OCEL log with validation findings")
def get_log(log_id: int, db: Session = Depends(get_db)):
    return _out(_log(db, log_id))


@router.delete("/{log_id}", status_code=204, summary="Delete an OCEL log, its objects, relationships and runs")
def delete_log(log_id: int, db: Session = Depends(get_db)):
    o = _log(db, log_id)
    run_ids = db.scalars(select(MiningRun.id).where(MiningRun.ocel_log_id == o.id)).all()
    runs.delete_runs(db, run_ids)
    svc.delete_log(db, o)
    return Response(status_code=204)


@router.get("/{log_id}/stats", summary="Unique event/object/relationship counts (shared events counted once)")
def stats(log_id: int, object_type: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    o = _log(db, log_id)
    return {"log_id": o.id, "version": o.version, "object_types_filter": _types(object_type, o),
            **svc.stats(svc.load(db, o), _types(object_type, o))}


@router.get("/{log_id}/ocdfg", summary="Object-centric directly-follows graph")
def ocdfg(log_id: int, object_type: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    o = _log(db, log_id)
    return {"log_id": o.id, "version": o.version, "object_types_filter": _types(object_type, o),
            **svc.ocdfg(svc.load(db, o), _types(object_type, o))}


@router.get("/{log_id}/objects", summary="Objects (paged) with event counts")
def objects(log_id: int, object_type: str | None = None, search: str | None = Query(None, max_length=200),
            page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    o = _log(db, log_id)
    if object_type:
        _types([object_type], o)
    return svc.objects_page(svc.load(db, o), object_type, search, page, page_size)


@router.get("/{log_id}/objects/{object_id}", summary="Object timeline, attribute history and related objects")
def object_detail(log_id: int, object_id: str, db: Session = Depends(get_db)):
    o = _log(db, log_id)
    detail = svc.object_detail(svc.load(db, o), db, o, object_id)
    if detail is None:
        raise not_found("object")
    return detail


@router.get("/{log_id}/relationship-delays", summary="Object lifetimes by number of related objects (descriptive)")
def relationship_delays(log_id: int, object_type: str, db: Session = Depends(get_db)):
    o = _log(db, log_id)
    _types([object_type], o)
    return svc.relationship_delays(svc.load(db, o), object_type)


@router.get("/{log_id}/projection-preview", summary="What flattening by one object type would do")
def projection_preview(log_id: int, object_type: str, db: Session = Depends(get_db)):
    o = _log(db, log_id)
    _types([object_type], o)
    _, info = svc.projection_csv(svc.load(db, o), object_type)
    return info


class ProjectionRequest(BaseModel):
    object_type: str
    dataset_name: str | None = Field(None, max_length=200)
    acknowledge_flattening: bool = Field(False, description="Must be true: flattening duplicates shared events")


@router.post("/{log_id}/project", status_code=202, summary="Flatten to a case-centric dataset by one object type")
def project(log_id: int, body: ProjectionRequest, db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    o = _log(db, log_id)
    _types([body.object_type], o)
    if not body.acknowledge_flattening:
        raise AppError(422, "flattening_not_acknowledged", key="ocel.err.ack_flattening")
    try:
        content, info = svc.projection_csv(svc.load(db, o), body.object_type)
    except ValueError:
        raise AppError(422, "empty_projection", key="ocel.err.empty_selection") from None
    upload_, _ = importer.create_upload(db, io.BytesIO(content), f"{o.name}-{body.object_type}.csv")
    request = ConfirmImportRequest(
        mapping=ColumnMapping(case_id="case_id", activity="activity", timestamp="timestamp",
                              attributes=["ocel_event_id", "qualifier", "objects_sharing_event"]),
        invalid_row_policy="reject_import",
        dataset_name=body.dataset_name or t(lang, "ocel.projection_name", name=o.name, type=body.object_type),
        display_timezone=o.display_timezone,
    )
    description = t(lang, "ocel.projection_description", name=o.name, type=body.object_type,
                    duplicated=info["duplicated_event_rows"], unique=info["unique_events"])
    job = importer.start_import(db, upload_, request, kind="ocel_projection",
                                extra={"description": description, "ocel_log_id": o.id, "projection": info})
    return JSONResponse({"job": _job_out(job, lang).model_dump(mode="json"), "projection": info}, status_code=202)


class OcpnRequest(BaseModel):
    object_types: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    timeout_s: float | None = Field(None, gt=0)
    force: bool = False


@router.post("/{log_id}/ocpn", summary="Discover an object-centric Petri net (background run; 202, or 200 if cached)")
def ocpn(log_id: int, body: OcpnRequest, db: Session = Depends(get_db)):
    o = _log(db, log_id)
    types = _types(body.object_types, o)
    try:
        run, cached = runs.create_ocpn(db, o, types, body.params, body.timeout_s, body.force)
    except registry.ParamError as exc:
        raise AppError(422, "invalid_parameters", key="run.err.invalid_parameters", details=exc.errors,
                       detail="; ".join(f"{e['param']}: {e['problem']}" for e in exc.errors)) from None
    except runs.RunRequestError as exc:
        raise AppError(422, exc.key.split(".")[-1], key=exc.key, **exc.params) from None
    return JSONResponse({"run": runs.run_out(run), "cached": cached}, status_code=200 if cached else 202)
