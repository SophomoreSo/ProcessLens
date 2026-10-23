"""OCEL 2.0 JSON export of a whole dataset (re-importable through the import wizard).

Events keep their attributes. The standard fields the analyses use (resource, department,
cost, start time) are added under the usual OCEL/XES keys (``org:resource``, ``org:group``,
``cost:total``, ``start_timestamp``) unless the event already carries the attribute they were
read from. Object attribute values keep their validity times; static values are written with
the time 1970-01-01T00:00:00Z, as OCEL 2.0 prescribes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dataset, OcelE2O, OcelEvent, OcelLog, OcelO2O, OcelObject, OcelObjectAttribute

STANDARD_KEYS = {"resource": ("org:resource", "string"), "department": ("org:group", "string"),
                 "cost": ("cost:total", "float"), "start_timestamp": ("start_timestamp", "time")}


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def export(db: Session, ds: Dataset) -> dict:
    ocel = db.get(OcelLog, ds.ocel_log_id)
    lid = ocel.id
    # attributes the standard fields were read from (OCEL imports) are already in the events
    source_keys = {k: v for k, v in ((ds.import_options or {}).get("ocel_request", {}).get("fields") or {}).items() if v}

    rel_by_event: dict[str, list] = defaultdict(list)
    for eid, oid, q in db.execute(select(OcelE2O.event_id, OcelE2O.object_id, OcelE2O.qualifier)
                                  .where(OcelE2O.log_id == lid).order_by(OcelE2O.id)):
        rel_by_event[eid].append({"objectId": oid, "qualifier": q or ""})
    o2o_by_object: dict[str, list] = defaultdict(list)
    for s, t_, q in db.execute(select(OcelO2O.source_id, OcelO2O.target_id, OcelO2O.qualifier)
                               .where(OcelO2O.log_id == lid).order_by(OcelO2O.id)):
        o2o_by_object[s].append({"objectId": t_, "qualifier": q or ""})
    attrs_by_object: dict[str, list] = defaultdict(list)
    for oid, name, value, ts in db.execute(
            select(OcelObjectAttribute.object_id, OcelObjectAttribute.name, OcelObjectAttribute.value,
                   OcelObjectAttribute.ts).where(OcelObjectAttribute.log_id == lid)
            .order_by(OcelObjectAttribute.ts, OcelObjectAttribute.id)):
        attrs_by_object[oid].append({"name": name, "time": _iso(ts), "value": value})

    event_types = {et["name"]: {a["name"]: a["type"] for a in et.get("attributes", [])} for et in ocel.event_types}
    events = []
    for row in db.execute(select(OcelEvent).where(OcelEvent.log_id == lid).order_by(OcelEvent.ts, OcelEvent.position)).scalars():
        attrs = dict(row.attributes or {})
        typed = {"resource": row.resource, "department": row.department, "cost": row.cost,
                 "start_timestamp": _iso(row.start_ts) if row.start_ts else None}
        for field, value in typed.items():
            key, atype = STANDARD_KEYS[field]
            if value is None or source_keys.get(field) in attrs or key in attrs:
                continue
            attrs[key] = value
            event_types.setdefault(row.event_type, {}).setdefault(key, atype)
        events.append({"id": row.event_id, "type": row.event_type, "time": _iso(row.ts),
                       "attributes": [{"name": k, "value": v} for k, v in attrs.items()],
                       "relationships": rel_by_event.get(row.event_id, [])})

    object_types = {ot["name"]: {a["name"]: a["type"] for a in ot.get("attributes", [])} for ot in ocel.object_types}
    objects = []
    for oid, otype in db.execute(select(OcelObject.object_id, OcelObject.object_type).where(OcelObject.log_id == lid)
                                 .order_by(OcelObject.id)):
        object_types.setdefault(otype, {})
        for a in attrs_by_object.get(oid, []):
            object_types[otype].setdefault(a["name"], "string")
        objects.append({"id": oid, "type": otype, "attributes": attrs_by_object.get(oid, []),
                        "relationships": o2o_by_object.get(oid, [])})

    def types(d: dict) -> list:
        return [{"name": n, "attributes": [{"name": a, "type": t_} for a, t_ in attrs.items()]} for n, attrs in d.items()]

    return {"objectTypes": types(object_types), "eventTypes": types(event_types), "objects": objects, "events": events}
