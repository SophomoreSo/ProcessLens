from __future__ import annotations

import logging

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import request_lang
from app.database import get_db
from app.i18n import render
from app.errors import AppError, not_found
from app.models import (
    Alert,
    AlertRule,
    ConformanceRule,
    Dataset,
    ImportFinding,
    MiningArtifact,
    MiningRun,
    OcelE2O,
    OcelLog,
    OcelObject,
    QuarantinedRow,
    ReferenceModel,
    TaskLog,
)
from app.schemas.datasets import DatasetOut, DatasetUpdate, FindingOut
from app.services import ocel_store
from app.services.cache import cache
from app.services.exports import to_csv, to_json
from app.services.importer import delete_upload_file_if_unused

log = logging.getLogger("processlens.datasets")
router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _dataset(db: Session, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise not_found("dataset")
    return ds


def _object_types(db: Session, ds: Dataset) -> list[dict]:
    if not ds.ocel_log_id:
        return []
    objects = dict(db.execute(select(OcelObject.object_type, func.count()).where(OcelObject.log_id == ds.ocel_log_id)
                              .group_by(OcelObject.object_type)).all())
    events = dict(db.execute(
        select(OcelObject.object_type, func.count(func.distinct(OcelE2O.event_id)))
        .join(OcelE2O, (OcelE2O.log_id == OcelObject.log_id) & (OcelE2O.object_id == OcelObject.object_id))
        .where(OcelObject.log_id == ds.ocel_log_id).group_by(OcelObject.object_type)).all())
    ocel = db.get(OcelLog, ds.ocel_log_id)
    names = [t["name"] for t in (ocel.object_types if ocel else [])] or sorted(objects)
    return [{"name": n, "objects": int(objects.get(n, 0)), "events": int(events.get(n, 0))} for n in names]


def _out(db: Session, ds: Dataset) -> DatasetOut:
    out = DatasetOut.model_validate(ds)
    out.object_type_settings = ds.object_type_settings or {}
    out.object_types = _object_types(db, ds)
    return out


@router.get("", response_model=list[DatasetOut], summary="List datasets")
def list_datasets(db: Session = Depends(get_db)):
    return [_out(db, ds) for ds in db.scalars(select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc()))]


@router.get("/{dataset_id}", response_model=DatasetOut, summary="Dataset metadata (with its object types)")
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    return _out(db, _dataset(db, dataset_id))


@router.patch("/{dataset_id}", response_model=DatasetOut,
              summary="Update name or analysis settings (completion rule, reference time, display timezone)")
