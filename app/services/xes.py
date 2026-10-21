"""XES (IEEE 1849) import: streaming conversion of an event log to the import table.

The converted table is an ordinary UTF-8 CSV with one row per event, so the
normal import wizard (mapping, timestamp handling, validation, quarantine,
versioning) applies unchanged. Column names keep the XES attribute keys:

* event attributes as they are (``concept:name``, ``time:timestamp``, ``org:resource`` ...);
* trace attributes prefixed with ``case:`` (``case:concept:name`` is the case id),
  following the convention of PM4Py and the XES-to-table mapping used by most tools.

Lifecycle handling (``lifecycle:transition``) is explicit:

* ``all_events`` — every event becomes a row; the transition is kept as a column.
* ``pair_start_complete`` — each ``complete`` event becomes a row; the matching
  earlier ``start`` event of the same activity (and ``concept:instance`` if present)
  in the same trace provides ``start_timestamp`` (first-in-first-out). Events with
  other transitions and starts without a later complete are left out and counted.
  Events without a transition count as ``complete``.

Values of list and container attributes, meta-attributes (attributes nested in
attributes), log-level attributes, global default values and classifiers are not
imported; every such case is counted in the conversion summary. Documents with a
DTD (``<!DOCTYPE``/``<!ENTITY``) are refused.
"""

from __future__ import annotations

import csv
import gzip
import io
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from pathlib import Path

from app.config import Settings
from app.services.ingestion import XES_SUFFIXES, IngestionError, xes_suffix  # noqa: F401 (re-exported)

LIFECYCLE_MODES = ("all_events", "pair_start_complete")
SIMPLE_TYPES = {"string", "date", "int", "float", "boolean", "id"}
COMPLEX_TYPES = {"list", "container"}
LIFECYCLE = "lifecycle:transition"
PREFERRED = ["case:concept:name", "concept:name", "time:timestamp", "start_timestamp", LIFECYCLE,
             "org:resource", "org:group", "org:role", "cost:total"]
HEAD_BYTES = 1024 * 1024


class _LimitedReader(io.RawIOBase):
    """Counts decompressed bytes so a small .xes.gz cannot expand without bound."""

    def __init__(self, raw, limit: int, limit_mb: int):
        self.raw, self.limit, self.limit_mb, self.read_bytes = raw, limit, limit_mb, 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = self.raw.read(len(b))
        self.read_bytes += len(data)
        if self.read_bytes > self.limit:
            raise IngestionError("file_too_large", limit_mb=self.limit_mb)
        b[:len(data)] = data
        return len(data)


def _open(path: Path, settings: Settings):
    raw = gzip.open(path, "rb") if path.name.endswith(".gz") else path.open("rb")
    return io.BufferedReader(_LimitedReader(raw, settings.max_upload_bytes, settings.max_upload_mb))


def _check_head(path: Path, settings: Settings) -> None:
    try:
        with _open(path, settings) as fh:
            head = fh.read(HEAD_BYTES)
    except (OSError, EOFError) as exc:  # broken gzip stream
        raise IngestionError("xes_invalid", detail=str(exc)[:200]) from None
    lowered = head.lower()
    root = lowered.find(b"<log")
    before_root = lowered if root < 0 else lowered[:root]
    if b"<!doctype" in before_root or b"<!entity" in before_root:
        raise IngestionError("xes_dtd")
    if root < 0:
        raise IngestionError("xes_no_log")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _pair(events: list[dict], counts: dict) -> list[dict]:
    pending: dict[tuple, deque] = defaultdict(deque)
    rows = []
    for ev in events:
        transition = (ev.get(LIFECYCLE) or "complete").strip().lower()
        key = (ev.get("concept:name"), ev.get("concept:instance"))
        if transition == "start":
            pending[key].append(ev)
        elif transition == "complete":
            row = {k: v for k, v in ev.items() if k != LIFECYCLE}
            if pending[key]:
                row["start_timestamp"] = pending[key].popleft().get("time:timestamp", "")
                counts["paired"] += 1
            rows.append(row)
        else:
            counts["dropped_transitions"][transition] += 1
    counts["starts_without_complete"] += sum(len(q) for q in pending.values())
    return rows


