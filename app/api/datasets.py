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
    OcelLog,
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


@router.get("", response_model=list[DatasetOut], summary="List datasets")
def list_datasets(db: Session = Depends(get_db)):
    return db.scalars(select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc())).all()


@router.get("/{dataset_id}", response_model=DatasetOut, summary="Dataset metadata")
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    return _dataset(db, dataset_id)


@router.patch("/{dataset_id}", response_model=DatasetOut,
              summary="Update name or analysis settings (completion rule, reference time, display timezone)")
def update_dataset(dataset_id: int, body: DatasetUpdate, db: Session = Depends(get_db)):
    ds = _dataset(db, dataset_id)
    data = body.model_dump(exclude_unset=True)
    analysis_changed = False
    for key in ("name", "description"):
        if key in data and data[key] is not None:
            setattr(ds, key, data[key])
    for key in ("display_timezone", "completion_mode", "completion_activities", "completed_status_values",
                "open_status_values"):
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
    if ds.completion_mode == "end_activity" and not ds.completion_activities:
        raise AppError(422, "invalid_settings", key="error.invalid_settings_end_activity")
    if ds.completion_mode == "case_status" and not ds.completed_status_values:
        raise AppError(422, "invalid_settings", key="error.invalid_settings_case_status")
    if analysis_changed:
        ds.settings_revision += 1
    db.commit()
    cache.invalidate_dataset(ds.id)
    return ds


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
