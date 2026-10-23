"""Task mining: import user-interaction events, sessionize, and analyse task sequences."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import request_lang
from app.config import PROJECT_ROOT, get_settings
from app.database import get_db
from app.errors import AppError, not_found
from app.i18n import t
from app.models import Dataset, TaskLog
from app.services import tasks as svc

router = APIRouter(prefix="/api/tasks", tags=["task mining"])
DEMO_FILE = PROJECT_ROOT / "sample_data" / "task_events_demo.csv"
Granularity = Literal["action", "application"]


def _log(db: Session, log_id: int) -> TaskLog:
    tl = db.get(TaskLog, log_id)
    if tl is None:
        raise not_found("task_log")
    return tl


def sessionization(mode: Literal["source", "inactivity", "source_and_inactivity"] = Query(
                       "source_and_inactivity", description="How events are grouped into sessions"),
                   gap_minutes: float = Query(30.0, gt=0, le=24 * 60, description="Inactivity gap that starts a new session")
                   ) -> svc.Sessionization:
    return svc.Sessionization(mode, gap_minutes)


def _out(tl: TaskLog) -> dict:
    return {"id": tl.id, "name": tl.name, "description": tl.description, "is_demo": tl.is_demo, "version": tl.version,
            "source_filename": tl.source_filename, "source_sha256": tl.source_sha256,
            "source_size_bytes": tl.source_size_bytes, "mapping": tl.mapping, "source_timezone": tl.source_timezone,
            "counts": tl.counts, "findings": tl.findings, "ignored_columns": tl.ignored_columns,
            "linked_dataset_id": tl.linked_dataset_id, "imported_at": tl.imported_at.isoformat() + "Z"}


def _read(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_bytes
    raw = file.file.read(limit + 1)
    if len(raw) > limit:
        raise AppError(413, "file_too_large", max_mb=get_settings().max_upload_mb)
    return raw


def _import_error(exc: svc.TaskImportError) -> AppError:
    return AppError(422, exc.key.split(".")[-1], key=exc.key, details=exc.findings or None, **exc.params)


@router.get("", summary="List task-event logs")
def list_logs(db: Session = Depends(get_db)):
    return {"items": [_out(x) for x in db.scalars(select(TaskLog).order_by(TaskLog.imported_at.desc())).all()]}


@router.post("/preview", summary="Columns, suggested mapping and sample rows of a task-event CSV")
def preview(file: UploadFile = File(...)):
    try:
        return svc.preview(_read(file))
    except svc.TaskImportError as exc:
        raise _import_error(exc) from None


@router.post("/upload", status_code=201, summary="Import a task-event CSV (only mapped fields are stored)")
def upload(file: UploadFile = File(...), mapping: str = Form(..., description="JSON object: field -> column"),
           name: str | None = Form(None), source_timezone: str = Form("UTC"),
           invalid_row_policy: Literal["reject_import", "skip_invalid"] = Form("reject_import"),
           linked_dataset_id: int | None = Form(None), db: Session = Depends(get_db)):
    raw = _read(file)
    try:
        mapping_ = json.loads(mapping)
        if not isinstance(mapping_, dict):
            raise ValueError
    except ValueError:
        raise AppError(422, "invalid_mapping", key="task.err.mapping_json") from None
    if linked_dataset_id is not None and db.get(Dataset, linked_dataset_id) is None:
        raise not_found("dataset")
    filename = (file.filename or "tasks.csv").replace("/", "_").replace("\\", "_")
    try:
        parsed = svc.parse(raw, mapping_, source_timezone, invalid_row_policy)
    except svc.TaskImportError as exc:
        raise _import_error(exc) from None
    tl = svc.store(db, parsed, raw, name or filename.rsplit(".", 1)[0], filename, mapping_, source_timezone,
                   linked_dataset_id)
    return _out(tl)


@router.post("/demo", status_code=201, summary="Import the synthetic accounts-payable task-event demo log")
def demo(db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    raw = DEMO_FILE.read_bytes()
    mapping = svc.suggest_mapping(svc.read_table(raw).columns.tolist())
    parsed = svc.parse(raw, mapping)
    demo_ds = db.scalars(select(Dataset).where(Dataset.is_demo.is_(True), Dataset.status == "ready",
                                               Dataset.source_filename == "p2p_demo_events.csv")
                         .order_by(Dataset.id.desc())).first()  # task events refer to purchase orders
    tl = svc.store(db, parsed, raw, t(lang, "task.demo_name"), DEMO_FILE.name, mapping, "UTC",
                   demo_ds.id if demo_ds else None, t(lang, "task.demo_description"), is_demo=True)
    return _out(tl)


@router.get("/{log_id}", summary="One task-event log with import findings")
def get_log(log_id: int, db: Session = Depends(get_db)):
    return _out(_log(db, log_id))


@router.delete("/{log_id}", status_code=204, summary="Delete a task-event log")
def delete_log(log_id: int, db: Session = Depends(get_db)):
    svc.delete_log(db, _log(db, log_id))
    return Response(status_code=204)


class LinkRequest(BaseModel):
    dataset_id: int | None


@router.put("/{log_id}/link", summary="Link business_case_id values to a case-centric dataset")
def link(log_id: int, body: LinkRequest, db: Session = Depends(get_db)):
    tl = _log(db, log_id)
    if body.dataset_id is not None and db.get(Dataset, body.dataset_id) is None:
        raise not_found("dataset")
    tl.linked_dataset_id = body.dataset_id
    db.commit()
    return _out(tl)


@router.get("/{log_id}/summary", summary="Session and event counts for a sessionization")
def summary(log_id: int, s: svc.Sessionization = Depends(sessionization), db: Session = Depends(get_db)):
    return svc.summary(db, _log(db, log_id), s)


@router.get("/{log_id}/variants", summary="Task variants: sessions with the same sequence")
def variants(log_id: int, granularity: Granularity = "action", limit: int = Query(50, ge=1, le=500),
             s: svc.Sessionization = Depends(sessionization), db: Session = Depends(get_db)):
    return svc.variants(db, _log(db, log_id), s, granularity, limit)


@router.get("/{log_id}/sequences", summary="Repeated contiguous task sequences across sessions")
def sequences(log_id: int, granularity: Granularity = "action", min_length: int = Query(2, ge=2, le=10),
              max_length: int = Query(5, ge=2, le=10), min_sessions: int = Query(2, ge=1),
              limit: int = Query(50, ge=1, le=500), s: svc.Sessionization = Depends(sessionization),
              db: Session = Depends(get_db)):
    if max_length < min_length:
        raise AppError(422, "invalid_range", key="task.err.length_range")
    return svc.repeated_sequences(db, _log(db, log_id), s, granularity, min_length, max_length, min_sessions, limit)


@router.get("/{log_id}/switches", summary="Application switches within sessions")
def switches(log_id: int, s: svc.Sessionization = Depends(sessionization), db: Session = Depends(get_db)):
    return svc.app_switches(db, _log(db, log_id), s)


@router.get("/{log_id}/dfg", summary="Task-level directly-follows graph (frequencies; explicit durations only)")
def dfg(log_id: int, granularity: Granularity = "action", s: svc.Sessionization = Depends(sessionization),
        db: Session = Depends(get_db)):
    return svc.task_dfg(db, _log(db, log_id), s, granularity)


@router.get("/{log_id}/cases", summary="Business cases touched by task sessions")
def cases(log_id: int, dataset_id: int | None = None, page: int = Query(1, ge=1),
          page_size: int = Query(50, ge=1, le=500), s: svc.Sessionization = Depends(sessionization),
          db: Session = Depends(get_db)):
    tl = _log(db, log_id)
    return svc.business_cases(db, tl, s, dataset_id or tl.linked_dataset_id, page, page_size)
