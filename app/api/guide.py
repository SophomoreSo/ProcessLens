"""Guided analysis: predefined questions answered by deterministic backend functions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import build_view, filter_params, get_dataset
from app.database import get_db
from app.errors import AppError, not_found
from app.models import Dataset, OcelLog
from app.schemas.filters import FilterSpec
from app.services import guide

router = APIRouter(prefix="/api/guide", tags=["guided analysis"])
RESERVED = {"question", "dataset_id", "ocel_log_id"} | set(FilterSpec.model_fields)


@router.get("/questions", summary="Questions, the data they need and their parameters")
def questions():
    return {"questions": guide.describe()}


def _params(request: Request) -> dict:
    return {k: v for k, v in request.query_params.items() if k not in RESERVED}


def _error(exc: guide.GuideError) -> AppError:
    return AppError(422, exc.key.split(".")[-1], key=exc.key, **exc.params)


@router.get("/answer", summary="Answer one question (dataset questions honour the usual filter parameters)")
def answer(request: Request, question: str = Query(...), dataset_id: int | None = None, ocel_log_id: int | None = None,
           spec: FilterSpec = Depends(filter_params), db: Session = Depends(get_db)):
    q = guide.BY_ID.get(question)
    if q is None:
        raise _error(guide.GuideError("guide.err.unknown_question", question=question))
    try:
        if q.source == "ocel":
            if ocel_log_id is None:
                raise guide.GuideError("guide.err.needs_ocel")
            ocel = db.get(OcelLog, ocel_log_id)
            if ocel is None:
                raise not_found("ocel_log")
            return guide.answer(db, question, _params(request), ocel=ocel)
        if dataset_id is None:
            raise guide.GuideError("guide.err.needs_dataset")
        ds: Dataset = get_dataset(dataset_id, db)
        _, view = build_view(db, ds, spec)
        return guide.answer(db, question, _params(request), ds=ds, view=view)
    except guide.GuideError as exc:
        raise _error(exc) from None