def update_dataset(dataset_id: int, body: DatasetUpdate, db: Session = Depends(get_db)):
    ds = _dataset(db, dataset_id)
    data = body.model_dump(exclude_unset=True)
    analysis_changed = False
    types = [t["name"] for t in _object_types(db, ds)]
    for key in ("name", "description"):
        if key in data and data[key] is not None:
            setattr(ds, key, data[key])
    if body.main_object_type is not None and body.main_object_type != ds.main_object_type:
        if body.main_object_type not in types:
            raise AppError(422, "unknown_object_type", key="ocel.err.unknown_object_type", types=body.main_object_type)
        # the settings stay with their object type when the main type changes
        old_main = ds.main_object_type
        # rules and models without an explicit type meant the old main type: keep them on it
        for model in (ConformanceRule, AlertRule, ReferenceModel):
            db.execute(update(model).where(model.dataset_id == ds.id, model.object_type.is_(None))
                       .values(object_type=old_main))
        settings = dict(ds.object_type_settings or {})
        settings[old_main] = _type_settings_json(ds)
        new = settings.pop(body.main_object_type, {})
        ds.main_object_type = body.main_object_type
        _apply_type_settings(ds, new)
        ds.object_type_settings = settings
        analysis_changed = True
    if "display_timezone" in data and data["display_timezone"] is not None:
        ds.display_timezone = data["display_timezone"]
        analysis_changed = True
    target = body.object_type or ds.main_object_type
    if target not in types:
        raise AppError(422, "unknown_object_type", key="ocel.err.unknown_object_type", types=target)
    if target != ds.main_object_type:  # settings of another object type live in object_type_settings
        settings = dict(ds.object_type_settings or {})
        cur = dict(settings.get(target, {}))
        for key in ("completion_mode", "completion_activities", "completed_status_values", "open_status_values"):
            if key in data and data[key] is not None:
                cur[key] = data[key]
                analysis_changed = True
        if body.clear_reference_time:
            cur.pop("reference_time", None)
            analysis_changed = True
        elif "reference_time" in data and body.reference_time is not None:
            ref = pd.Timestamp(body.reference_time)
            cur["reference_time"] = (ref.tz_convert("UTC").tz_localize(None) if ref.tzinfo else ref).isoformat()
            analysis_changed = True
        _check_type_settings(cur.get("completion_mode", "none"), cur.get("completion_activities", []),
                             cur.get("completed_status_values", []))
        settings[target] = cur
        ds.object_type_settings = settings
        if analysis_changed:
            ds.settings_revision += 1
        db.commit()
        cache.invalidate_dataset(ds.id)
        return _out(db, ds)
    for key in ("completion_mode", "completion_activities", "completed_status_values", "open_status_values"):
        if key in data and data[key] is not None:
            setattr(ds, key, data[key])
            analysis_changed = True
    if body.clear_reference_time:
        ds.reference_time = None
        analysis_changed = True
    elif "reference_time" in data and body.reference_time is not None:
        ref = pd.Timestamp(body.reference_time)
        ref = ref.tz_convert("UTC").tz_localize(None) if ref.tzinfo else ref
        ds.reference_time = ref.to_pydatetime()
        analysis_changed = True
    _check_type_settings(ds.completion_mode, ds.completion_activities, ds.completed_status_values)
    if analysis_changed:
        ds.settings_revision += 1
    db.commit()
    cache.invalidate_dataset(ds.id)
    return _out(db, ds)


def _check_type_settings(mode: str, activities: list, done_values: list) -> None:
    if mode == "end_activity" and not activities:
        raise AppError(422, "invalid_settings", key="error.invalid_settings_end_activity")
    if mode == "case_status" and not done_values:
        raise AppError(422, "invalid_settings", key="error.invalid_settings_case_status")


def _type_settings_json(ds: Dataset) -> dict:
    return {"completion_mode": ds.completion_mode, "completion_activities": ds.completion_activities,
            "completed_status_values": ds.completed_status_values, "open_status_values": ds.open_status_values,
            **({"reference_time": ds.reference_time.isoformat()} if ds.reference_time else {})}


def _apply_type_settings(ds: Dataset, s: dict) -> None:
    ds.completion_mode = s.get("completion_mode", "none")
    ds.completion_activities = s.get("completion_activities", [])
    ds.completed_status_values = s.get("completed_status_values", [])
    ds.open_status_values = s.get("open_status_values", [])
    ds.reference_time = pd.Timestamp(s["reference_time"]).to_pydatetime() if s.get("reference_time") else None


@router.delete("/{dataset_id}", status_code=204,
               summary="Delete a dataset (requires confirm_name equal to the dataset name)")
