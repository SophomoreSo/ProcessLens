"""Object-centric event logs (OCEL 2.0 JSON): validation, persistence and analysis.

Supported constructs (anything else is reported, never silently accepted):
  * top level: ``objectTypes``, ``eventTypes``, ``objects``, ``events``
  * type definitions with typed attributes (string, time, integer, float, boolean)
  * events: id, type, time, attributes, relationships (objectId + qualifier)
  * objects: id, type, attributes with ``time`` (changing values over time),
    relationships to other objects (objectId + qualifier)

Counting rules: every event is stored and counted once even when it relates to
many objects; event-object relationships, object-object relationships and
object-type-specific transitions are counted separately. Numeric event
attributes such as cost are summed over unique events only.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections import defaultdict
import math
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import OcelE2O, OcelEvent, OcelLog, OcelO2O, OcelObject, OcelObjectAttribute
from app.services.cache import cache

log = logging.getLogger("processlens.ocel")

SUPPORTED_TOP = {"objectTypes", "eventTypes", "objects", "events"}
SUPPORTED_ATTR_TYPES = {"string", "time", "integer", "float", "boolean"}
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
MAX_FINDINGS = 500


class OcelInvalid(Exception):
    def __init__(self, findings: list[dict]):
        super().__init__("invalid OCEL")
        self.findings = findings


@dataclass
class Parsed:
    event_types: list[dict]
    object_types: list[dict]
    events: list[dict]  # {event_id, event_type, ts, position, attributes}
    objects: list[dict]  # {object_id, object_type}
    object_attrs: list[dict]  # {object_id, name, value, ts}
    e2o: list[dict]
    o2o: list[dict]
    findings: list[dict]


def _finding(findings: list, severity: str, code: str, element: str | None = None, **params) -> None:
    if len(findings) < MAX_FINDINGS or severity == "error":
        findings.append({"severity": severity, "code": code, "key": f"ocel.finding.{code}", "element": element,
                         "params": params})


def _parse_time(value) -> pd.Timestamp | None:
    if not isinstance(value, str) or not value.strip():
        return None
    ts = pd.to_datetime(value, format="ISO8601", utc=True, errors="coerce")
    return None if pd.isna(ts) else ts


def _naive(value: str) -> bool:
    v = value.strip()
    return not (v.endswith("Z") or v.endswith("z") or "+" in v[10:] or (len(v) > 19 and "-" in v[19:]))


def _check_value(atype: str, value) -> bool:
    if value is None:
        return True
    if atype == "string":
        return isinstance(value, str)
    if atype == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, str) and value.strip().lstrip("-").isdigit())
    if atype == "float":
        try:
            float(value)
            return not isinstance(value, bool)
        except (TypeError, ValueError):
            return False
    if atype == "boolean":
        return isinstance(value, bool) or (isinstance(value, str) and value.lower() in ("true", "false"))
    if atype == "time":
        return _parse_time(value) is not None
    return True


def _types(raw, kind: str, findings: list) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, list):
        _finding(findings, "error", "not_a_list", kind)
        return out
    for i, t in enumerate(raw):
        if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"]:
            _finding(findings, "error", "type_without_name", f"{kind}[{i}]")
            continue
        if t["name"] in out:
            _finding(findings, "error", "duplicate_type", f"{kind}[{i}]", name=t["name"])
            continue
        attrs = {}
        for a in t.get("attributes") or []:
            if not isinstance(a, dict) or not a.get("name"):
                _finding(findings, "warning", "attribute_without_name", t["name"])
                continue
            atype = str(a.get("type", "string"))
            if atype not in SUPPORTED_ATTR_TYPES:
                _finding(findings, "warning", "unsupported_attribute_type", t["name"], attribute=a["name"], type=atype)
                atype = "string"
            attrs[a["name"]] = atype
        out[t["name"]] = attrs
        for extra in sorted(set(t) - {"name", "attributes"}):
            _finding(findings, "warning", "ignored_key", f"{kind}:{t['name']}", key=extra)
    return out


def parse(content: bytes) -> Parsed:
    """Parse and validate OCEL 2.0 JSON. Raises OcelInvalid with findings when errors are found."""
    findings: list[dict] = []
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OcelInvalid([{"severity": "error", "code": "not_json", "key": "ocel.finding.not_json", "element": None,
                            "params": {"detail": str(exc)[:200]}}]) from None
    if not isinstance(data, dict):
        raise OcelInvalid([{"severity": "error", "code": "not_object", "key": "ocel.finding.not_object", "element": None, "params": {}}])
    for key in sorted(SUPPORTED_TOP - set(data)):
        _finding(findings, "error", "missing_key", None, key=key)
    for key in sorted(set(data) - SUPPORTED_TOP):
        _finding(findings, "warning", "unsupported_top_level", None, key=key)
    if any(f["severity"] == "error" for f in findings):
        raise OcelInvalid(findings)

    etypes = _types(data["eventTypes"], "eventTypes", findings)
    otypes = _types(data["objectTypes"], "objectTypes", findings)

    objects, object_attrs, o2o_raw = [], [], []
    object_type_of: dict[str, str] = {}
    if not isinstance(data["objects"], list):
        _finding(findings, "error", "not_a_list", "objects")
    else:
        for i, o in enumerate(data["objects"]):
            el = f"objects[{i}]"
            if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not o["id"]:
                _finding(findings, "error", "object_without_id", el)
                continue
            oid = o["id"]
            if oid in object_type_of:
                _finding(findings, "error", "duplicate_object_id", el, id=oid)
                continue
            otype = o.get("type")
            if otype not in otypes:
                _finding(findings, "error", "undeclared_object_type", el, id=oid, type=str(otype))
                continue
            object_type_of[oid] = otype
            objects.append({"object_id": oid, "object_type": otype})
            for a in o.get("attributes") or []:
                name = a.get("name") if isinstance(a, dict) else None
                if not name:
                    _finding(findings, "warning", "attribute_without_name", oid)
                    continue
                if name not in otypes[otype]:
                    _finding(findings, "warning", "undeclared_attribute", oid, attribute=name)
                atype = otypes[otype].get(name, "string")
                if not _check_value(atype, a.get("value")):
                    _finding(findings, "warning", "attribute_value_type", oid, attribute=name, type=atype)
                ts = _parse_time(a.get("time")) if a.get("time") is not None else EPOCH
                if ts is None:
                    _finding(findings, "error", "invalid_time", oid, value=str(a.get("time"))[:60])
                    continue
                object_attrs.append({"object_id": oid, "name": name,
                                     "value": None if a.get("value") is None else str(a.get("value")), "ts": ts})
            for r in o.get("relationships") or []:
                if not isinstance(r, dict) or not r.get("objectId"):
                    _finding(findings, "error", "relationship_without_target", oid)
                    continue
                o2o_raw.append({"source_id": oid, "target_id": r["objectId"], "qualifier": str(r.get("qualifier") or "")})
            for extra in sorted(set(o) - {"id", "type", "attributes", "relationships"}):
                _finding(findings, "warning", "ignored_key", oid, key=extra)
    o2o = []
    for r in o2o_raw:
        if r["target_id"] not in object_type_of:
            _finding(findings, "error", "dangling_o2o", r["source_id"], target=r["target_id"])
        else:
            o2o.append(r)

    events, e2o = [], []
    seen_events: set[str] = set()
    naive_times = 0
    if not isinstance(data["events"], list):
        _finding(findings, "error", "not_a_list", "events")
    else:
        for i, e in enumerate(data["events"]):
            el = f"events[{i}]"
            if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not e["id"]:
                _finding(findings, "error", "event_without_id", el)
                continue
            eid = e["id"]
            if eid in seen_events:
                _finding(findings, "error", "duplicate_event_id", el, id=eid)
                continue
            seen_events.add(eid)
            etype = e.get("type")
            if etype not in etypes:
                _finding(findings, "error", "undeclared_event_type", el, id=eid, type=str(etype))
                continue
            ts = _parse_time(e.get("time"))
            if ts is None:
                _finding(findings, "error", "invalid_time", eid, value=str(e.get("time"))[:60])
                continue
            if _naive(e["time"]):
                naive_times += 1
            attrs = {}
            for a in e.get("attributes") or []:
                name = a.get("name") if isinstance(a, dict) else None
                if not name:
                    _finding(findings, "warning", "attribute_without_name", eid)
                    continue
                if name not in etypes[etype]:
                    _finding(findings, "warning", "undeclared_attribute", eid, attribute=name)
                atype = etypes[etype].get(name, "string")
                if not _check_value(atype, a.get("value")):
                    _finding(findings, "warning", "attribute_value_type", eid, attribute=name, type=atype)
                attrs[name] = a.get("value")
            events.append({"event_id": eid, "event_type": etype, "ts": ts, "position": i, "attributes": attrs or None})
            seen_rel = set()
            for r in e.get("relationships") or []:
                if not isinstance(r, dict) or not r.get("objectId"):
                    _finding(findings, "error", "relationship_without_target", eid)
                    continue
                if r["objectId"] not in object_type_of:
                    _finding(findings, "error", "dangling_e2o", eid, target=r["objectId"])
                    continue
                key = (r["objectId"], str(r.get("qualifier") or ""))
                if key in seen_rel:
                    _finding(findings, "warning", "duplicate_relationship", eid, target=r["objectId"])
                    continue
                seen_rel.add(key)
                e2o.append({"event_id": eid, "object_id": r["objectId"], "qualifier": key[1]})
            for extra in sorted(set(e) - {"id", "type", "time", "attributes", "relationships"}):
                _finding(findings, "warning", "ignored_key", eid, key=extra)
    if naive_times:
        _finding(findings, "warning", "naive_times_utc", None, count=naive_times)
    if not events:
        _finding(findings, "error", "no_events", None)
    if any(f["severity"] == "error" for f in findings):
        raise OcelInvalid(findings)
    related = {r["object_id"] for r in e2o}
    unrelated = sum(1 for o in objects if o["object_id"] not in related)
    if unrelated:
        _finding(findings, "info", "objects_without_events", None, count=unrelated)
    to_list = lambda types: [{"name": n, "attributes": [{"name": a, "type": t} for a, t in attrs.items()]}
                             for n, attrs in types.items()]
    return Parsed(to_list(etypes), to_list(otypes), events, objects, object_attrs, e2o, o2o, findings)


def _naive_utc(ts: pd.Timestamp):
    return ts.tz_convert("UTC").tz_localize(None).to_pydatetime()


def store(db: Session, parsed: Parsed, name: str, filename: str, raw: bytes, description: str | None = None,
          is_demo: bool = False) -> OcelLog:
    counts = {
        "events": len(parsed.events), "objects": len(parsed.objects), "e2o": len(parsed.e2o), "o2o": len(parsed.o2o),
        "event_types": len(parsed.event_types), "object_types": len(parsed.object_types),
        "object_attribute_values": len(parsed.object_attrs),
    }
    ocel = OcelLog(name=name, description=description, is_demo=is_demo, source_filename=filename,
                   source_sha256=hashlib.sha256(raw).hexdigest(), source_size_bytes=len(raw),
                   event_types=parsed.event_types, object_types=parsed.object_types, counts=counts,
                   findings=parsed.findings)
    db.add(ocel)
    db.flush()
    chunk = 5000

    def bulk(model, rows):
        for i in range(0, len(rows), chunk):
            db.execute(insert(model), rows[i:i + chunk])

    bulk(OcelEvent, [{"log_id": ocel.id, **{**e, "ts": _naive_utc(e["ts"])}} for e in parsed.events])
    bulk(OcelObject, [{"log_id": ocel.id, **o} for o in parsed.objects])
    bulk(OcelObjectAttribute, [{"log_id": ocel.id, **{**a, "ts": _naive_utc(a["ts"])}} for a in parsed.object_attrs])
    bulk(OcelE2O, [{"log_id": ocel.id, **r} for r in parsed.e2o])
    bulk(OcelO2O, [{"log_id": ocel.id, **r} for r in parsed.o2o])
    db.commit()
    log.info("Imported OCEL %s: %s", ocel.id, counts)
    return ocel


def delete_log(db: Session, ocel: OcelLog) -> None:
    for model in (OcelE2O, OcelO2O, OcelObjectAttribute, OcelObject, OcelEvent):
        db.execute(delete(model).where(model.log_id == ocel.id))
    db.delete(ocel)
    db.commit()
    cache.invalidate_dataset(("ocel", ocel.id))


def _chunks(ids: list, size: int = 900):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def event_attributes(db: Session, log_id: int | None, event_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for part in _chunks(list(dict.fromkeys(event_ids))):
        out.update({e: a or {} for e, a in db.execute(select(OcelEvent.event_id, OcelEvent.attributes).where(
            OcelEvent.log_id == log_id, OcelEvent.event_id.in_(part))).all()})
    return out


def event_objects(db: Session, log_id: int | None, event_ids: list[str], exclude: str | None = None) -> dict[str, list]:
    """Objects related to each event (except ``exclude``): the object-centric context of a timeline."""
    out: dict[str, list] = defaultdict(list)
    for part in _chunks(list(dict.fromkeys(event_ids))):
        stmt = (select(OcelE2O.event_id, OcelE2O.object_id, OcelObject.object_type, OcelE2O.qualifier)
                .join(OcelObject, (OcelObject.log_id == OcelE2O.log_id) & (OcelObject.object_id == OcelE2O.object_id))
                .where(OcelE2O.log_id == log_id, OcelE2O.event_id.in_(part)).order_by(OcelE2O.id))
        for eid, oid, otype, q in db.execute(stmt).all():
            if oid != exclude:
                out[eid].append({"object_id": oid, "object_type": otype, "qualifier": q})
    return dict(out)


# =========================================================================== analysis
@dataclass
class OcelData:
    log_id: int
    events: pd.DataFrame  # event_id, event_type, ts, position, attributes
    objects: pd.DataFrame  # object_id, object_type
    e2o: pd.DataFrame  # event_id, object_id, qualifier, object_type, event_type, ts, position
    o2o: pd.DataFrame  # source_id, target_id, qualifier, source_type, target_type


def load(db: Session, ocel: OcelLog) -> OcelData:
    def compute() -> OcelData:
        ev = pd.DataFrame(db.execute(select(OcelEvent.event_id, OcelEvent.event_type, OcelEvent.ts, OcelEvent.position,
                                            OcelEvent.attributes).where(OcelEvent.log_id == ocel.id)).all(),
                          columns=["event_id", "event_type", "ts", "position", "attributes"])
        ev["ts"] = pd.to_datetime(ev["ts"]).dt.tz_localize("UTC")
        ev = ev.sort_values(["ts", "position"], kind="mergesort").reset_index(drop=True)
        ob = pd.DataFrame(db.execute(select(OcelObject.object_id, OcelObject.object_type).where(OcelObject.log_id == ocel.id)).all(),
                          columns=["object_id", "object_type"])
        rel = pd.DataFrame(db.execute(select(OcelE2O.event_id, OcelE2O.object_id, OcelE2O.qualifier).where(OcelE2O.log_id == ocel.id)).all(),
                           columns=["event_id", "object_id", "qualifier"])
        rel = rel.merge(ob, on="object_id", how="left").merge(ev[["event_id", "event_type", "ts", "position"]], on="event_id", how="left")
        rel = rel.sort_values(["object_id", "ts", "position"], kind="mergesort").reset_index(drop=True)
        o2o = pd.DataFrame(db.execute(select(OcelO2O.source_id, OcelO2O.target_id, OcelO2O.qualifier).where(OcelO2O.log_id == ocel.id)).all(),
                           columns=["source_id", "target_id", "qualifier"])
        types = dict(zip(ob["object_id"], ob["object_type"]))
        o2o["source_type"] = o2o["source_id"].map(types)
        o2o["target_type"] = o2o["target_id"].map(types)
        return OcelData(ocel.id, ev, ob, rel, o2o)

    return cache.get_or_compute((("ocel", ocel.id), ocel.version, "data"), compute)


def _restrict(data: OcelData, types: list[str] | None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Events, objects and relationships restricted to the selected object types.

    An event is kept when it relates to at least one object of a selected type;
    it remains a single event (not one per object)."""
    if not types:
        return data.events, data.objects, data.e2o
    ob = data.objects[data.objects["object_type"].isin(types)]
    rel = data.e2o[data.e2o["object_type"].isin(types)]
    ev = data.events[data.events["event_id"].isin(set(rel["event_id"]))]
    return ev, ob, rel


