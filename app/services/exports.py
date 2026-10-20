"""CSV/JSON/XES exports.

CSV cells holding text that a spreadsheet could interpret as a formula
(starting with ``= + - @``, tab or carriage return) are prefixed with a single
quote. Numeric cells are written as numbers and left untouched.
"""

from __future__ import annotations

import csv
import io
import json
import math
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

import pandas as pd

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> object:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    s = str(value)
    if s.startswith(FORMULA_PREFIXES):
        return "'" + s
    return s


def to_csv(rows: Iterable[dict], columns: Sequence[str]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([safe_cell(c) for c in columns])
    for row in rows:
        writer.writerow([safe_cell(row.get(c)) for c in columns])
    return buf.getvalue()


def json_default(o):
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if isinstance(o, float) and math.isnan(o):
        return None
    return str(o)


def to_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, default=json_default, indent=2)


EVENT_EXPORT_COLUMNS = ["case_id", "seq", "activity", "timestamp_utc", "start_timestamp_utc", "resource",
                        "department", "cost", "case_status", "event_id", "source_row",
                        "elapsed_since_previous_event_s"]


def _iso_or_none(v) -> str | None:
    return None if v is None or pd.isna(v) else pd.Timestamp(v).isoformat()


def events_for_export(events: pd.DataFrame, attributes: dict[tuple[str, int], dict] | None = None) -> list[dict]:
    """Rows for an events export; ``attributes`` maps (case_id, seq) -> extra attributes."""
    out = []
    cols = ["case_id", "seq", "activity", "ts", "start_ts", "resource", "department", "cost", "case_status",
            "event_id", "source_row", "elapsed_s"]
    for rec in events[cols].to_dict(orient="records"):
        row = {
            "case_id": rec["case_id"], "seq": int(rec["seq"]), "activity": rec["activity"],
            "timestamp_utc": _iso_or_none(rec["ts"]), "start_timestamp_utc": _iso_or_none(rec["start_ts"]),
            "resource": rec["resource"], "department": rec["department"],
            "cost": None if pd.isna(rec["cost"]) else float(rec["cost"]),
            "case_status": rec["case_status"], "event_id": rec["event_id"], "source_row": int(rec["source_row"]),
            "elapsed_since_previous_event_s": None if pd.isna(rec["elapsed_s"]) else float(rec["elapsed_s"]),
        }
        if attributes is not None:
            for k, v in (attributes.get((rec["case_id"], int(rec["seq"]))) or {}).items():
                row[f"attr:{k}"] = v
        out.append(row)
    return out


def write_xes(events: pd.DataFrame) -> Path:
    """Write the filtered events as XES using PM4Py; returns a temp file path."""
    import pm4py  # imported lazily: PM4Py import takes several seconds

    df = pd.DataFrame({
        "case:concept:name": events["case_id"].astype(str),
        "concept:name": events["activity"].astype(str),
        "time:timestamp": events["ts"],
    })
    if events["resource"].notna().any():
        df["org:resource"] = events["resource"].fillna("").astype(str)
    if events["department"].notna().any():
        df["org:group"] = events["department"].fillna("").astype(str)
    if events["cost"].notna().any():
        df["cost:total"] = events["cost"]
    tmp = tempfile.NamedTemporaryFile(prefix="processlens_", suffix=".xes", delete=False)
    tmp.close()
    pm4py.write_xes(df, tmp.name, case_id_key="case:concept:name")
    return Path(tmp.name)
