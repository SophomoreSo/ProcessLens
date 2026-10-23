from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import build_view, filter_params, get_dataset, request_lang, rule_getter, rule_type
from app.database import get_db
from app.errors import AppError, not_found
from app.models import ConformanceRule, Dataset
from app.schemas.filters import FilterSpec
from app.services import analytics
from app.services.conformance import describe_rule, evaluate_rule, explain, lookup_factory
from app.services.filtering import intersect
from app.services.ocel import event_attributes, event_objects

router = APIRouter(prefix="/api/datasets/{dataset_id}", tags=["analytics"])


def _v(x):
    """JSON-safe scalar from a DataFrame cell (NaN/NA -> None)."""
    if x is None or (not isinstance(x, str) and pd.isna(x)):
        return None
    return float(x) if isinstance(x, float) else x


def _envelope(ds: Dataset, view, data: dict | list) -> dict:
    return {
        "dataset_id": ds.id,
        "dataset_version": ds.version,
        "object_type": view.log.object_type,
        "filters": view.spec.normalized(),
        "population": {"case_count": view.case_count, "total_case_count": view.total_case_count},
        "display_timezone": ds.display_timezone,
        "data": data,
    }


@router.get("/overview", summary="Dashboard KPIs for the filtered case population")
def overview(ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
             db: Session = Depends(get_db)):
    log, view = build_view(db, ds, spec)
    data = analytics.overview(view)
    data["capabilities"] = analytics.log_capabilities(log)
    return _envelope(ds, view, data)


@router.get("/filter-options", summary="Values available for filter pickers")
def filter_options(ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    log, view = build_view(db, ds, FilterSpec())
    ev = log.events

    def values(col: str) -> list[str]:
        return sorted(ev[col].dropna().astype(str).unique().tolist()) if col in ev else []

    first = log.cases["start"].min() if len(log.cases) else None
    last = log.cases["start"].max() if len(log.cases) else None
    tz = log.display_tz
    return {
        "activities": values("activity"), "resources": values("resource"), "departments": values("department"),
        "case_start_range": {
            "min": first.tz_convert(tz).date().isoformat() if first is not None else None,
            "max": last.tz_convert(tz).date().isoformat() if last is not None else None,
        },
        "conformance_rules": [{"id": r.id, "name": r.name} for r in db.scalars(
            select(ConformanceRule).where(ConformanceRule.dataset_id == ds.id).order_by(ConformanceRule.id))],
        "capabilities": analytics.log_capabilities(log),
        "completion_mode": log.completion_mode,
    }


@router.get("/graph", summary="Directly-follows graph (not a BPMN model)")
def graph(ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
          db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    _, view = build_view(db, ds, spec)
    return _envelope(ds, view, analytics.with_notes(analytics.process_graph(view), lang))


@router.get("/variants", summary="Process variants ranked by case count")
def variants(ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
             limit: int = Query(50, ge=1, le=5000), offset: int = Query(0, ge=0),
             db: Session = Depends(get_db)):
    _, view = build_view(db, ds, spec)
    return _envelope(ds, view, analytics.variants(view, limit=limit, offset=offset))


@router.get("/cases", summary="Paginated, sortable case table")
def cases(ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
          page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
          sort: str = Query("start", pattern="^(case_id|start|end|duration|events|status|rework|cost)$"),
          order: str = Query("asc", pattern="^(asc|desc)$"), db: Session = Depends(get_db)):
    _, view = build_view(db, ds, spec)
    return _envelope(ds, view, analytics.case_rows(view, page, page_size, sort, order))


@router.get("/cases/{case_id:path}", summary="Case (object of the lead type) detail: timeline and conformance findings")
def case_detail(case_id: str, ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
                db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    log, _ = build_view(db, ds, FilterSpec(object_type=spec.object_type))
    if case_id not in log.cases.index:
        raise not_found("case")
    rec = log.cases.loc[case_id].to_dict()
    rec["case_id"] = case_id
    summary = analytics.case_summary(rec, log.variants["label"])
    rows = log.events[log.events["case_id"] == case_id]
    attrs = event_attributes(db, ds.ocel_log_id, rows["ocel_event_id"].tolist())
    others = event_objects(db, ds.ocel_log_id, rows["ocel_event_id"].tolist(), exclude=case_id)
    events, prev = [], None
    for e in rows.itertuples():
        ts = e.ts
        start = e.start_ts if not pd.isna(e.start_ts) else None
        events.append({
            "seq": int(e.seq), "activity": e.activity, "timestamp": ts.isoformat(),
            "start_timestamp": start.isoformat() if start is not None else None,
            "execution_s": (ts - start).total_seconds() if start is not None else None,
            "elapsed_since_previous_s": (ts - prev).total_seconds() if prev is not None else None,
            "resource": _v(e.resource), "department": _v(e.department), "cost": _v(e.cost),
            "case_status": _v(e.case_status), "event_id": _v(e.event_id), "ocel_event_id": e.ocel_event_id,
            "source_row": int(e.source_row), "attributes": attrs.get(e.ocel_event_id) or {},
            "other_objects": others.get(e.ocel_event_id, []), "tied_timestamp": False,
        })
        prev = ts
    for i in range(1, len(events)):
        if events[i]["timestamp"] == events[i - 1]["timestamp"]:
            events[i]["tied_timestamp"] = events[i - 1]["tied_timestamp"] = True
    conformance = []
    for rule in db.scalars(select(ConformanceRule).where(ConformanceRule.dataset_id == ds.id,
                                                         ConformanceRule.enabled.is_(True)).order_by(ConformanceRule.id)):
        if rule_type(ds, rule) != log.object_type:
            continue
        res = evaluate_rule(log, rule).loc[case_id]
        conformance.append({"rule_id": rule.id, "rule_name": rule.name,
                            "rule": describe_rule(rule.rule_type, rule.params, lang),
                            "result": res["result"], "explanation": explain(res, lang)})
    return {"dataset_id": ds.id, "dataset_version": ds.version, "display_timezone": ds.display_timezone,
            "object_type": log.object_type, "case": summary, "events": events, "conformance": conformance,
            "has_timestamp_ties": any(e["tied_timestamp"] for e in events)}


@router.get("/performance", summary="Transition, resource and department performance")
def performance(ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
                db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    _, view = build_view(db, ds, spec)
    return _envelope(ds, view, analytics.with_notes(analytics.performance(view), lang))


class CohortRequest(BaseModel):
    a: FilterSpec = Field(default_factory=FilterSpec)
    b: FilterSpec = Field(default_factory=FilterSpec)
    label_a: str = Field("Cohort A", max_length=100)
    label_b: str = Field("Cohort B", max_length=100)


@router.post("/performance/compare", summary="Compare two cohorts within the globally filtered population")
def compare(body: CohortRequest, ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
            db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    log, base = build_view(db, ds, spec)
    lookup = lookup_factory(log, rule_getter(db, ds.id), ds.main_object_type)
    try:
        a = intersect(base, body.a, lookup)
        b = intersect(base, body.b, lookup)
    except LookupError as exc:
        raise AppError(422, "invalid_filter", str(exc)) from None
    data = analytics.with_notes(analytics.compare_cohorts(base, a, b), lang)
    data["a"]["label"], data["b"]["label"] = body.label_a, body.label_b
    data["a"]["filters"], data["b"]["filters"] = body.a.normalized(), body.b.normalized()
    return _envelope(ds, base, data)
