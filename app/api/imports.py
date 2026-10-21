from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import request_lang
from app.config import PROJECT_ROOT
from app.database import get_db
from app.errors import AppError, not_found
from app.i18n import render, t
from app.models import ImportJob, Upload
from app.schemas.imports import (
    ConfirmImportRequest,
    ImportOptions,
    JobOut,
    UploadPreview,
    ValidationSummary,
    normalize_delimiter,
    normalize_encoding,
)
from app.services import importer, jobs
from app.services.demo import DEMO_FILES, start_demo_import
from app.services.exports import to_csv, to_json
from app.services.validation import preview_records

router = APIRouter(prefix="/api", tags=["imports"])

FINDING_COLUMNS = ["severity", "code", "row", "field", "case_id", "value", "message"]


def _get_upload(db: Session, upload_id: str) -> Upload:
    if not upload_id.isalnum() or len(upload_id) != 32:
        raise not_found("upload")
    up = db.get(Upload, upload_id)
    if up is None:
        raise not_found("upload")
    return up


def _preview(up: Upload, data: dict, lang: str) -> UploadPreview:
    data = {**data, "column_warnings": [t(lang, w["key"], **w["params"]) for w in data["column_warnings"]],
            "conversion_findings": [{**f, "message": t(lang, f["key"], **f["params"])}
                                    for f in data.get("conversion_findings", [])]}
    return UploadPreview(upload_id=up.id, original_filename=up.original_filename, size_bytes=up.size_bytes,
                         encoding=up.encoding, delimiter=up.delimiter, row_count=up.row_count, **data)


@router.post("/uploads", response_model=UploadPreview, status_code=201,
             summary="Upload a CSV or XES (.xes, .xes.gz) file and get a preview with suggested mappings")
def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    up, data = importer.create_upload(db, file.file, file.filename)
    return _preview(up, data, lang)


@router.get("/uploads/{upload_id}", response_model=UploadPreview, summary="Preview a staged upload")
def get_upload(upload_id: str, encoding: str | None = None, delimiter: str | None = None,
               db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    up = _get_upload(db, upload_id)
    try:
        enc, delim = normalize_encoding(encoding), normalize_delimiter(delimiter)
    except ValueError as exc:
        raise AppError(422, "validation_error", str(exc)) from None
    return _preview(up, importer.preview_for_upload(up, enc, delim), lang)


class XesOptions(BaseModel):
    lifecycle: Literal["all_events", "pair_start_complete"]


@router.post("/uploads/{upload_id}/xes-options", response_model=UploadPreview,
             summary="Convert a staged XES upload again with another lifecycle handling")
def xes_options(upload_id: str, body: XesOptions, db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    up = _get_upload(db, upload_id)
    return _preview(up, importer.reconvert_xes(db, up, body.lifecycle), lang)


@router.post("/uploads/{upload_id}/validate", response_model=ValidationSummary,
             summary="Validate a staged upload with a mapping (no data is imported)")
def validate_upload(upload_id: str, options: ImportOptions, db: Session = Depends(get_db),
                    lang: str = Depends(request_lang)):
    up = _get_upload(db, upload_id)
    try:
        result = importer.run_validation(up, options)
    except ValueError as exc:
        raise AppError(422, "invalid_mapping", str(exc)) from None
    summary = importer.validation_summary(up, result, lang)
    up.last_validation = {"options": options.model_dump(mode="json"),
                          "counts_by_code": summary["counts_by_code"],
                          "valid_rows": summary["valid_rows"], "invalid_rows": summary["invalid_rows"]}
    db.commit()
    return summary


@router.get("/uploads/{upload_id}/validation-report",
            summary="Download the full validation report for the last validation of an upload")
def upload_validation_report(upload_id: str, format: str = Query("csv", pattern="^(csv|json)$"),
                             db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    up = _get_upload(db, upload_id)
    if not up.last_validation:
        raise AppError(409, "not_validated")
    options = ImportOptions.model_validate(up.last_validation["options"])
    result = importer.run_validation(up, options)
    findings = importer.localized_findings(result.findings, lang)
    name = f"validation_report_{up.id[:8]}.{format}"
    if format == "json":
        body = to_json({"upload_id": up.id, "file": up.original_filename, "total_rows": result.total_rows,
                        "valid_rows": result.valid_rows, "invalid_rows": len(result.invalid_rows),
                        "counts_by_code": dict(result.counts_by_code), "findings": findings,
                        "normalized_preview": preview_records(result.normalized)})
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return Response(to_csv(findings, FINDING_COLUMNS), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/uploads/{upload_id}/import", response_model=JobOut, status_code=202,
             summary="Confirm an import; runs as a background job")
def confirm_import(upload_id: str, request: ConfirmImportRequest, db: Session = Depends(get_db),
                   lang: str = Depends(request_lang)):
    up = _get_upload(db, upload_id)
    missing = [c for c in list(request.mapping.mapped_columns().values()) + request.mapping.attributes
               if c not in up.columns]
    if missing and not request.encoding and not request.delimiter:
        raise AppError(422, "invalid_mapping", ", ".join(sorted(set(missing))), key="error.mapped_columns_missing")
    try:
        job = importer.start_import(db, up, request)
    except LookupError:
        raise not_found("dataset") from None
    return _job_out(job, lang)


def _job_out(job: ImportJob, lang: str) -> JobOut:
    out = JobOut.model_validate(job, from_attributes=True)
    live = jobs.live_progress(job.id)
    stage_key, stage_params = job.stage, {}
    if live and job.status in ("queued", "running"):
        out.progress = live["progress"]
        stage_key, stage_params = live["stage_key"], live["stage_params"]
    out.stage = render(lang, stage_key, stage_params, fallback=job.stage)
    out.error = render(lang, job.error_key, job.error_params, fallback=job.error) if job.error else None
    if out.result and out.result.get("alerts_error_key"):
        out.result = {**out.result, "alerts_error": t(lang, out.result["alerts_error_key"])}
    return out


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Import job status and progress")
def get_job(job_id: str, db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    job = db.get(ImportJob, job_id)
    if job is None:
        raise not_found("job")
    db.refresh(job)
    return _job_out(job, lang)


@router.post("/demo/load", response_model=JobOut, status_code=202,
             summary="Load the synthetic purchase-to-pay demo dataset")
def load_demo(db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    return _job_out(start_demo_import(db, lang), lang)


@router.get("/sample-data/{name}", summary="Download a sample CSV file")
def sample_data(name: str):
    if name not in DEMO_FILES:
        raise not_found("sample_file")
    path = PROJECT_ROOT / "sample_data" / DEMO_FILES[name]
    return FileResponse(path, media_type="text/csv", filename=DEMO_FILES[name])
