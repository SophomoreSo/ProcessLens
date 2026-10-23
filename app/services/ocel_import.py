"""OCEL 2.0 JSON through the import wizard: preview, standard-field mapping and conversion to rows.

Parsing and validation are those of ``app/services/ocel.py``. The wizard adds a mapping of
event attributes onto the standard fields the analyses use (resource, department, cost,
start time) and the choice of the main object type. Attributes stay in the event's
attribute set as well, so the original log is preserved.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from app.services import ocel as ocel_svc
from app.services.ingestion import SYNONYMS, IngestionError, _norm
from app.services.ocel_store import OcelRows

FIELDS = ("resource", "department", "cost", "start_timestamp")


def _naive_utc(ts) -> object:
    return None if ts is None else pd.Timestamp(ts).tz_convert("UTC").tz_localize(None).to_pydatetime()


def parse_file(path: Path) -> ocel_svc.Parsed:
    try:
        return ocel_svc.parse(path.read_bytes())
    except ocel_svc.OcelInvalid as exc:
        errors = sum(1 for f in exc.findings if f["severity"] == "error")
        raise IngestionError("ocel_invalid", details=exc.findings, errors=errors) from None


def attribute_names(parsed: ocel_svc.Parsed) -> list[str]:
    names: dict[str, None] = {}
    for et in parsed.event_types:
        for a in et["attributes"]:
            names.setdefault(a["name"], None)
    for e in parsed.events:
        for k in e["attributes"] or {}:
            names.setdefault(k, None)
    return list(names)


def suggest_fields(names: list[str]) -> dict[str, str | None]:
    normalized = {_norm(n): n for n in names}
    out: dict[str, str | None] = {}
    for f in FIELDS:
        out[f] = next((normalized[s] for s in SYNONYMS[f] if s in normalized), None)
    return out


def summary(parsed: ocel_svc.Parsed) -> dict:
    """What the wizard shows for an OCEL upload (stored as ``Upload.conversion``)."""
    per_type: dict[str, int] = {}
    for o in parsed.objects:
        per_type[o["object_type"]] = per_type.get(o["object_type"], 0) + 1
    related: dict[str, set] = {}
    types = {o["object_id"]: o["object_type"] for o in parsed.objects}
    for r in parsed.e2o:
        related.setdefault(types[r["object_id"]], set()).add(r["event_id"])
    names = attribute_names(parsed)
    main = max(related, key=lambda t: (len(related[t]), -list(per_type).index(t))) if related else None
    return {
        "format": "ocel",
        "counts": {"events": len(parsed.events), "objects": len(parsed.objects), "e2o": len(parsed.e2o),
                   "o2o": len(parsed.o2o), "object_attribute_values": len(parsed.object_attrs)},
        "object_types": [{"name": t, "objects": n, "events": len(related.get(t, ()))} for t, n in per_type.items()],
        "event_types": [et["name"] for et in parsed.event_types],
        "attribute_names": names,
        "suggested_fields": suggest_fields(names),
        "suggested_main_type": main,
        "findings": parsed.findings,
    }


def rows_from_parsed(parsed: ocel_svc.Parsed, fields: dict[str, str | None]) -> tuple[OcelRows, dict]:
    """OCEL rows with the mapped standard fields filled in; returns counts of unusable values."""
    bad = {"cost": 0, "start_timestamp": 0, "start_after_end": 0}
    rows = OcelRows(event_types=parsed.event_types, object_types=parsed.object_types)
    for e in parsed.events:
        attrs = e["attributes"] or {}

        def val(f):
            key = fields.get(f)
            v = attrs.get(key) if key else None
            return None if v is None or (isinstance(v, str) and not v.strip()) else v

        cost = val("cost")
        if cost is not None:
            try:
                cost = float(cost)
                if not math.isfinite(cost):
                    raise ValueError
            except (TypeError, ValueError):
                bad["cost"] += 1
                cost = None
        start = val("start_timestamp")
        if start is not None:
            start = ocel_svc._parse_time(str(start))
            if start is None:
                bad["start_timestamp"] += 1
            elif start > e["ts"]:
                bad["start_after_end"] += 1
                start = None
        rows.events.append({
            "event_id": e["event_id"], "event_type": e["event_type"], "ts": _naive_utc(e["ts"]),
            "position": e["position"], "attributes": e["attributes"], "start_ts": _naive_utc(start),
            "resource": None if val("resource") is None else str(val("resource"))[:255],
            "department": None if val("department") is None else str(val("department"))[:255],
            "cost": cost, "source_row": None, "id_generated": False,
        })
    rows.objects = list(parsed.objects)
    rows.attrs = [{**a, "ts": _naive_utc(a["ts"]), "event_id": None} for a in parsed.object_attrs]
    rows.e2o = list(parsed.e2o)
    rows.o2o = list(parsed.o2o)
    return rows, bad
