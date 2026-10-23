"""Object-centric views of a dataset: unique counts, OC-DFG, objects, relationships, OC Petri nets.

Every dataset is object-centric; these endpoints look across object types (the other analyses
use one lead object type at a time).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_dataset
from app.database import get_db
from app.errors import AppError, not_found
from app.mining import registry, runs
from app.models import Dataset, OcelLog
from app.services import ocel as svc

router = APIRouter(prefix="/api/datasets/{dataset_id}/ocel", tags=["object-centric"])


def _log(db: Session, ds: Dataset) -> OcelLog:
    o = db.get(OcelLog, ds.ocel_log_id) if ds.ocel_log_id else None
    if o is None:
        raise not_found("ocel_log")
    return o


def _types(object_type: list[str], o: OcelLog) -> list[str] | None:
    known = {t_["name"] for t_ in o.object_types}
    unknown = sorted(set(object_type) - known)
    if unknown:
        raise AppError(422, "unknown_object_type", key="ocel.err.unknown_object_type", types=", ".join(unknown))
    return sorted(set(object_type)) or None


@router.get("", summary="Object model: object/event types, unique counts, import findings")
def model(ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    o = _log(db, ds)
    return {"dataset_id": ds.id, "version": ds.version, "main_object_type": ds.main_object_type,
            "event_types": o.event_types, "object_types": o.object_types, "counts": o.counts, "findings": o.findings}


@router.get("/stats", summary="Unique event/object/relationship counts (shared events counted once)")
def stats(object_type: list[str] = Query(default=[]), ds: Dataset = Depends(get_dataset),
          db: Session = Depends(get_db)):
    o = _log(db, ds)
    types = _types(object_type, o)
    return {"dataset_id": ds.id, "version": ds.version, "object_types_filter": types, **svc.stats(svc.load(db, o), types)}


@router.get("/ocdfg", summary="Object-centric directly-follows graph")
def ocdfg(object_type: list[str] = Query(default=[]), ds: Dataset = Depends(get_dataset),
          db: Session = Depends(get_db)):
    o = _log(db, ds)
    types = _types(object_type, o)
    return {"dataset_id": ds.id, "version": ds.version, "object_types_filter": types, **svc.ocdfg(svc.load(db, o), types)}


@router.get("/type-graph", summary="Object types and how their objects are related (shared events, O2O)")
def type_graph(ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    o = _log(db, ds)
    return {"dataset_id": ds.id, "version": ds.version, **svc.type_graph(svc.load(db, o))}


@router.get("/objects", summary="Objects (paged) with event counts")
def objects(object_type: str | None = None, search: str | None = Query(None, max_length=200),
            page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
            ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    o = _log(db, ds)
    if object_type:
        _types([object_type], o)
    return svc.objects_page(svc.load(db, o), object_type, search, page, page_size)


@router.get("/objects/{object_id:path}", summary="Object timeline, attribute history and related objects")
def object_detail(object_id: str, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    o = _log(db, ds)
    detail = svc.object_detail(svc.load(db, o), db, o, object_id)
    if detail is None:
        raise not_found("object")
    return detail


@router.get("/relationship-delays", summary="Object lifetimes by number of related objects (descriptive)")
def relationship_delays(object_type: str, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    o = _log(db, ds)
    _types([object_type], o)
    return svc.relationship_delays(svc.load(db, o), object_type)


class OcpnRequest(BaseModel):
    object_types: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    timeout_s: float | None = Field(None, gt=0)
    force: bool = False


@router.post("/ocpn", summary="Discover an object-centric Petri net (background run; 202, or 200 if cached)")
def ocpn(body: OcpnRequest, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    o = _log(db, ds)
    types = _types(body.object_types, o)
    try:
        run, cached = runs.create_ocpn(db, ds, o, types, body.params, body.timeout_s, body.force)
    except registry.ParamError as exc:
        raise AppError(422, "invalid_parameters", key="run.err.invalid_parameters", details=exc.errors,
                       detail="; ".join(f"{e['param']}: {e['problem']}" for e in exc.errors)) from None
    except runs.RunRequestError as exc:
        raise AppError(422, exc.key.split(".")[-1], key=exc.key, **exc.params) from None
    return JSONResponse({"run": runs.run_out(run), "cached": cached}, status_code=200 if cached else 202)
