"""Read-only access to ProcessLens datasets: the frames PLQL formulas are evaluated on."""

from __future__ import annotations

import hashlib

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dataset, OcelLog
from app.schemas.filters import FilterSpec
from app.services import analytics
from app.services.eventlog import get_event_log
from app.services.filtering import FilteredView
from miner.plql import Frame, KnowledgeModel
from miner.plql.engine import filter_frame
from miner.store import Package


class DataError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def list_datasets(db: Session) -> list[dict]:
    out = []
    for ds in db.scalars(select(Dataset).where(Dataset.status == "ready").order_by(Dataset.id)):
        out.append({"id": ds.id, "name": ds.name, "version": ds.version, "case_count": ds.case_count,
                    "event_count": ds.event_count, "main_object_type": ds.main_object_type,
                    "object_types": object_types(db, ds), "is_demo": ds.is_demo})
    return out


def object_types(db: Session, ds: Dataset) -> list[str]:
    ocel = db.get(OcelLog, ds.ocel_log_id) if ds.ocel_log_id else None
    types = [t["name"] for t in (ocel.object_types if ocel else [])]
    return types or [ds.main_object_type]


def dataset_for(db: Session, pkg: Package) -> Dataset:
    ds = db.get(Dataset, pkg.dataset_id)
    if ds is None or ds.status != "ready":
        raise DataError(409, "dataset_unavailable",
                        f"The package's ProcessLens dataset #{pkg.dataset_id} no longer exists or is not ready. "
                        "Pick another dataset in the package settings.")
    return ds


def lead_type(db: Session, ds: Dataset, object_type: str | None) -> str:
    ot = object_type or ds.main_object_type
    if ot not in object_types(db, ds):
        raise DataError(409, "unknown_object_type", f"The dataset has no object type {ot!r}.")
    return ot


def knowledge_model(pkg: Package, override: tuple[str, str, str] | None = None) -> KnowledgeModel:
    """KPI and filter formulas by key; ``override`` = (kind, key, formula) for an unsaved edit."""
    kpis = {k.key: k.formula for k in pkg.kpis}
    filters = {f.key: f.formula for f in pkg.filters}
    if override:
        kind, key, formula = override
        (kpis if kind == "kpi" else filters)[key] = formula
    return KnowledgeModel(kpis=kpis, filters=filters)


def package_frame(db: Session, pkg: Package, km: KnowledgeModel) -> Frame:
    """All cases of the package (the package's base filter applied)."""
    ds = dataset_for(db, pkg)
    frame = Frame.of(get_event_log(db, ds, lead_type(db, ds, pkg.object_type)))
    if pkg.base_filter.strip():
        frame = filter_frame(frame, [pkg.base_filter], km)
    return frame


def schema_info(db: Session, pkg: Package) -> dict:
    """Data facts for the editors: object types, activities and value lists."""
    ds = dataset_for(db, pkg)
    ot = lead_type(db, ds, pkg.object_type)
    log = get_event_log(db, ds, ot)
    ev = log.events

    def values(col: str, limit: int = 200) -> list[str]:
        if col not in ev or ev[col].isna().all():
            return []
        return [str(v) for v in ev[col].dropna().value_counts().index[:limit]]

    return {
        "dataset": {"id": ds.id, "name": ds.name, "version": ds.version, "display_timezone": log.display_tz},
        "object_type": ot,
        "object_types": object_types(db, ds),
        "case_count": int(len(log.cases)),
        "event_count": int(len(ev)),
        "activities": values("activity"),
        "resources": values("resource"),
        "departments": values("department"),
        "has": {"cost": log.has_cost, "resource": log.has_resource, "department": log.has_department,
                "start_ts": log.has_start_ts, "status": log.completion_mode != "none"},
    }


def _view(frame: Frame, filters: list[str]) -> FilteredView:
    """A ProcessLens filtered view over the frame's cases, for the graph and variant analyses."""
    key = hashlib.sha1("\x1f".join(filters).encode()).hexdigest()[:16]
    return FilteredView(frame.log, FilterSpec(), frame.cases, frame.events, cache_id=f"miner:{key}:{len(frame.cases)}")


def process_graph(frame: Frame, filters: list[str], max_activities: int) -> dict:
    graph = analytics.process_graph(_view(frame, filters))
    nodes = graph["nodes"][:max_activities]  # sorted by occurrences
    keep = {n["id"] for n in nodes} | {graph["markers"]["start"], graph["markers"]["end"]}
    edges = [e for e in graph["edges"] if e["source"] in keep and e["target"] in keep]
    return {"nodes": nodes, "edges": edges, "markers": graph["markers"], "case_count": graph["case_count"],
            "hidden_activities": max(0, len(graph["nodes"]) - len(nodes))}


def variants(frame: Frame, filters: list[str], limit: int) -> dict:
    return analytics.variants(_view(frame, filters), limit=limit)


def duration_quantile_days(frame: Frame, q: float) -> float | None:
    s = frame.cases["duration_s"].dropna()
    return None if s.empty else float(pd.Series(s).quantile(q)) / 86400.0
