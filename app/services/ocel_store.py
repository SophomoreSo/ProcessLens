"""Writing object-centric data: the single storage path of every import (CSV, XES, OCEL JSON).

Rows are plain dicts with UTC timestamps as naive ``datetime`` values:

* events:  event_id, event_type, ts, position, attributes, start_ts, resource, department,
           cost, source_row, id_generated
* objects: object_id, object_type
* attrs:   object_id, name, value, ts, event_id (the event whose row reported the value, or None)
* e2o:     event_id, object_id, qualifier
* o2o:     source_id, target_id, qualifier
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.models import OcelE2O, OcelEvent, OcelLog, OcelO2O, OcelObject, OcelObjectAttribute
from app.services.cache import cache

CHUNK = 5000
STATUS_ATTRIBUTE = "status"


@dataclass
class OcelRows:
    events: list[dict] = field(default_factory=list)
    objects: list[dict] = field(default_factory=list)
    attrs: list[dict] = field(default_factory=list)
    e2o: list[dict] = field(default_factory=list)
    o2o: list[dict] = field(default_factory=list)
    event_types: list[dict] = field(default_factory=list)  # [{"name", "attributes": [{"name", "type"}]}]
    object_types: list[dict] = field(default_factory=list)

    def counts(self) -> dict:
        return {"events": len(self.events), "objects": len(self.objects), "e2o": len(self.e2o),
                "o2o": len(self.o2o), "event_types": len(self.event_types),
                "object_types": len(self.object_types), "object_attribute_values": len(self.attrs)}


def clear(db: Session, log_id: int) -> None:
    for model in (OcelE2O, OcelO2O, OcelObjectAttribute, OcelObject, OcelEvent):
        db.execute(delete(model).where(model.log_id == log_id))
    cache.invalidate_dataset(("ocel", log_id))


def write(db: Session, log_id: int, rows: OcelRows, progress: Callable[[int, int], None] | None = None) -> None:
    """Insert all rows of one log (the caller commits)."""
    total = max(len(rows.events), 1)

    def bulk(model, data, report=False):
        for i in range(0, len(data), CHUNK):
            db.execute(insert(model), [{"log_id": log_id, **r} for r in data[i:i + CHUNK]])
            if report and progress:
                progress(min(i + CHUNK, total), total)

    bulk(OcelEvent, rows.events, report=True)
    bulk(OcelObject, rows.objects)
    bulk(OcelObjectAttribute, rows.attrs)
    bulk(OcelE2O, rows.e2o)
    bulk(OcelO2O, rows.o2o)
    cache.invalidate_dataset(("ocel", log_id))


def apply_metadata(ocel: OcelLog, rows: OcelRows) -> None:
    ocel.event_types = rows.event_types
    ocel.object_types = rows.object_types
    ocel.counts = rows.counts()


# --------------------------------------------------------------------------- case-centric table -> OCEL
def _naive(series: pd.Series) -> list:
    s = series.dt.tz_convert("UTC").dt.tz_localize(None)
    return [None if pd.isna(v) else v.to_pydatetime() for v in s]


def _opt(values) -> list:
    return [None if v is None or (isinstance(v, float) and np.isnan(v)) else v for v in values]


def from_case_table(norm: pd.DataFrame, object_type: str, attribute_columns: list[str],
                    case_attribute_columns: list[str] | None = None) -> OcelRows:
    """Rows of a validated one-row-per-event table (``validation.validate_table``) as OCEL.

    The case column becomes objects of ``object_type``; each row is one event related to its
    case object and to the objects of its object columns (qualifier = source column). Mapped
    event ids are kept (rows without one get ``row-<source row>``). The case status and the case
    attribute columns become attribute values of the case object at that event; a value is
    recorded when it first appears or changes, so the object keeps an attribute history."""
    rows = OcelRows()
    n = len(norm)
    if not n:
        return rows
    mapped_ids = _opt(norm["event_id"].tolist())
    source_rows = norm["source_row"].astype(int).tolist()
    taken = {m for m in mapped_ids if m}
    ids = [m if m else (f"row-{r}" if f"row-{r}" not in taken else f"row-{r}-generated")
           for m, r in zip(mapped_ids, source_rows)]
    ts = _naive(norm["ts"])
    start = _naive(norm["start_ts"])
    resource = _opt(norm["resource"].tolist())
    department = _opt(norm["department"].tolist())
    cost = [None if v is None or np.isnan(v) else float(v) for v in norm["cost"].tolist()]
    status = _opt(norm["case_status"].tolist())
    cases = norm["case_id"].tolist()
    acts = norm["activity"].tolist()
    attrs = norm["attributes"].tolist()
    related = norm["objects"].tolist() if "objects" in norm else [[] for _ in range(n)]
    case_attrs = norm["case_attrs"].tolist() if "case_attrs" in norm else [None] * n
    other_objects: dict[str, str] = {}
    last_value: dict[tuple[str, str], str] = {}
    for i in range(n):
        rows.events.append({"event_id": ids[i], "event_type": acts[i], "ts": ts[i], "position": source_rows[i],
                            "attributes": attrs[i], "start_ts": start[i], "resource": resource[i],
                            "department": department[i], "cost": cost[i], "source_row": source_rows[i],
                            "id_generated": not mapped_ids[i]})
        rows.e2o.append({"event_id": ids[i], "object_id": cases[i], "qualifier": ""})
        for column, otype, oids in related[i] or []:
            for oid in oids:
                other_objects.setdefault(oid, otype)
                rows.e2o.append({"event_id": ids[i], "object_id": oid, "qualifier": column})
        if status[i]:  # every reported status is kept (completion uses the case's last one)
            rows.attrs.append({"object_id": cases[i], "name": STATUS_ATTRIBUTE, "value": str(status[i]),
                               "ts": ts[i], "event_id": ids[i]})
        for name, value in (case_attrs[i] or {}).items():
            if last_value.get((cases[i], name)) != value:
                last_value[(cases[i], name)] = value
                rows.attrs.append({"object_id": cases[i], "name": name, "value": value, "ts": ts[i],
                                   "event_id": ids[i]})
    case_ids = list(dict.fromkeys(cases))
    rows.objects = [{"object_id": c, "object_type": object_type} for c in case_ids]
    seen = set(case_ids)
    rows.objects += [{"object_id": o, "object_type": t} for o, t in other_objects.items() if o not in seen]
    rows.event_types = [{"name": a, "attributes": [{"name": c, "type": "string"} for c in attribute_columns]}
                        for a in sorted(set(acts))]
    case_attr_names = ([STATUS_ATTRIBUTE] if any(status) else []) + list(case_attribute_columns or [])
    rows.object_types = [{"name": object_type, "attributes": [{"name": a, "type": "string"} for a in case_attr_names]}]
    for otype in dict.fromkeys(other_objects.values()):
        if otype != object_type:
            rows.object_types.append({"name": otype, "attributes": []})
    return rows