def _numeric_attribute_total(ev: pd.DataFrame, name: str) -> float | None:
    vals = []
    for attrs in ev["attributes"]:
        if attrs and name in attrs:
            try:
                vals.append(float(attrs[name]))
            except (TypeError, ValueError):
                pass
    return round(math.fsum(vals), 9) if vals else None


def stats(data: OcelData, types: list[str] | None = None) -> dict:
    ev, ob, rel = _restrict(data, types)
    per_event = rel.groupby("event_id")["object_id"].nunique()
    shared = int((per_event > 1).sum())
    per_type = []
    for otype, g in rel.groupby("object_type"):
        objs = int(ob[ob["object_type"] == otype].shape[0])
        evs_per_obj = g.groupby("object_id")["event_id"].nunique()
        multi = g.groupby("event_id")["object_id"].nunique()
        per_type.append({
            "object_type": otype, "objects": objs, "unique_events": int(g["event_id"].nunique()),
            "e2o_relationships": int(len(g)), "events_with_several_objects_of_type": int((multi > 1).sum()),
            "median_events_per_object": float(evs_per_obj.median()) if len(evs_per_obj) else None,
        })
    per_event_type = []
    for etype, g in ev.groupby("event_type"):
        r = rel[rel["event_type"] == etype]
        per_event_type.append({"event_type": etype, "unique_events": int(len(g)), "e2o_relationships": int(len(r)),
                               "object_types": sorted(r["object_type"].dropna().unique().tolist())})
    o2o = data.o2o
    if types:
        o2o = o2o[o2o["source_type"].isin(types) | o2o["target_type"].isin(types)]
    return {
        "unique_events": int(len(ev)), "unique_objects": int(len(ob)), "e2o_relationships": int(len(rel)),
        "o2o_relationships": int(len(o2o)), "shared_events": shared,
        "events_without_objects": int(len(set(data.events["event_id"]) - set(data.e2o["event_id"]))) if not types else 0,
        "object_types": sorted(per_type, key=lambda r: -r["objects"]),
        "event_types": sorted(per_event_type, key=lambda r: -r["unique_events"]),
        "o2o_qualifiers": [{"source_type": s, "target_type": t, "qualifier": q, "count": int(c)}
                           for (s, t, q), c in o2o.groupby(["source_type", "target_type", "qualifier"]).size().items()],
        "cost_total_unique_events": _numeric_attribute_total(ev, "cost"),
        "time_range": [ev["ts"].min().isoformat(), ev["ts"].max().isoformat()] if len(ev) else None,
    }