def convert(src: Path, dest: Path, settings: Settings, lifecycle: str | None = None) -> dict:
    """Convert ``src`` (XES or XES.gz) to the import CSV ``dest``; returns the conversion summary.

    ``lifecycle=None`` chooses ``pair_start_complete`` if the log contains ``start``
    transitions and ``all_events`` otherwise (the summary says which and why)."""
    if lifecycle is not None and lifecycle not in LIFECYCLE_MODES:
        raise ValueError(lifecycle)
    _check_head(src, settings)
    traces: list[tuple[dict, list[dict]]] = []
    lifecycle_values: Counter = Counter()
    complex_skipped: Counter = Counter()
    s = {"meta": 0, "outside": 0, "global_keys": set(), "classifiers": [], "extensions": [], "log_name": None,
         "no_key": 0}
    stack: list[str] = []
    trace_attrs: dict | None = None
    trace_events: list[dict] = []
    event: dict | None = None
    n_events = 0
    try:
        with _open(src, settings) as fh:
            for kind, elem in ET.iterparse(fh, events=("start", "end")):
                tag = _local(elem.tag)
                if kind == "start":
                    parent = stack[-1] if stack else None
                    stack.append(tag)
                    if tag == "trace" and parent == "log":
                        trace_attrs, trace_events = {}, []
                    elif tag == "event":
                        if parent == "trace":
                            event = {}
                            n_events += 1
                            if n_events > settings.max_rows:
                                raise IngestionError("too_many_rows", limit=f"{settings.max_rows:,}")
                        else:
                            s["outside"] += 1
                    elif tag == "classifier" and parent == "log":
                        s["classifiers"].append(elem.get("name") or "")
                    elif tag == "extension" and parent == "log":
                        s["extensions"].append(elem.get("prefix") or elem.get("name") or "")
                    elif tag in SIMPLE_TYPES or tag in COMPLEX_TYPES:
                        key = elem.get("key")
                        if parent == "global":
                            s["global_keys"].add(key)
                        elif parent in ("trace", "event"):
                            if not key:
                                s["no_key"] += 1
                            elif tag in COMPLEX_TYPES:
                                complex_skipped[key] += 1
                            else:
                                target = event if parent == "event" else trace_attrs
                                if target is not None:
                                    target[key] = elem.get("value", "")
                        elif parent == "log":
                            if key == "concept:name" and tag in SIMPLE_TYPES:
                                s["log_name"] = elem.get("value")
                        elif parent in SIMPLE_TYPES:  # meta-attribute (attribute of an attribute)
                            s["meta"] += 1
                        # attributes inside lists/containers are covered by complex_attributes_skipped
                    continue
                # end
                stack.pop()
                parent = stack[-1] if stack else None
                if tag == "event" and parent == "trace" and event is not None:
                    lifecycle_values[(event.get(LIFECYCLE) or "").strip().lower() or "(none)"] += 1
                    trace_events.append(event)
                    event = None
                elif tag == "trace" and parent == "log" and trace_attrs is not None:
                    traces.append((trace_attrs, trace_events))
                    trace_attrs, trace_events = None, []
                    elem.clear()
    except ET.ParseError as exc:
        raise IngestionError("xes_invalid", detail=str(exc)[:200]) from None
    except (OSError, EOFError) as exc:
        raise IngestionError("xes_invalid", detail=str(exc)[:200]) from None
    if not traces:
        raise IngestionError("xes_no_traces")

    has_start = lifecycle_values.get("start", 0) > 0
    mode = lifecycle or ("pair_start_complete" if has_start else "all_events")
    counts = {"paired": 0, "starts_without_complete": 0, "dropped_transitions": Counter()}
    rows: list[dict] = []
    order: dict[str, None] = {}
    empty_traces = traces_without_name = 0
    for attrs, events in traces:
        if not events:
            empty_traces += 1
            continue
        if not attrs.get("concept:name"):
            traces_without_name += 1
        case_cols = {f"case:{k}": v for k, v in attrs.items()}
        out = _pair(events, counts) if mode == "pair_start_complete" else events
        for ev in out:
            row = {**case_cols, **ev}
            for k in row:
                order.setdefault(k, None)
            rows.append(row)
    if not rows:
        raise IngestionError("xes_no_events")
    columns = [c for c in PREFERRED if c in order] + [c for c in order if c not in PREFERRED and not c.startswith("case:")] \
        + [c for c in order if c not in PREFERRED and c.startswith("case:")]
    if mode == "pair_start_complete" and "start_timestamp" not in columns:
        columns.insert(columns.index("time:timestamp") + 1 if "time:timestamp" in columns else len(columns),
                       "start_timestamp")
    if len(columns) > settings.max_columns:
        raise IngestionError("too_many_columns", count=len(columns), limit=settings.max_columns)
    with dest.open("w", encoding="utf-8", newline="") as out_fh:
        writer = csv.DictWriter(out_fh, fieldnames=columns, restval="", extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    return {
        "format": "xes",
        "lifecycle_mode": mode,
        "lifecycle_mode_chosen": "user" if lifecycle else ("auto_start_events" if has_start else "auto_no_start_events"),
        "log_name": s["log_name"],
        "traces": len(traces),
        "empty_traces": empty_traces,
        "traces_without_case_id": traces_without_name,
        "events_in_file": n_events,
        "rows": len(rows),
        "lifecycle_transitions": dict(sorted(lifecycle_values.items())),
        "paired_start_events": counts["paired"],
        "starts_without_complete": counts["starts_without_complete"],
        "dropped_transitions": dict(sorted(counts["dropped_transitions"].items())),
        "complex_attributes_skipped": dict(sorted(complex_skipped.items())),
        "meta_attributes_skipped": s["meta"],
        "attributes_without_key": s["no_key"],
        "events_outside_traces": s["outside"],
        "global_attributes": sorted(k for k in s["global_keys"] if k),
        "classifiers": [c for c in s["classifiers"] if c],
        "extensions": [e for e in s["extensions"] if e],
        "columns": columns,
    }


def findings(summary: dict) -> list[dict]:
    """Translatable notes about everything the conversion did not carry over one-to-one."""
    out: list[dict] = []

    def add(severity: str, code: str, **params):
        params = {k: f"{v:,}" if isinstance(v, int) else v for k, v in params.items()}
        out.append({"severity": severity, "key": f"xes.finding.{code}", "params": params})

    add("info", f"lifecycle_{summary['lifecycle_mode']}", rows=summary["rows"], events=summary["events_in_file"],
        paired=summary["paired_start_events"])
    if summary["lifecycle_mode_chosen"] == "auto_start_events":
        add("info", "lifecycle_auto")
    if summary["dropped_transitions"]:
        add("warning", "dropped_transitions",
            detail=", ".join(f"{k} × {v:,}" for k, v in summary["dropped_transitions"].items()))
    if summary["starts_without_complete"]:
        add("warning", "starts_without_complete", count=summary["starts_without_complete"])
    if summary["traces_without_case_id"]:
        add("error", "traces_without_case_id", count=summary["traces_without_case_id"])
    if summary["empty_traces"]:
        add("info", "empty_traces", count=summary["empty_traces"])
    if summary["events_outside_traces"]:
        add("warning", "events_outside_traces", count=summary["events_outside_traces"])
    if summary["complex_attributes_skipped"]:
        add("warning", "complex_attributes",
            detail=", ".join(f"{k} × {v:,}" for k, v in summary["complex_attributes_skipped"].items()))
    if summary["meta_attributes_skipped"]:
        add("info", "meta_attributes", count=summary["meta_attributes_skipped"])
    if summary["attributes_without_key"]:
        add("warning", "attributes_without_key", count=summary["attributes_without_key"])
    if summary["global_attributes"]:
        add("info", "globals_not_applied", keys=", ".join(summary["global_attributes"][:15]))
    if summary["classifiers"]:
        add("info", "classifiers_ignored", names=", ".join(summary["classifiers"][:10]))
    return out
