"""Shared API dependencies: dataset lookup and the common filter query parameters."""

from __future__ import annotations

from datetime import date

from fastapi import Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError, not_found
from app.models import ConformanceRule, Dataset, OcelLog
from app.schemas.filters import FilterSpec
from app.services.conformance import lookup_factory
from app.services.eventlog import EventLog, get_event_log
from app.services.filtering import FilteredView, apply_filters
from app.services.preferences import language_for


def request_lang(request: Request) -> str:
    """Response language: ``X-ProcessLens-Language`` header, else the saved user preference."""
    return language_for(request)


def get_dataset(dataset_id: int, db: Session = Depends(get_db)) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise not_found("dataset")
    if ds.status != "ready":
        raise AppError(409, "dataset_not_ready", status=ds.status)
    return ds


def filter_params(
    start_from: date | None = Query(None, description="Case start date ≥ (inclusive, display timezone)"),
    start_to: date | None = Query(None, description="Case start date ≤ (inclusive, display timezone)"),
    activity: list[str] = Query(default=[], description="Case contains every listed activity"),
    exclude_activity: list[str] = Query(default=[], description="Case contains none of the listed activities"),
    resource: list[str] = Query(default=[], description="Any event of the case has one of these resources"),
    department: list[str] = Query(default=[], description="Any event of the case has one of these departments"),
    variant: list[str] = Query(default=[], description="Case follows one of these variant IDs"),
    min_duration_h: float | None = Query(None, ge=0, description="Observed duration ≥ hours (inclusive)"),
    max_duration_h: float | None = Query(None, ge=0, description="Observed duration ≤ hours (inclusive)"),
    rework: bool | None = Query(None, description="true: rework cases only; false: no rework"),
    status: list[str] = Query(default=[], description="completed / incomplete / unknown"),
    conformance_rule: int | None = Query(None, description="Conformance rule ID used with conformance_result"),
    conformance_result: list[str] = Query(default=[], description="compliant / violating / not_evaluable"),
    transition_from: str | None = Query(None, description="Directly-follows source (or __start__)"),
    transition_to: str | None = Query(None, description="Directly-follows target (or __end__)"),
    object_type: str | None = Query(None, description="Lead object type: its objects are the cases of the analysis "
                                                      "(default: the dataset's main object type)"),
) -> FilterSpec:
    try:
        return FilterSpec(
            start_from=start_from, start_to=start_to, activity=activity, exclude_activity=exclude_activity,
            resource=resource, department=department, variant=variant, min_duration_h=min_duration_h,
            max_duration_h=max_duration_h, rework=rework, status=status, conformance_rule=conformance_rule,
            conformance_result=conformance_result, transition_from=transition_from, transition_to=transition_to,
            object_type=object_type or None,
        )
    except ValidationError as exc:
        msgs = [e["msg"] for e in exc.errors()]
        raise AppError(422, "invalid_filter", "; ".join(msgs), [
            {"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]) from None


def rule_getter(db: Session, dataset_id: int):
    def get(rule_id: int) -> ConformanceRule | None:
        return db.scalars(select(ConformanceRule).where(
            ConformanceRule.id == rule_id, ConformanceRule.dataset_id == dataset_id)).first()
    return get


def dataset_object_types(db: Session, ds: Dataset) -> list[str]:
    ocel = db.get(OcelLog, ds.ocel_log_id) if ds.ocel_log_id else None
    return [t["name"] for t in (ocel.object_types if ocel else [])]


def lead_type(db: Session, ds: Dataset, object_type: str | None) -> str:
    """Validated lead object type (default: the dataset's main type)."""
    ot = object_type or ds.main_object_type
    types = dataset_object_types(db, ds)
    if types and ot not in types:
        raise AppError(422, "unknown_object_type", key="ocel.err.unknown_object_type", types=ot)
    return ot


def rule_type(ds: Dataset, rule) -> str:
    return rule.object_type or ds.main_object_type


def rule_log(db: Session, ds: Dataset, rule) -> EventLog:
    """The event log of the object type a conformance rule applies to."""
    return get_event_log(db, ds, rule_type(ds, rule))


def build_view(db: Session, ds: Dataset, spec: FilterSpec) -> tuple[EventLog, FilteredView]:
    log = get_event_log(db, ds, lead_type(db, ds, spec.object_type))
    try:
        view = apply_filters(log, spec, lookup_factory(log, rule_getter(db, ds.id), ds.main_object_type))
    except LookupError as exc:
        raise AppError(422, "invalid_filter", str(exc)) from None
    return log, view