def ocdfg(data: OcelData, types: list[str] | None = None) -> dict:
    """Object-centric DFG: per object type, consecutive events of each object form an edge."""
    ev, ob, rel = _restrict(data, types)
    nodes = []
    for etype, g in ev.groupby("event_type"):
        r = rel[rel["event_type"] == etype]
        nodes.append({"id": etype, "unique_events": int(len(g)),
                      "per_type": {t: int(c) for t, c in r.groupby("object_type")["event_id"].nunique().items()}})
    edges = []
    starts, ends = [], []
    for otype, g in rel.groupby("object_type"):
        g = g.sort_values(["object_id", "ts", "position"], kind="mergesort")
        same = g["object_id"].to_numpy()[1:] == g["object_id"].to_numpy()[:-1]
        src = g["event_type"].to_numpy()[:-1][same]
        tgt = g["event_type"].to_numpy()[1:][same]
        oid = g["object_id"].to_numpy()[1:][same]
        src_e = g["event_id"].to_numpy()[:-1][same]
        tgt_e = g["event_id"].to_numpy()[1:][same]
        dt = (g["ts"].to_numpy()[1:][same] - g["ts"].to_numpy()[:-1][same]) / np.timedelta64(1, "s")
        frame = pd.DataFrame({"source": src, "target": tgt, "object_id": oid, "pair": list(zip(src_e, tgt_e)), "dt": dt})
        for (s, t), e in frame.groupby(["source", "target"]):
            edges.append({"object_type": otype, "source": s, "target": t,
                          "object_transitions": int(len(e)), "objects": int(e["object_id"].nunique()),
                          "unique_event_pairs": int(e["pair"].nunique()),
                          "median_elapsed_s": float(np.median(e["dt"]))})
        first = g.groupby("object_id")["event_type"].first().value_counts()
        last = g.groupby("object_id")["event_type"].last().value_counts()
        starts += [{"object_type": otype, "activity": a, "objects": int(c)} for a, c in first.items()]
        ends += [{"object_type": otype, "activity": a, "objects": int(c)} for a, c in last.items()]
    return {"type": "object_centric_dfg", "object_types": sorted(rel["object_type"].dropna().unique().tolist()),
            "nodes": sorted(nodes, key=lambda n: -n["unique_events"]),
            "edges": sorted(edges, key=lambda e: (e["object_type"], -e["object_transitions"])),
            "start": starts, "end": ends, "unique_events": int(len(ev))}


