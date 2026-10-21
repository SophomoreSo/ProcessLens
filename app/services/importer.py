"""Upload staging, validation runs and the transactional import pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import Dataset, Event, ImportFinding, ImportJob, QuarantinedRow, Upload
from app.models.dataset import utcnow
from app.schemas.imports import ConfirmImportRequest, ImportOptions
from app.services import jobs, xes
from app.services.cache import cache
from app.services.ingestion import (
    IngestionError,
    StagedFile,
    build_preview,
    count_data_rows,
    detect_delimiter,
    detect_encoding,
    read_raw_table,
    sanitize_display_filename,
    stage_upload,
)
from app.services.validation import ValidationResult, preview_records, validate_table
from app.i18n import render, t

log = logging.getLogger("processlens.import")

INSERT_CHUNK = 5000
SUMMARY_FINDINGS = 200


class ImportRejected(Exception):
    """Import refused for a reason the user can fix. Message = catalog entry ``key``."""

    def __init__(self, key: str, **params):
        self.key = key
        self.params = params
        super().__init__(t("en", key, **params))


# --------------------------------------------------------------------------- uploads
def _register_upload(db: Session, staged: StagedFile, original: str, settings: Settings) -> tuple[Upload, dict]:
    csv_path, conversion = staged.path, None
    try:
        if staged.source_format == "xes":
            csv_path = settings.upload_dir / f"{staged.upload_id}.csv"
            conversion = xes.convert(staged.path, csv_path, settings)
        encoding = detect_encoding(csv_path)
        delimiter = detect_delimiter(csv_path, encoding)
        preview = build_preview(csv_path, encoding, delimiter, settings)
        row_count = count_data_rows(csv_path, encoding, delimiter)
    except IngestionError:
        staged.path.unlink(missing_ok=True)
        csv_path.unlink(missing_ok=True)
        raise
    upload = Upload(
        id=staged.upload_id, original_filename=original, stored_name=csv_path.name,
        size_bytes=staged.size_bytes, sha256=staged.sha256, encoding=encoding, delimiter=delimiter,
        columns=preview["columns"], row_count=row_count, source_format=staged.source_format,
        source_name=staged.stored_name if staged.source_format == "xes" else None, conversion=conversion,
    )
    db.add(upload)
    db.commit()
    log.info("Staged %s upload %s (%d bytes, %d rows, %d columns)", upload.source_format, upload.id,
             upload.size_bytes, row_count, len(preview["columns"]))
    return upload, _with_source(upload, preview)


def _with_source(upload: Upload, preview: dict) -> dict:
    return {**preview, "source_format": upload.source_format or "csv", "conversion": upload.conversion,
            "conversion_findings": xes.findings(upload.conversion) if upload.conversion else []}


def reconvert_xes(db: Session, upload: Upload, lifecycle: str) -> dict:
    """Convert the stored original XES file again with another lifecycle handling."""
    settings = get_settings()
    if upload.source_format != "xes" or not upload.source_name:
        raise IngestionError("not_xes")
    src = settings.upload_dir / upload.source_name
    if not src.exists():
        raise IngestionError("upload_missing")
    tmp = settings.upload_dir / f"{upload.id}.converting.csv"
    try:
        conversion = xes.convert(src, tmp, settings, lifecycle)
        tmp.replace(upload_path(upload))
    finally:
        tmp.unlink(missing_ok=True)
    preview = build_preview(upload_path(upload), upload.encoding, upload.delimiter, settings)
    upload.conversion = conversion
    upload.columns = preview["columns"]
    upload.row_count = count_data_rows(upload_path(upload), upload.encoding, upload.delimiter)
    upload.last_validation = None
    db.commit()
    return _with_source(upload, preview)


def create_upload(db: Session, stream: BinaryIO, filename: str | None) -> tuple[Upload, dict]:
    settings = get_settings()
    original = sanitize_display_filename(filename)
    staged = stage_upload(stream, original, settings)
    return _register_upload(db, staged, original, settings)


def create_upload_from_path(db: Session, path: Path, display_name: str) -> tuple[Upload, dict]:
    with path.open("rb") as fh:
        return create_upload(db, fh, display_name)


def upload_path(upload: Upload) -> Path:
    return get_settings().upload_dir / upload.stored_name


def preview_for_upload(upload: Upload, encoding: str | None = None, delimiter: str | None = None) -> dict:
    settings = get_settings()
    if upload.source_format == "xes":  # the converted table is always UTF-8 with commas
        encoding = delimiter = None
    return _with_source(upload, build_preview(upload_path(upload), encoding or upload.encoding,
                                              delimiter or upload.delimiter, settings))


# --------------------------------------------------------------------------- validation
def run_validation(upload: Upload, options: ImportOptions, max_findings: int | None = None) -> ValidationResult:
    settings = get_settings()
    path = upload_path(upload)
    if not path.exists():
        raise IngestionError("upload_missing")
    if upload.source_format == "xes":  # converted table: fixed encoding and delimiter
        table = read_raw_table(path, upload.encoding, upload.delimiter, settings)
    else:
        table = read_raw_table(path, options.encoding or upload.encoding, options.delimiter or upload.delimiter,
                               settings)
    return validate_table(table, options.mapping, options.timestamps, max_findings=max_findings)


def localized_findings(findings: list[dict], lang: str) -> list[dict]:
    return [{**f, "message": render(lang, f.get("message_key"), f.get("message_params"), f["message"])} for f in findings]


def validation_summary(upload: Upload, result: ValidationResult, lang: str = "en") -> dict:
    invalid = len(result.invalid_rows)
    file_errors = [f for f in result.findings if f["severity"] == "error" and f["row"] is None]
    return {
        "upload_id": upload.id,
        "total_rows": result.total_rows,
        "valid_rows": result.valid_rows,
        "invalid_rows": invalid,
        "case_count": int(result.normalized["case_id"].nunique()) if result.valid_rows else 0,
        "error_count": result.count("error"),
        "warning_count": result.count("warning"),
        "info_count": result.count("info"),
        "counts_by_code": dict(result.counts_by_code),
        "findings": localized_findings(result.findings[:SUMMARY_FINDINGS], lang),
        "findings_truncated": len(result.findings) > SUMMARY_FINDINGS,
        "preview_rows": preview_records(result.normalized),
        "can_import_with_reject_policy": invalid == 0 and not file_errors and result.valid_rows > 0,
        "timestamp_tie_cases": result.tie_cases,
    }


# --------------------------------------------------------------------------- import job
def start_import(db: Session, upload: Upload, request: ConfirmImportRequest, kind: str = "import",
                 extra: dict | None = None) -> ImportJob:
    if request.target_dataset_id is not None and db.get(Dataset, request.target_dataset_id) is None:
        raise LookupError("Target dataset not found")
    job = ImportJob(
        id=jobs.new_job_id(), kind=kind, status="queued", progress=0.0, stage="job.stage.queued",
        upload_id=upload.id, target_dataset_id=request.target_dataset_id,
        options={"request": request.model_dump(mode="json"), **(extra or {})},
    )
    db.add(job)
    db.commit()
    jobs.set_progress(job.id, 0.0, "job.stage.queued")
    jobs.submit(job.id, _run_import_job)
    return job


def _naive_utc(series: pd.Series) -> list:
    s = series.dt.tz_convert("UTC").dt.tz_localize(None)
    return [None if pd.isna(v) else v.to_pydatetime() for v in s]


def _optional_values(series: pd.Series) -> list:
    return [None if v is None or (isinstance(v, float) and np.isnan(v)) else v for v in series.tolist()]


def _event_records(dataset_id: int, norm: pd.DataFrame) -> list[dict]:
    cols = {
        "source_row": norm["source_row"].astype(int).tolist(),
        "seq": norm["seq"].astype(int).tolist(),
        "case_id": norm["case_id"].tolist(),
        "activity": norm["activity"].tolist(),
        "ts": _naive_utc(norm["ts"]),
        "start_ts": _naive_utc(norm["start_ts"]),
        "event_id": _optional_values(norm["event_id"]),
        "resource": _optional_values(norm["resource"]),
        "department": _optional_values(norm["department"]),
        "cost": [None if (v is None or np.isnan(v)) else float(v) for v in norm["cost"].tolist()],
        "case_status": _optional_values(norm["case_status"]),
        "attributes": norm["attributes"].tolist(),
    }
    n = len(norm)
    return [{"dataset_id": dataset_id, **{k: v[i] for k, v in cols.items()}} for i in range(n)]


def _persist(db: Session, job_id: str, upload: Upload, request: ConfirmImportRequest,
             result: ValidationResult, is_demo: bool, description: str | None) -> Dataset:
    norm = result.normalized
    if request.target_dataset_id is not None:
        ds = db.get(Dataset, request.target_dataset_id)
        if ds is None:
            raise ImportRejected("import.target_deleted")
        db.execute(delete(Event).where(Event.dataset_id == ds.id))
        db.execute(delete(ImportFinding).where(ImportFinding.dataset_id == ds.id))
        db.execute(delete(QuarantinedRow).where(QuarantinedRow.dataset_id == ds.id))
        ds.version += 1
        ds.settings_revision += 1
    else:
        ds = Dataset(name=request.dataset_name or Path(upload.original_filename).stem[:200], version=1)
        db.add(ds)
    ds.status = "ready"
    ds.is_demo = is_demo
    if description is not None:
        ds.description = description
    if request.dataset_name:
        ds.name = request.dataset_name
    ds.source_filename = upload.original_filename
    ds.source_sha256 = upload.sha256
    ds.source_size_bytes = upload.size_bytes
    ds.upload_id = upload.id
    ds.import_options = request.model_dump(mode="json")
    if upload.source_format == "xes":  # provenance: how the XES file became rows
        ds.import_options["source_format"] = "xes"
        ds.import_options["xes_conversion"] = {k: v for k, v in (upload.conversion or {}).items() if k != "columns"}
    ds.imported_at = utcnow()
    ds.source_row_count = result.total_rows
    ds.event_count = len(norm)
    ds.case_count = int(norm["case_id"].nunique())
    ds.quarantined_count = len(result.invalid_rows)
    ds.error_count = result.count("error")
    ds.warning_count = result.count("warning")
    ds.display_timezone = request.display_timezone or request.timestamps.source_timezone
    ds.completion_mode = request.completion.mode
    ds.completion_activities = request.completion.end_activities
    ds.completed_status_values = request.completion.completed_status_values
    ds.open_status_values = request.completion.open_status_values
    ref = request.reference_time
    if ref is not None and ref.tzinfo is not None:
        ref = pd.Timestamp(ref).tz_convert("UTC").tz_localize(None).to_pydatetime()
    ds.reference_time = ref
    ds.attribute_columns = request.mapping.attributes
    db.flush()

    records = _event_records(ds.id, norm)
    total = max(len(records), 1)
    for i in range(0, len(records), INSERT_CHUNK):
        db.execute(insert(Event), records[i:i + INSERT_CHUNK])
        jobs.set_progress(job_id, 0.35 + 0.5 * min(i + INSERT_CHUNK, total) / total, "job.stage.writing",
                          done=f"{min(i + INSERT_CHUNK, total):,}", total=f"{total:,}")

    finding_rows = [{"dataset_id": ds.id, "dataset_version": ds.version, **f} for f in result.findings]
    for i in range(0, len(finding_rows), INSERT_CHUNK):
        db.execute(insert(ImportFinding), finding_rows[i:i + INSERT_CHUNK])

    if result.invalid_rows:
        raw = result.raw.set_index("__row")
        q_rows = []
        for row, reasons in sorted(result.invalid_rows.items()):
            rec = raw.loc[row].to_dict()
            q_rows.append({"dataset_id": ds.id, "dataset_version": ds.version, "row": int(row), "raw": rec,
                           "reasons": sorted(set(reasons))})
        for i in range(0, len(q_rows), INSERT_CHUNK):
            db.execute(insert(QuarantinedRow), q_rows[i:i + INSERT_CHUNK])
    return ds


def _finish_job(job_id: str, **fields) -> None:
    with SessionLocal() as db:
        job = db.get(ImportJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        job.updated_at = utcnow()
        db.commit()


def _run_import_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(ImportJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.stage = "job.stage.reading"
        db.commit()
        request = ConfirmImportRequest.model_validate(job.options["request"])
        is_demo = bool(job.options.get("demo"))
        upload = db.get(Upload, job.upload_id)
        if upload is None:
            raise ImportRejected("error.upload_missing")

        jobs.set_progress(job_id, 0.05, "job.stage.reading")
        result = run_validation(upload, request, max_findings=get_settings().max_findings_stored)
        jobs.set_progress(job_id, 0.3, "job.stage.validated")
        file_errors = [f for f in result.findings if f["severity"] == "error" and f["row"] is None]
        if file_errors:
            raise ImportRejected(file_errors[0]["message_key"], **file_errors[0]["message_params"])
        if result.invalid_rows and request.invalid_row_policy == "reject_import":
            raise ImportRejected("import.rejected_invalid", invalid=f"{len(result.invalid_rows):,}",
                                 total=f"{result.total_rows:,}")
        if result.valid_rows == 0:
            raise ImportRejected("import.no_valid_rows")

        ds = _persist(db, job_id, upload, request, result, is_demo, job.options.get("description"))
        db.commit()
        dataset_id, version = ds.id, ds.version
        cache.invalidate_dataset(dataset_id)
        log.info("Imported dataset %s v%s: %d events, %d cases, %d quarantined", dataset_id, version,
                 ds.event_count, ds.case_count, ds.quarantined_count)

        job_result: dict = {"dataset_id": dataset_id, "version": version, "events": ds.event_count,
                            "cases": ds.case_count, "quarantined_rows": ds.quarantined_count}
        jobs.set_progress(job_id, 0.9, "job.stage.alerts")
        try:
            if is_demo:
                from app.services.demo import create_demo_rules
                create_demo_rules(db, ds, job.options.get("lang", "en"))
            from app.services.alerts import evaluate_alert_rules
            job_result["alerts"] = evaluate_alert_rules(db, ds)
        except Exception:  # alert evaluation must not undo a committed import
            db.rollback()
            log.exception("Post-import alert evaluation failed for dataset %s", dataset_id)
            job_result["alerts_error_key"] = "import.alerts_failed"
        _finish_job(job_id, status="succeeded", progress=1.0, stage="job.stage.completed", dataset_id=dataset_id,
                    result=job_result)
    except (ImportRejected, IngestionError) as exc:
        db.rollback()
        key = exc.key if isinstance(exc, ImportRejected) else f"error.{exc.code}"
        log.info("Import job %s rejected", job_id)
        _finish_job(job_id, status="failed", stage="job.stage.rejected", error=str(exc), error_key=key,
                    error_params=exc.params)
    except ValueError as exc:  # e.g. a mapped column missing after an encoding/delimiter change
        db.rollback()
        log.info("Import job %s rejected (invalid options)", job_id)
        _finish_job(job_id, status="failed", stage="job.stage.rejected", error=str(exc),
                    error_key="import.invalid_options", error_params={"detail": str(exc)})
    except Exception:
        db.rollback()
        log.exception("Import job %s failed", job_id)
        _finish_job(job_id, status="failed", stage="job.stage.failed", error=t("en", "import.internal_error"),
                    error_key="import.internal_error", error_params={})
    finally:
        db.close()
        jobs.clear_progress(job_id)


def delete_upload_file_if_unused(db: Session, upload_id: str | None) -> None:
    if not upload_id:
        return
    still_used = db.query(Dataset).filter(Dataset.upload_id == upload_id).count()
    if still_used:
        return
    upload = db.get(Upload, upload_id)
    if upload is not None:
        upload_path(upload).unlink(missing_ok=True)
        if upload.source_name:
            (get_settings().upload_dir / upload.source_name).unlink(missing_ok=True)
        db.delete(upload)
