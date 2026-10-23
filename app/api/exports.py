"""Exports of the *filtered* population (same filter semantics as every page)."""

from __future__ import annotations

import json

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import build_view, filter_params, get_dataset, request_lang
from app.database import get_db
from app.errors import AppError, not_found
from app.models import Alert, AlertRule, ConformanceRule, Dataset
from app.schemas.filters import FilterSpec
from app.services import analytics
from app.i18n import render
from app.services.conformance import evaluate_rule, explain
from app.services import ocel_export
from app.services.ocel import event_attributes
from app.services.exports import EVENT_EXPORT_COLUMNS, events_for_export, to_csv, to_json, write_xes

router = APIRouter(prefix="/api/datasets/{dataset_id}/exports", tags=["exports"])

CASE_COLUMNS = ["case_id", "start", "end", "n_events", "observed_duration_s", "status", "open_case_age_s",
                "rework", "repeated_activities", "variant_id", "variant_rank", "variant_label",
                "first_activity", "last_activity", "total_cost"]


def _download(body: str, fmt: str, name: str) -> Response:
    media = "text/csv; charset=utf-8" if fmt == "csv" else "application/json"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})


def _meta(ds: Dataset, view, kind: str) -> dict:
    return {"export": kind, "dataset_id": ds.id, "dataset_name": ds.name, "dataset_version": ds.version,
            "is_synthetic_demo": ds.is_demo, "object_type": view.log.object_type, "filters": view.spec.normalized(),
            "case_count": view.case_count, "generated_at": datetime.now(timezone.utc).isoformat(),
            "timestamps": "UTC (ISO 8601)", "durations": "seconds"}


@router.get("/events.xes", summary="Export filtered events as XES (written with PM4Py)")
def export_xes(background: BackgroundTasks, ds: Dataset = Depends(get_dataset),
               spec: FilterSpec = Depends(filter_params), db: Session = Depends(get_db)):
    _, view = build_view(db, ds, spec)
    if view.case_count == 0:
        raise AppError(422, "empty_population")
    path = write_xes(view.events)
    background.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, media_type="application/xml",
                        filename=f"processlens_{ds.id}_v{ds.version}_events.xes", background=background)


@router.get("/ocel.json", summary="Export the whole dataset as OCEL 2.0 JSON (all object types; filters do not apply)")
def export_ocel(ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    if not ds.ocel_log_id:
        raise AppError(422, "empty_population")
    body = json.dumps(ocel_export.export(db, ds), ensure_ascii=False)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="processlens_{ds.id}_v{ds.version}.jsonocel"'})


@router.get("/{kind}.{fmt}", summary="Export filtered results: cases, events, variants, transitions, "
                                     "summary, conformance (rule_id), alerts")
def export(kind: str = Path(..., pattern="^(cases|events|variants|transitions|summary|conformance|alerts)$"),
           fmt: str = Path(..., pattern="^(csv|json)$"),
           rule_id: int | None = Query(None, description="Required for kind=conformance"),
           ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
           db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    log, view = build_view(db, ds, spec)
    name = f"processlens_{ds.id}_v{ds.version}_{kind}"

    if kind == "summary":
        if fmt == "csv":
            raise AppError(422, "unsupported_format")
        return _download(to_json({**_meta(ds, view, kind), "overview": analytics.overview(view)}), fmt, name)

    if kind == "cases":
        rows = analytics.case_rows(view, 1, max(view.case_count, 1), "start", "asc")["items"]
        columns = CASE_COLUMNS
    elif kind == "events":
        attrs = event_attributes(db, ds.ocel_log_id, view.events["ocel_event_id"].tolist())
        rows = events_for_export(view.events, attrs)
        extra = sorted({k for r in rows for k in r if k.startswith("attr:")})
        columns = EVENT_EXPORT_COLUMNS + extra
    elif kind == "variants":
        items = analytics.variants(view)["items"]
        rows = [{"rank": v["rank"], "variant_id": v["variant_id"], "label": v["label"],
                 "n_activities": v["n_activities"], "case_count": v["case_count"], "share": v["share"],
                 "cumulative_share": v["cumulative_share"],
                 "median_observed_duration_s": v["observed_duration"]["median"],
                 "p90_observed_duration_s": v["observed_duration"]["p90"],
                 "has_rework": v["has_rework"], "repeated_activities": v["repeated_activities"]} for v in items]
        columns = list(rows[0].keys()) if rows else ["rank", "variant_id", "label"]
    elif kind == "transitions":
        rows = [{"source": t["source"], "target": t["target"], "occurrences": t["occurrences"],
                 "case_count": t["case_count"], "mean_elapsed_s": t["elapsed"]["mean"],
                 "median_elapsed_s": t["elapsed"]["median"], "p90_elapsed_s": t["elapsed"]["p90"]}
                for t in analytics.transitions(view, sort="median")]
        columns = ["source", "target", "occurrences", "case_count", "mean_elapsed_s", "median_elapsed_s",
                   "p90_elapsed_s"]
    elif kind == "conformance":
        if rule_id is None:
            raise AppError(422, "missing_parameter", name="rule_id")
        rule = db.get(ConformanceRule, rule_id)
        if rule is None or rule.dataset_id != ds.id:
            raise not_found("conformance_rule")
        res = evaluate_rule(log, rule).reindex(view.cases.index)
        rows = [{"case_id": cid, "rule_id": rule.id, "rule_name": rule.name, "result": r["result"],
                 "explanation": explain(r, lang), "case_status": view.cases.at[cid, "status"]}
                for cid, r in res.iterrows()]
        columns = ["case_id", "rule_id", "rule_name", "result", "explanation", "case_status"]
    else:  # alerts (restricted to cases in the filtered population)
        keep = set(view.cases.index)
        q = (select(Alert, AlertRule.name).join(AlertRule, AlertRule.id == Alert.alert_rule_id)
             .where(Alert.dataset_id == ds.id).order_by(Alert.id))
        rows = [{"alert_id": a.id, "rule_name": n, "case_id": a.case_id, "status": a.status, "severity": a.severity,
                 "condition_active": a.condition_active,
                 "message": render(lang, a.message_key, a.message_params, fallback=a.message),
                 "first_detected_at": a.first_detected_at.isoformat(), "last_evaluated_at": a.last_evaluated_at.isoformat()}
                for a, n in db.execute(q).all() if a.case_id in keep]
        columns = ["alert_id", "rule_name", "case_id", "status", "severity", "condition_active", "message",
                   "first_detected_at", "last_evaluated_at"]

    if fmt == "csv":
        return _download(to_csv(rows, columns), fmt, name)
    return _download(to_json({**_meta(ds, view, kind), "items": rows}), fmt, name)