def object_detail(data: OcelData, db: Session, ocel: OcelLog, object_id: str) -> dict | None:
    row = data.objects[data.objects["object_id"] == object_id]
    if row.empty:
        return None
    otype = row["object_type"].iloc[0]
    rel = data.e2o[data.e2o["object_id"] == object_id].sort_values(["ts", "position"], kind="mergesort")
    eids = rel["event_id"].tolist()
    co = data.e2o[data.e2o["event_id"].isin(eids) & (data.e2o["object_id"] != object_id)]
    co_objects = co.groupby(["object_id", "object_type"]).size().reset_index(name="shared_events")
    out_o2o = data.o2o[data.o2o["source_id"] == object_id]
    in_o2o = data.o2o[data.o2o["target_id"] == object_id]
    attrs = db.execute(select(OcelObjectAttribute.name, OcelObjectAttribute.value, OcelObjectAttribute.ts)
                       .where(OcelObjectAttribute.log_id == ocel.id, OcelObjectAttribute.object_id == object_id)
                       .order_by(OcelObjectAttribute.ts, OcelObjectAttribute.id)).all()
    events = []
    by_event = co.groupby("event_id")
    for r in rel.itertuples():
        others = by_event.get_group(r.event_id) if r.event_id in by_event.groups else None
        events.append({"event_id": r.event_id, "event_type": r.event_type, "timestamp": r.ts.isoformat(),
                       "qualifier": r.qualifier,
                       "other_objects": [] if others is None else [{"object_id": o, "object_type": t}
                                                                   for o, t in zip(others["object_id"], others["object_type"])][:20],
                       "other_object_count": 0 if others is None else int(len(others))})
    return {
        "object_id": object_id, "object_type": otype, "event_count": len(events),
        "lifetime_s": float((rel["ts"].max() - rel["ts"].min()).total_seconds()) if len(rel) > 1 else 0.0,
        "attributes": [{"name": n, "value": v, "valid_from": None if ts.year == 1970 else ts.isoformat() + "Z"}
                       for n, v, ts in attrs],
        "events": events,
        "related_via_events": [{"object_id": o, "object_type": t, "shared_events": int(c)}
                               for o, t, c in co_objects.sort_values("shared_events", ascending=False).itertuples(index=False)][:100],
        "related_via_o2o": [{"direction": "out", "object_id": r.target_id, "object_type": r.target_type, "qualifier": r.qualifier}
                            for r in out_o2o.itertuples()]
        + [{"direction": "in", "object_id": r.source_id, "object_type": r.source_type, "qualifier": r.qualifier}
           for r in in_o2o.itertuples()],
    }


