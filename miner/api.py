"""Miner JSON API. Errors use ProcessLens' shape: ``{"error": {"code", "message", "details"}}``."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Dataset
from miner import __version__
from miner.config import get_settings
from miner.data import (
    DataError,
    knowledge_model,
    lead_type,
    list_datasets,
    package_frame,
    process_graph,
    schema_info,
    variants,
)
from miner.plql import PlqlError, QueryItem, catalog, compile_measure, compile_row, count_matching, run_query
from miner.plql.engine import filter_frame
from miner.schemas import (
    EXCHANGE_FORMAT,
    EXCHANGE_VERSION,
    Component,
    ComponentDataIn,
    EvaluateIn,
    FilterIn,
    ImportIn,
    Item,
    KpiIn,
    PackageIn,
    PackagePatch,
    QuerySpec,
    SavedQueryIn,
    ViewIn,
)
from miner.security import csrf_token
from miner.starter import starter_content
from miner.store import FilterDef, KpiDef, Package, SavedQuery, ViewDef, get_store

router = APIRouter(prefix="/api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details}}, status_code=status)


def install_error_handlers(app) -> None:
    @app.exception_handler(ApiError)
    async def _api(_req: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(DataError)
    async def _data(_req: Request, exc: DataError):
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(PlqlError)
    async def _plql(_req: Request, exc: PlqlError):
        return _error(422, "plql_error", exc.message, {"position": exc.position})

    @app.exception_handler(RequestValidationError)
    async def _invalid(_req: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        where = ".".join(str(p) for p in first.get("loc", ())[1:])
        message = f"{where}: {first.get('msg', 'invalid value')}" if where else first.get("msg", "Invalid request")
        return _error(422, "invalid_request", message,
                      [{"loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in errors])


# --------------------------------------------------------------------------- helpers
def _package(store: Session, package_id: int) -> Package:
    pkg = store.get(Package, package_id)
    if pkg is None:
        raise ApiError(404, "not_found", f"Package #{package_id} does not exist.")
    return pkg


def _iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _kpi_json(k: KpiDef) -> dict:
    return {"id": k.id, "key": k.key, "name": k.name, "formula": k.formula, "format": k.format, "target": k.target,
            "higher_is_better": k.higher_is_better, "description": k.description}


def _filter_json(f: FilterDef) -> dict:
    return {"id": f.id, "key": f.key, "name": f.name, "formula": f.formula, "description": f.description}


def _view_json(v: ViewDef, full: bool = True) -> dict:
    out = {"id": v.id, "name": v.name, "description": v.description, "updated_at": _iso(v.updated_at),
           "component_count": len(v.components or [])}
    if full:
        out.update(filter=v.filter, components=v.components or [])
    return out


def _query_json(q: SavedQuery) -> dict:
    return {"id": q.id, "name": q.name, "spec": q.spec, "updated_at": _iso(q.updated_at)}


def _package_json(pkg: Package, db: Session, full: bool = False) -> dict:
    ds = db.get(Dataset, pkg.dataset_id)
    out = {"id": pkg.id, "name": pkg.name, "description": pkg.description, "dataset_id": pkg.dataset_id,
           "dataset_name": ds.name if ds else None, "dataset_ready": bool(ds and ds.status == "ready"),
           "object_type": pkg.object_type or (ds.main_object_type if ds else None),
           "base_filter": pkg.base_filter, "created_at": _iso(pkg.created_at), "updated_at": _iso(pkg.updated_at),
           "counts": {"kpis": len(pkg.kpis), "filters": len(pkg.filters), "views": len(pkg.views),
                      "queries": len(pkg.queries)}}
    if full:
        out.update(kpis=[_kpi_json(k) for k in pkg.kpis], filters=[_filter_json(f) for f in pkg.filters],
                   views=[_view_json(v, full=False) for v in pkg.views],
                   queries=[_query_json(q) for q in pkg.queries])
    return out


def _strings(obj) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _references(pkg: Package, kind: str, key: str, skip_id: int | None = None) -> list[str]:
    """Where ``KPI("key")`` / ``FILTER("key")`` is used in the package."""
    pattern = re.compile(rf"""\b{kind.upper()}\s*\(\s*(["']){re.escape(key)}\1\s*\)""", re.IGNORECASE)
    found = []
    for k in pkg.kpis:
        if not (kind == "kpi" and k.id == skip_id) and pattern.search(k.formula):
            found.append(f"KPI {k.name}")
    for f in pkg.filters:
        if not (kind == "filter" and f.id == skip_id) and pattern.search(f.formula):
            found.append(f"filter {f.name}")
    if pattern.search(pkg.base_filter or ""):
        found.append("package base filter")
    for v in pkg.views:
        if any(pattern.search(s) for s in _strings([v.filter, v.components])):
            found.append(f"view {v.name}")
    for q in pkg.queries:
        if any(pattern.search(s) for s in _strings(q.spec)):
            found.append(f"query {q.name}")
    return found


def _validate_kpi(pkg: Package, key: str, formula: str) -> None:
    compile_measure(formula, knowledge_model(pkg, ("kpi", key, formula)))


def _validate_filter(pkg: Package, key: str, formula: str) -> None:
    compile_row(formula, knowledge_model(pkg, ("filter", key, formula)))


KPI_REF = re.compile(r"""^\s*KPI\s*\(\s*(["'])(?P<key>[a-z][a-z0-9_]*)\1\s*\)\s*$""", re.IGNORECASE)


def _kpi_ref(pkg: Package, formula: str) -> KpiDef | None:
    m = KPI_REF.match(formula)
    if not m:
        return None
    return next((k for k in pkg.kpis if k.key == m.group("key")), None)


def resolve_format(pkg: Package, item: Item) -> str:
    if item.format != "auto":
        return item.format
    kpi = _kpi_ref(pkg, item.formula)
    if kpi is not None:
        return kpi.format if kpi.format != "auto" else resolve_format(pkg, Item(formula=kpi.formula))
    up = item.formula.strip().upper()
    if up.startswith("SHARE("):
        return "percent"
    if up.startswith(("COUNT_CASES(", "COUNT_EVENTS(", "COUNT(", "COUNT_DISTINCT(")):
        return "integer"
    return "number"


def _result_json(result, pkg: Package, items: list[Item], started: float) -> dict:
    columns = result.columns
    for col, item in zip(columns, items):
        col["format"] = resolve_format(pkg, item) if col["kind"] == "measure" else (
            item.format if item.format != "auto" else "auto")
    return {"columns": columns, "rows": result.rows, "total_groups": result.total_groups,
            "case_count": result.case_count, "total_case_count": result.total_case_count,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}


# --------------------------------------------------------------------------- meta
@router.get("/session")
def session_info(request: Request):
    return {"csrf_token": csrf_token(), "version": __version__, "processlens_url": get_settings().processlens_url}


@router.get("/reference")
def reference():
    return catalog()


@router.get("/datasets")
def datasets(db: Session = Depends(get_db)):
    return list_datasets(db)


# --------------------------------------------------------------------------- packages
@router.get("/packages")
def packages(store: Session = Depends(get_store), db: Session = Depends(get_db)):
    return [_package_json(p, db) for p in store.scalars(select(Package).order_by(Package.updated_at.desc()))]


def _add_content(store: Session, pkg: Package, content: dict) -> None:
    for i, k in enumerate(content.get("kpis", [])):
        k = KpiIn.model_validate(k)
        pkg.kpis.append(KpiDef(position=i, **k.model_dump()))
    for i, f in enumerate(content.get("filters", [])):
        f = FilterIn.model_validate(f)
        pkg.filters.append(FilterDef(position=i, **f.model_dump()))
    for i, v in enumerate(content.get("views", [])):
        v = ViewIn.model_validate(v)
        pkg.views.append(ViewDef(position=i, name=v.name, description=v.description, filter=v.filter,
                                 components=[c.model_dump() for c in v.components]))
    for q in content.get("queries", []):
        q = SavedQueryIn.model_validate(q)
        pkg.queries.append(SavedQuery(name=q.name, spec=q.spec.model_dump()))


@router.post("/packages", status_code=201)
def create_package(body: PackageIn, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    ds = db.get(Dataset, body.dataset_id)
    if ds is None or ds.status != "ready":
        raise ApiError(422, "unknown_dataset", f"ProcessLens dataset #{body.dataset_id} does not exist or is not ready.")
    ot = lead_type(db, ds, body.object_type)
    pkg = Package(name=body.name, description=body.description, dataset_id=ds.id,
                  object_type=None if ot == ds.main_object_type else ot, base_filter="")
    store.add(pkg)
    if body.starter_content:
        frame = package_frame(db, pkg, knowledge_model(pkg))
        _add_content(store, pkg, starter_content(frame))
    store.commit()
    return _package_json(pkg, db, full=True)


@router.get("/packages/{package_id}")
def get_package(package_id: int, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    return _package_json(_package(store, package_id), db, full=True)


@router.patch("/packages/{package_id}")
def patch_package(package_id: int, body: PackagePatch, store: Session = Depends(get_store),
                  db: Session = Depends(get_db)):
    pkg = _package(store, package_id)
    data = body.model_dump(exclude_unset=True)
    if "dataset_id" in data or "object_type" in data:
        ds = db.get(Dataset, data.get("dataset_id", pkg.dataset_id))
        if ds is None or ds.status != "ready":
            raise ApiError(422, "unknown_dataset", "That ProcessLens dataset does not exist or is not ready.")
        ot = lead_type(db, ds, data.get("object_type", pkg.object_type if "dataset_id" not in data else None))
        pkg.dataset_id = ds.id
        pkg.object_type = None if ot == ds.main_object_type else ot
    if "base_filter" in data:
        formula = (data["base_filter"] or "").strip()
        if formula:
            compile_row(formula, knowledge_model(pkg))
        pkg.base_filter = formula
    for field in ("name", "description"):
        if data.get(field) is not None:
            setattr(pkg, field, data[field])
    store.commit()
    return _package_json(pkg, db, full=True)


@router.delete("/packages/{package_id}", status_code=204)
def delete_package(package_id: int, store: Session = Depends(get_store)):
    store.delete(_package(store, package_id))
    store.commit()
    return Response(status_code=204)


@router.get("/packages/{package_id}/export")
def export_package(package_id: int, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    pkg = _package(store, package_id)
    ds = db.get(Dataset, pkg.dataset_id)
    doc = {
        "format": EXCHANGE_FORMAT, "version": EXCHANGE_VERSION, "miner_version": __version__,
        "package": {"name": pkg.name, "description": pkg.description, "object_type": pkg.object_type,
                    "base_filter": pkg.base_filter,
                    "dataset": {"id": pkg.dataset_id, "name": ds.name if ds else None}},
        "kpis": [{k: v for k, v in _kpi_json(x).items() if k != "id"} for x in pkg.kpis],
        "filters": [{k: v for k, v in _filter_json(x).items() if k != "id"} for x in pkg.filters],
        "views": [{"name": v.name, "description": v.description, "filter": v.filter, "components": v.components}
                  for v in pkg.views],
        "queries": [{"name": q.name, "spec": q.spec} for q in pkg.queries],
    }
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", pkg.name).strip("-")[:60] or "package"
    return JSONResponse(doc, headers={"Content-Disposition": f'attachment; filename="{slug}.miner.json"'})


@router.post("/packages/import", status_code=201)
def import_package(body: ImportIn, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    doc = body.document
    if doc.get("format") != EXCHANGE_FORMAT or doc.get("version") != EXCHANGE_VERSION:
        raise ApiError(422, "invalid_package", f"Not a ProcessLens Miner package (format {EXCHANGE_FORMAT!r}, "
                                               f"version {EXCHANGE_VERSION}).")
    meta = doc.get("package") or {}
    dataset_id = body.dataset_id or (meta.get("dataset") or {}).get("id")
    ds = db.get(Dataset, dataset_id) if isinstance(dataset_id, int) else None
    if ds is None or ds.status != "ready":
        raise ApiError(422, "unknown_dataset", "Choose a ProcessLens dataset for the imported package.")
    ot = lead_type(db, ds, body.object_type or meta.get("object_type"))
    pkg = Package(name=str(meta.get("name") or "Imported package")[:200],
                  description=str(meta.get("description") or "")[:4000], dataset_id=ds.id,
                  object_type=None if ot == ds.main_object_type else ot,
                  base_filter=str(meta.get("base_filter") or "")[:4000])
    store.add(pkg)
    try:
        _add_content(store, pkg, doc)
    except ValueError as exc:
        store.rollback()
        raise ApiError(422, "invalid_package", f"The package file is not valid: {exc}") from None
    keys = [k.key for k in pkg.kpis]
    fkeys = [f.key for f in pkg.filters]
    if len(set(keys)) != len(keys) or len(set(fkeys)) != len(fkeys):
        store.rollback()
        raise ApiError(422, "invalid_package", "The package file contains duplicate KPI or filter keys.")
    store.commit()
    return _package_json(pkg, db, full=True)


@router.get("/packages/{package_id}/schema")
def package_schema(package_id: int, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    return schema_info(db, _package(store, package_id))


# --------------------------------------------------------------------------- knowledge model
def _next_position(items) -> int:
    return max((i.position for i in items), default=-1) + 1


@router.post("/packages/{package_id}/kpis", status_code=201)
def create_kpi(package_id: int, body: KpiIn, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    if any(k.key == body.key for k in pkg.kpis):
        raise ApiError(409, "duplicate_key", f"A KPI with the key {body.key!r} already exists.")
    _validate_kpi(pkg, body.key, body.formula)
    kpi = KpiDef(position=_next_position(pkg.kpis), **body.model_dump())
    pkg.kpis.append(kpi)
    store.commit()
    return _kpi_json(kpi)


def _kpi(pkg: Package, kpi_id: int) -> KpiDef:
    kpi = next((k for k in pkg.kpis if k.id == kpi_id), None)
    if kpi is None:
        raise ApiError(404, "not_found", f"KPI #{kpi_id} does not exist in this package.")
    return kpi


@router.put("/packages/{package_id}/kpis/{kpi_id}")
def update_kpi(package_id: int, kpi_id: int, body: KpiIn, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    kpi = _kpi(pkg, kpi_id)
    if body.key != kpi.key:
        if any(k.key == body.key for k in pkg.kpis):
            raise ApiError(409, "duplicate_key", f"A KPI with the key {body.key!r} already exists.")
        refs = _references(pkg, "kpi", kpi.key, skip_id=kpi.id)
        if refs:
            raise ApiError(409, "in_use", f"The key cannot change while KPI({kpi.key!r}) is used in: "
                                          f"{', '.join(refs)}.", refs)
    _validate_kpi(pkg, body.key, body.formula)
    for k, v in body.model_dump().items():
        setattr(kpi, k, v)
    store.commit()
    return _kpi_json(kpi)


@router.delete("/packages/{package_id}/kpis/{kpi_id}", status_code=204)
def delete_kpi(package_id: int, kpi_id: int, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    kpi = _kpi(pkg, kpi_id)
    refs = _references(pkg, "kpi", kpi.key, skip_id=kpi.id)
    if refs:
        raise ApiError(409, "in_use", f"KPI({kpi.key!r}) is used in: {', '.join(refs)}.", refs)
    pkg.kpis.remove(kpi)
    store.commit()
    return Response(status_code=204)


@router.post("/packages/{package_id}/filters", status_code=201)
def create_filter(package_id: int, body: FilterIn, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    if any(f.key == body.key for f in pkg.filters):
        raise ApiError(409, "duplicate_key", f"A filter with the key {body.key!r} already exists.")
    _validate_filter(pkg, body.key, body.formula)
    f = FilterDef(position=_next_position(pkg.filters), **body.model_dump())
    pkg.filters.append(f)
    store.commit()
    return _filter_json(f)


def _filter(pkg: Package, filter_id: int) -> FilterDef:
    f = next((x for x in pkg.filters if x.id == filter_id), None)
    if f is None:
        raise ApiError(404, "not_found", f"Filter #{filter_id} does not exist in this package.")
    return f


@router.put("/packages/{package_id}/filters/{filter_id}")
def update_filter(package_id: int, filter_id: int, body: FilterIn, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    f = _filter(pkg, filter_id)
    if body.key != f.key:
        if any(x.key == body.key for x in pkg.filters):
            raise ApiError(409, "duplicate_key", f"A filter with the key {body.key!r} already exists.")
        refs = _references(pkg, "filter", f.key, skip_id=f.id)
        if refs:
            raise ApiError(409, "in_use", f"The key cannot change while FILTER({f.key!r}) is used in: "
                                          f"{', '.join(refs)}.", refs)
    _validate_filter(pkg, body.key, body.formula)
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    store.commit()
    return _filter_json(f)


@router.delete("/packages/{package_id}/filters/{filter_id}", status_code=204)
def delete_filter(package_id: int, filter_id: int, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    f = _filter(pkg, filter_id)
    refs = _references(pkg, "filter", f.key, skip_id=f.id)
    if refs:
        raise ApiError(409, "in_use", f"FILTER({f.key!r}) is used in: {', '.join(refs)}.", refs)
    pkg.filters.remove(f)
    store.commit()
    return Response(status_code=204)


@router.get("/packages/{package_id}/kpi-values")
def kpi_values(package_id: int, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    """Every KPI over all cases of the package (errors are reported per KPI)."""
    pkg = _package(store, package_id)
    km = knowledge_model(pkg)
    frame = package_frame(db, pkg, km)
    out = {}
    for k in pkg.kpis:
        try:
            result = run_query(frame, [], [QueryItem(k.formula)], km)
            out[k.key] = {"value": result.rows[0][0], "format": resolve_format(pkg, Item(formula=k.formula,
                                                                                             format=k.format))}
        except PlqlError as exc:
            out[k.key] = {"error": exc.message, "position": exc.position}
    return {"case_count": len(frame.cases), "values": out}


@router.get("/packages/{package_id}/filter-counts")
def filter_counts(package_id: int, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    pkg = _package(store, package_id)
    km = knowledge_model(pkg)
    frame = package_frame(db, pkg, km)
    out = {}
    for f in pkg.filters:
        try:
            matching, total = count_matching(frame, f.formula, km)
            out[f.key] = {"matching": matching, "total": total}
        except PlqlError as exc:
            out[f.key] = {"error": exc.message, "position": exc.position}
    return {"case_count": len(frame.cases), "counts": out}


@router.post("/packages/{package_id}/evaluate")
def evaluate(package_id: int, body: EvaluateIn, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    """Test a formula: a measure's value over all cases, or how many cases a condition keeps."""
    pkg = _package(store, package_id)
    started = time.perf_counter()
    override = (("kpi" if body.kind == "measure" else "filter"), body.key, body.formula) if body.key else None
    km = knowledge_model(pkg, override)
    frame = package_frame(db, pkg, km)
    if body.kind == "measure":
        compile_measure(body.formula, km)
        result = run_query(frame, [], [QueryItem(body.formula)], km)
        value = result.rows[0][0]
        return {"kind": "measure", "value": value, "type": result.columns[0]["type"],
                "case_count": len(frame.cases), "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
    matching, total = count_matching(frame, body.formula, km)
    return {"kind": "condition", "matching": matching, "total": total,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}


# --------------------------------------------------------------------------- queries
def _order(spec: QuerySpec):
    return [(int(c), bool(d)) for c, d in spec.order_by] if spec.order_by is not None else None


@router.post("/packages/{package_id}/query")
def query(package_id: int, spec: QuerySpec, store: Session = Depends(get_store), db: Session = Depends(get_db)):
    pkg = _package(store, package_id)
    started = time.perf_counter()
    km = knowledge_model(pkg)
    frame = package_frame(db, pkg, km)
    result = run_query(frame, [QueryItem(d.formula, d.label) for d in spec.dimensions],
                       [QueryItem(m.formula, m.label) for m in spec.measures], km,
                       [f for f in spec.filters if f.strip()], _order(spec), spec.limit)
    return _result_json(result, pkg, [*spec.dimensions, *spec.measures], started)


@router.post("/packages/{package_id}/queries", status_code=201)
def create_query(package_id: int, body: SavedQueryIn, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    q = SavedQuery(name=body.name, spec=body.spec.model_dump())
    pkg.queries.append(q)
    store.commit()
    return _query_json(q)


def _saved_query(pkg: Package, query_id: int) -> SavedQuery:
    q = next((x for x in pkg.queries if x.id == query_id), None)
    if q is None:
        raise ApiError(404, "not_found", f"Query #{query_id} does not exist in this package.")
    return q


@router.put("/packages/{package_id}/queries/{query_id}")
def update_query(package_id: int, query_id: int, body: SavedQueryIn, store: Session = Depends(get_store)):
    q = _saved_query(_package(store, package_id), query_id)
    q.name, q.spec = body.name, body.spec.model_dump()
    store.commit()
    return _query_json(q)


@router.delete("/packages/{package_id}/queries/{query_id}", status_code=204)
def delete_query(package_id: int, query_id: int, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    pkg.queries.remove(_saved_query(pkg, query_id))
    store.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- views
def _view(pkg: Package, view_id: int) -> ViewDef:
    v = next((x for x in pkg.views if x.id == view_id), None)
    if v is None:
        raise ApiError(404, "not_found", f"View #{view_id} does not exist in this package.")
    return v


def _check_view(body: ViewIn) -> None:
    ids = [c.id for c in body.components]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "invalid_request", "Component ids must be unique within a view.")


@router.post("/packages/{package_id}/views", status_code=201)
def create_view(package_id: int, body: ViewIn, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    _check_view(body)
    v = ViewDef(name=body.name, description=body.description, filter=body.filter,
                components=[c.model_dump() for c in body.components], position=_next_position(pkg.views))
    pkg.views.append(v)
    store.commit()
    return _view_json(v)


@router.get("/packages/{package_id}/views/{view_id}")
def get_view(package_id: int, view_id: int, store: Session = Depends(get_store)):
    return _view_json(_view(_package(store, package_id), view_id))


@router.put("/packages/{package_id}/views/{view_id}")
def update_view(package_id: int, view_id: int, body: ViewIn, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    v = _view(pkg, view_id)
    _check_view(body)
    v.name, v.description, v.filter = body.name, body.description, body.filter
    v.components = [c.model_dump() for c in body.components]
    store.commit()
    return _view_json(v)


@router.post("/packages/{package_id}/views/{view_id}/duplicate", status_code=201)
def duplicate_view(package_id: int, view_id: int, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    v = _view(pkg, view_id)
    copy = ViewDef(name=f"{v.name} (copy)"[:200], description=v.description, filter=v.filter,
                   components=list(v.components or []), position=_next_position(pkg.views))
    pkg.views.append(copy)
    store.commit()
    return _view_json(copy)


@router.delete("/packages/{package_id}/views/{view_id}", status_code=204)
def delete_view(package_id: int, view_id: int, store: Session = Depends(get_store)):
    pkg = _package(store, package_id)
    pkg.views.remove(_view(pkg, view_id))
    store.commit()
    return Response(status_code=204)


def _items(raw, kind: str) -> list[Item]:
    try:
        return [Item.model_validate(x) for x in (raw or [])]
    except ValueError:
        raise ApiError(422, "invalid_component", f"The component's {kind} are not valid.") from None


@router.post("/packages/{package_id}/data")
def component_data(package_id: int, body: ComponentDataIn, store: Session = Depends(get_store),
                   db: Session = Depends(get_db)):
    """The data one view component shows, under the view's filters and selections (PLQL conditions)."""
    pkg = _package(store, package_id)
    comp: Component = body.component
    cfg = comp.config or {}
    started = time.perf_counter()
    if comp.type == "text":
        return {"type": "text"}
    km = knowledge_model(pkg)
    frame = package_frame(db, pkg, km)
    filters = [f for f in [*body.filters, comp.filter] if f and f.strip()]

    if comp.type == "kpi":
        measure = _items([cfg.get("measure")], "measure")[0] if cfg.get("measure") else None
        if measure is None:
            raise ApiError(422, "invalid_component", "Choose a KPI or write a measure for this tile.")
        result = run_query(frame, [], [QueryItem(measure.formula)], km, filters)
        kpi = _kpi_ref(pkg, measure.formula)
        return {"type": "kpi", "value": result.rows[0][0], "format": resolve_format(pkg, measure),
                "name": measure.label or (kpi.name if kpi else measure.formula),
                "target": kpi.target if kpi else None, "higher_is_better": kpi.higher_is_better if kpi else True,
                "description": kpi.description if kpi else "",
                "case_count": result.case_count, "total_case_count": result.total_case_count,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}

    if comp.type in ("bar", "line", "pie", "table"):
        if comp.type == "table":
            dims = _items(cfg.get("dimensions"), "dimensions")
        else:
            dims = _items([cfg["dimension"]], "dimension") if cfg.get("dimension") else []
            if not dims:
                raise ApiError(422, "invalid_component", "Choose a dimension for this chart.")
        measures = _items(cfg.get("measures"), "measures")
        if not measures and comp.type != "table":
            raise ApiError(422, "invalid_component", "Add at least one measure.")
        order = {"dimension": [(0, False)], "measure": [(len(dims), True)]}.get(cfg.get("order", "auto"))
        limit = max(1, min(int(cfg.get("limit") or (100 if comp.type == "table" else 20)), 5000))
        result = run_query(frame, [QueryItem(d.formula, d.label) for d in dims],
                           [QueryItem(m.formula, m.label) for m in measures], km, filters, order, limit)
        out = _result_json(result, pkg, [*dims, *measures], started)
        out["type"] = comp.type
        return out

    filtered = filter_frame(frame, filters, km)
    if comp.type == "process":
        n = max(2, min(int(cfg.get("max_activities") or 15), 60))
        out = process_graph(filtered, filters, n)
    else:
        n = max(1, min(int(cfg.get("limit") or 10), 100))
        out = variants(filtered, filters, n)
    out.update(type=comp.type, total_case_count=len(frame.cases),
               elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
    return out