def delete_dataset(dataset_id: int, confirm_name: str = Query(..., description="Must equal the dataset name"),
                   db: Session = Depends(get_db)):
    ds = _dataset(db, dataset_id)
    if confirm_name != ds.name:
        raise AppError(422, "confirmation_mismatch")
    upload_id = ds.upload_id
    run_ids = select(MiningRun.id).where((MiningRun.dataset_id == ds.id) | (
        (MiningRun.ocel_log_id == ds.ocel_log_id) if ds.ocel_log_id else False))
    db.execute(delete(MiningArtifact).where(MiningArtifact.run_id.in_(run_ids)))
    db.execute(delete(MiningRun).where(MiningRun.id.in_(db.scalars(run_ids).all())))
    db.execute(delete(ReferenceModel).where(ReferenceModel.dataset_id == ds.id))
    db.execute(update(TaskLog).where(TaskLog.linked_dataset_id == ds.id).values(linked_dataset_id=None))
    for model in (Alert, AlertRule, ConformanceRule, ImportFinding, QuarantinedRow):
        db.execute(delete(model).where(model.dataset_id == ds.id))
    if ds.ocel_log_id:
        ocel_store.clear(db, ds.ocel_log_id)
        db.execute(delete(OcelLog).where(OcelLog.id == ds.ocel_log_id))
    db.delete(ds)
    db.flush()
    delete_upload_file_if_unused(db, upload_id)
    db.commit()
    cache.invalidate_dataset(dataset_id)
    log.info("Deleted dataset %s", dataset_id)
    return Response(status_code=204)


def _finding(f: ImportFinding, lang: str) -> dict:
    out = FindingOut.model_validate(f).model_dump()
    out["message"] = render(lang, f.message_key, f.message_params, fallback=f.message)
    return out


@router.get("/{dataset_id}/validation-report", summary="Import findings of the current dataset version")
def validation_report(dataset_id: int, format: str = Query("json", pattern="^(csv|json)$"),
                      severity: str | None = Query(None, pattern="^(error|warning|info)$"),
                      limit: int = Query(500, ge=1, le=100_000), db: Session = Depends(get_db),
                      lang: str = Depends(request_lang)):
    ds = _dataset(db, dataset_id)
    q = select(ImportFinding).where(ImportFinding.dataset_id == ds.id).order_by(ImportFinding.id)
    if severity:
        q = q.where(ImportFinding.severity == severity)
    if format == "csv":
        rows = [_finding(f, lang) for f in db.scalars(q)]
        return Response(to_csv(rows, ["severity", "code", "row", "field", "case_id", "value", "message"]),
                        media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="dataset_{ds.id}_v{ds.version}_validation.csv"'})
    counts = dict(db.execute(select(ImportFinding.code, func.count()).where(ImportFinding.dataset_id == ds.id)
                             .group_by(ImportFinding.code)).all())
    findings = [_finding(f, lang) for f in db.scalars(q.limit(limit))]
    return {"dataset_id": ds.id, "version": ds.version, "source_row_count": ds.source_row_count,
            "event_count": ds.event_count, "quarantined_count": ds.quarantined_count,
            "error_count": ds.error_count, "warning_count": ds.warning_count,
            "counts_by_code": counts, "findings": findings}


@router.get("/{dataset_id}/quarantine", summary="Quarantined (invalid) source rows with reasons")
def quarantine(dataset_id: int, format: str = Query("json", pattern="^(csv|json)$"),
               limit: int = Query(500, ge=1, le=1_000_000), db: Session = Depends(get_db)):
    ds = _dataset(db, dataset_id)
    rows = db.scalars(select(QuarantinedRow).where(QuarantinedRow.dataset_id == ds.id)
                      .order_by(QuarantinedRow.row).limit(limit)).all()
    items = [{"row": r.row, "reasons": r.reasons, "raw": r.raw} for r in rows]
    if format == "csv":
        raw_cols: list[str] = []
        for it in items:
            for k in it["raw"]:
                if k not in raw_cols:
                    raw_cols.append(k)
        flat = [{"source_row": it["row"], "reasons": ";".join(it["reasons"]), **it["raw"]} for it in items]
        return Response(to_csv(flat, ["source_row", "reasons", *raw_cols]), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="dataset_{ds.id}_quarantine.csv"'})
    return Response(to_json({"dataset_id": ds.id, "total": ds.quarantined_count, "items": items}),
                    media_type="application/json")