def objects_page(data: OcelData, object_type: str | None, search: str | None, page: int, page_size: int) -> dict:
    ob = data.objects
    if object_type:
        ob = ob[ob["object_type"] == object_type]
    if search:
        ob = ob[ob["object_id"].str.contains(search, case=False, regex=False)]
    counts = data.e2o.groupby("object_id").agg(events=("event_id", "nunique"), first=("ts", "min"), last=("ts", "max"))
    ob = ob.join(counts, on="object_id").sort_values(["object_type", "object_id"])
    total = len(ob)
    chunk = ob.iloc[(page - 1) * page_size: page * page_size]
    items = [{"object_id": r.object_id, "object_type": r.object_type,
              "events": 0 if pd.isna(r.events) else int(r.events),
              "first": None if pd.isna(r.first) else r.first.isoformat(),
              "last": None if pd.isna(r.last) else r.last.isoformat()} for r in chunk.itertuples()]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def projection_csv(data: OcelData, object_type: str) -> tuple[bytes, dict]:
    """Flatten to a case-centric CSV by one object type (each object becomes a case).

    Events related to several objects of the type are copied into each of their
    cases; ``ocel_event_id`` and ``objects_sharing_event`` keep that visible.
    Event costs are deliberately not mapped to the cost field (they would be
    counted once per copy)."""
    rel = data.e2o[data.e2o["object_type"] == object_type].sort_values(["object_id", "ts", "position"], kind="mergesort")
    if rel.empty:
        raise ValueError("no events for this object type")
    sharing = rel.groupby("event_id")["object_id"].nunique()
    out = pd.DataFrame({
        "case_id": rel["object_id"].to_numpy(), "activity": rel["event_type"].to_numpy(),
        "timestamp": rel["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").to_numpy(),
        "ocel_event_id": rel["event_id"].to_numpy(), "qualifier": rel["qualifier"].to_numpy(),
        "objects_sharing_event": rel["event_id"].map(sharing).to_numpy(),
    })
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    info = {"object_type": object_type, "cases": int(rel["object_id"].nunique()), "rows": int(len(rel)),
            "unique_events": int(rel["event_id"].nunique()),
            "duplicated_event_rows": int(len(rel) - rel["event_id"].nunique())}
    return buf.getvalue().encode("utf-8"), info


def relationship_delays(data: OcelData, object_type: str) -> dict:
    """Lifetime of objects of a type grouped by how many related objects of each other type they have.

    Relations are taken from O2O links and from shared events. Descriptive only."""
    rel = data.e2o[data.e2o["object_type"] == object_type]
    life = rel.groupby("object_id")["ts"].agg(["min", "max"])
    lifetime = (life["max"] - life["min"]).dt.total_seconds()
    groups = []
    co = data.e2o[data.e2o["event_id"].isin(set(rel["event_id"]))][["event_id", "object_id", "object_type"]]
    co = co.rename(columns={"object_id": "object_id_other", "object_type": "object_type_other"})
    pairs = rel[["event_id", "object_id"]].merge(co, on="event_id")
    pairs = pairs[(pairs["object_id_other"] != pairs["object_id"]) & (pairs["object_type_other"] != object_type)]
    o2o = data.o2o
    links = pd.concat([
        o2o[o2o["source_type"] == object_type].rename(columns={"source_id": "object_id", "target_id": "other", "target_type": "other_type"})[["object_id", "other", "other_type", "qualifier"]],
        o2o[o2o["target_type"] == object_type].rename(columns={"target_id": "object_id", "source_id": "other", "source_type": "other_type"})[["object_id", "other", "other_type", "qualifier"]],
    ])
    sources = [("shared_events", t, pairs[pairs["object_type_other"] == t].groupby("object_id")["object_id_other"].nunique())
               for t in sorted(pairs["object_type_other"].dropna().unique())]
    sources += [("o2o", f"{t} ({q or '—'})", g.groupby("object_id")["other"].nunique())
                for (t, q), g in links.groupby(["other_type", "qualifier"])]
    for via, label, counts in sources:
        n_related = counts.reindex(lifetime.index, fill_value=0)
        frame = pd.DataFrame({"n": n_related, "lifetime": lifetime})
        buckets = []
        for n, g in frame.groupby(frame["n"].clip(upper=3)):
            buckets.append({"related": int(n), "related_label": f"{int(n)}+" if n == 3 else str(int(n)),
                            "objects": int(len(g)), "median_lifetime_s": float(g["lifetime"].median()),
                            "p90_lifetime_s": float(g["lifetime"].quantile(0.9))})
        groups.append({"via": via, "related_type": label, "buckets": buckets})
    return {"object_type": object_type, "objects": int(len(lifetime)), "groups": groups,
            "median_lifetime_s": float(lifetime.median()) if len(lifetime) else None}


# =========================================================================== OCPN (mining run)
def ocpn_payload(data: OcelData, types: list[str] | None) -> dict:
    ev, ob, rel = _restrict(data, types)
    return {"events": ev[["event_id", "event_type", "ts"]].copy(), "objects": ob.copy(),
            "relations": rel[["event_id", "object_id", "qualifier", "object_type", "event_type", "ts"]].copy()}


def exec_ocpn(db: Session, run, call) -> dict:
    from app.mining.runs import _store_artifact
    from app.mining.worker import TaskFailed

    ocel = db.get(OcelLog, run.ocel_log_id)
    if ocel is None:
        raise TaskFailed("ocel.err.deleted")
    if ocel.version != run.request.get("ocel_version"):
        raise TaskFailed("run.err.dataset_changed")
    data = load(db, ocel)
    out = call("ocpn", {**ocpn_payload(data, run.request.get("object_types")), "params": run.params})
    rep = out.pop("representation")
    _store_artifact(db, run.id, "representations.json", "application/json", json.dumps({"oc_petri_net": rep}))
    _store_artifact(db, run.id, "model.json", "application/json", json.dumps(
        {"processlens_model": "oc_petri_net", "format_note": "application-specific JSON", "model": rep}, ensure_ascii=False))
    out["representations"] = ["oc_petri_net"]
    return out
