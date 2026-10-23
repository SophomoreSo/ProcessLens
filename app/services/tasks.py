"""Task mining on imported user-interaction events (application + action).

Input is a CSV exported by some other tool: session_id, timestamp, application,
action, pseudonymous_user_id, optionally business_case_id and duration. Only
these fields are stored; every other column is reported and dropped.
ProcessLens never captures screens, keystrokes or window contents.

Working time is never inferred. The time between a session's first and last
event is reported as *elapsed span*; durations appear only where the source
supplied them explicitly, together with how many events had one.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
from collections import Counter
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import Dataset, OcelObject, TaskEvent, TaskLog
from app.services.cache import cache

log = logging.getLogger("processlens.tasks")

FIELDS = ("session_id", "timestamp", "application", "action", "user_id", "business_case_id", "duration")
REQUIRED = ("timestamp", "application", "action", "user_id")
ALIASES = {
    "session_id": ("session_id", "session", "sessionid"),
    "timestamp": ("timestamp", "time", "ts", "datetime", "event_time"),
    "application": ("application", "app", "application_name"),
    "action": ("action", "activity", "step", "event"),
    "user_id": ("pseudonymous_user_id", "user_id", "user", "pseudonym", "userid"),
    "business_case_id": ("business_case_id", "case_id", "case", "business_case"),
    "duration": ("duration", "duration_s", "duration_seconds"),
}
SESSION_MODES = ("source", "inactivity", "source_and_inactivity")
MAX_FINDINGS = 300


class TaskImportError(Exception):
    def __init__(self, key: str, findings: list[dict] | None = None, **params):
        super().__init__(key)
        self.key = key
        self.findings = findings or []
        self.params = params


def _norm(name: str) -> str:
    return "".join(ch for ch in name.strip().lower().replace(" ", "_").replace("-", "_") if ch.isalnum() or ch == "_")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise TaskImportError("task.err.encoding")


def read_table(raw: bytes) -> pd.DataFrame:
    text = _decode(raw)
    sample = text[:65536]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    try:
        df = pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, keep_default_na=False, skipinitialspace=True)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise TaskImportError("task.err.unreadable", detail=str(exc)[:200]) from None
    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        raise TaskImportError("task.err.duplicate_columns")
    return df


def suggest_mapping(columns: list[str]) -> dict[str, str | None]:
    normalized = {_norm(c): c for c in columns}
    return {f: next((normalized[a] for a in ALIASES[f] if a in normalized), None) for f in FIELDS}


def preview(raw: bytes) -> dict:
    df = read_table(raw)
    return {"columns": list(df.columns), "rows": int(len(df)), "suggested_mapping": suggest_mapping(list(df.columns)),
            "sample": df.head(10).to_dict(orient="records")}


def _finding(findings: list, severity: str, code: str, row: int | None = None, **params) -> None:
    if len(findings) < MAX_FINDINGS:
        findings.append({"severity": severity, "code": code, "key": f"task.finding.{code}", "row": row, "params": params})


@dataclass
class ParsedTasks:
    frame: pd.DataFrame
    findings: list[dict]
    ignored_columns: list[str]
    invalid_rows: int


def parse(raw: bytes, mapping: dict[str, str | None], source_timezone: str = "UTC",
          invalid_row_policy: str = "reject_import") -> ParsedTasks:
    df = read_table(raw)
    findings: list[dict] = []
    unknown = sorted(set(mapping) - set(FIELDS))
    if unknown:
        raise TaskImportError("task.err.unknown_fields", fields=", ".join(unknown))
    missing = [f for f in REQUIRED if not mapping.get(f)]
    if missing:
        raise TaskImportError("task.err.missing_mapping", fields=", ".join(missing))
    absent = [c for c in mapping.values() if c and c not in df.columns]
    if absent:
        raise TaskImportError("task.err.column_not_found", columns=", ".join(absent))
    used = [c for c in mapping.values() if c]
    if len(used) != len(set(used)):
        raise TaskImportError("task.err.column_twice")
    try:
        tz = ZoneInfo(source_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise TaskImportError("task.err.timezone", tz=source_timezone) from None
    ignored = [c for c in df.columns if c not in used]
    if ignored:
        _finding(findings, "info", "columns_not_stored", None, columns=", ".join(ignored[:20]))
    if df.empty:
        raise TaskImportError("task.err.no_rows")

    col = lambda f: df[mapping[f]].str.strip() if mapping.get(f) else pd.Series([""] * len(df), index=df.index)
    out = pd.DataFrame({"position": np.arange(len(df)), "row": np.arange(2, len(df) + 2)})
    for f in ("session_id", "application", "action", "user_id", "business_case_id"):
        out[f] = col(f).to_numpy()
    bad = np.zeros(len(df), dtype=bool)
    for f in ("application", "action", "user_id"):
        empty = out[f] == ""
        for r in out.loc[empty, "row"].head(20):
            _finding(findings, "error", "missing_value", int(r), field=f)
        bad |= empty.to_numpy()
    raw_ts = col("timestamp")
    # values with an explicit offset keep it; naive values are read in the source timezone
    has_offset = raw_ts.str.contains(r"(?:Z|z|[+-]\d{2}:?\d{2})$", regex=True)
    with_offset = pd.to_datetime(raw_ts.where(has_offset), format="ISO8601", utc=True, errors="coerce")
    naive = pd.to_datetime(raw_ts.where(~has_offset), format="ISO8601", errors="coerce")
    naive = naive.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    ts = with_offset.where(has_offset, naive)
    bad_ts = ts.isna().to_numpy()
    for r, v in zip(out.loc[bad_ts, "row"].head(20), raw_ts[bad_ts].head(20)):
        _finding(findings, "error", "invalid_timestamp", int(r), value=str(v)[:60])
    bad |= bad_ts
    out["ts"] = ts.to_numpy()
    if mapping.get("duration"):
        raw_d = col("duration")
        d = pd.to_numeric(raw_d.where(raw_d != ""), errors="coerce")
        bad_d = ((raw_d != "") & (d.isna() | (d < 0))).to_numpy()
        for r, v in zip(out.loc[bad_d, "row"].head(20), raw_d[bad_d].head(20)):
            _finding(findings, "error", "invalid_duration", int(r), value=str(v)[:60])
        bad |= bad_d
        out["duration_s"] = d.to_numpy()
    else:
        out["duration_s"] = np.nan
    identifying = out["user_id"].str.contains("@", regex=False).to_numpy()
    if identifying.any():
        _finding(findings, "error", "user_id_not_pseudonymous", None, count=int(identifying.sum()))
        bad |= identifying
    invalid = int(bad.sum())
    if invalid and (invalid_row_policy == "reject_import" or identifying.any()):
        raise TaskImportError("task.err.invalid_rows", findings, invalid=invalid, total=len(df))
    if invalid:
        _finding(findings, "warning", "rows_skipped", None, count=invalid)
    out = out[~bad]
    if out.empty:
        raise TaskImportError("task.err.no_valid_rows", findings)
    for f in ("session_id", "business_case_id"):
        out[f] = out[f].where(out[f] != "", None)
    if mapping.get("session_id"):
        users = out.dropna(subset=["session_id"]).groupby("session_id")["user_id"].nunique()
        if (users > 1).any():
            _finding(findings, "warning", "session_several_users", None, count=int((users > 1).sum()))
        if out["session_id"].isna().any():
            _finding(findings, "warning", "rows_without_session", None, count=int(out["session_id"].isna().sum()))
    return ParsedTasks(out.reset_index(drop=True), findings, ignored, invalid)


def store(db: Session, parsed: ParsedTasks, raw: bytes, name: str, filename: str, mapping: dict, source_timezone: str,
          linked_dataset_id: int | None = None, description: str | None = None, is_demo: bool = False) -> TaskLog:
    f = parsed.frame
    counts = {"events": int(len(f)), "users": int(f["user_id"].nunique()),
              "source_sessions": int(f["session_id"].nunique()) if f["session_id"].notna().any() else 0,
              "applications": int(f["application"].nunique()), "actions": int(f[["application", "action"]].drop_duplicates().shape[0]),
              "business_cases": int(f["business_case_id"].nunique()), "events_with_duration": int(f["duration_s"].notna().sum()),
              "skipped_rows": parsed.invalid_rows}
    tl = TaskLog(name=name[:200], description=description, is_demo=is_demo, source_filename=filename[:255],
                 source_sha256=hashlib.sha256(raw).hexdigest(), source_size_bytes=len(raw),
                 mapping={k: v for k, v in mapping.items() if v}, source_timezone=source_timezone, counts=counts,
                 findings=parsed.findings, ignored_columns=parsed.ignored_columns, linked_dataset_id=linked_dataset_id)
    db.add(tl)
    db.flush()
    rows = [{"log_id": tl.id, "position": int(r.position), "session_id": r.session_id, "user_id": r.user_id,
             "ts": r.ts.tz_convert("UTC").tz_localize(None).to_pydatetime(), "application": r.application,
             "action": r.action, "business_case_id": r.business_case_id,
             "duration_s": None if pd.isna(r.duration_s) else float(r.duration_s)} for r in f.itertuples()]
    for i in range(0, len(rows), 5000):
        db.execute(insert(TaskEvent), rows[i:i + 5000])
    db.commit()
    log.info("Imported task log %s: %s", tl.id, counts)
    return tl


def delete_log(db: Session, tl: TaskLog) -> None:
    db.execute(delete(TaskEvent).where(TaskEvent.log_id == tl.id))
    db.delete(tl)
    db.commit()
    cache.invalidate_dataset(("tasks", tl.id))


# =========================================================================== analysis
def load(db: Session, tl: TaskLog) -> pd.DataFrame:
    def compute() -> pd.DataFrame:
        df = pd.DataFrame(db.execute(select(TaskEvent.position, TaskEvent.session_id, TaskEvent.user_id, TaskEvent.ts,
                                            TaskEvent.application, TaskEvent.action, TaskEvent.business_case_id,
                                            TaskEvent.duration_s).where(TaskEvent.log_id == tl.id)).all(),
                          columns=["position", "session_id", "user_id", "ts", "application", "action",
                                   "business_case_id", "duration_s"])
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize("UTC")
        df["duration_s"] = pd.to_numeric(df["duration_s"])
        return df.sort_values(["user_id", "ts", "position"], kind="mergesort").reset_index(drop=True)

    return cache.get_or_compute((("tasks", tl.id), tl.version, "events"), compute)


@dataclass(frozen=True)
class Sessionization:
    mode: str = "source_and_inactivity"
    gap_minutes: float = 30.0

    def key(self) -> tuple:
        return (self.mode, float(self.gap_minutes))


def sessionize(df: pd.DataFrame, s: Sessionization) -> pd.DataFrame:
    """Assign a ``session`` key. Events are ordered by user, timestamp and source row.

    * ``source``: the exported session_id; events without one fall back to inactivity splitting.
    * ``inactivity``: a new session starts when the user changes or the gap exceeds the threshold.
    * ``source_and_inactivity``: both — a source session is split further at long gaps.
    """
    if s.mode not in SESSION_MODES:
        raise ValueError(s.mode)
    df = df.copy()
    if s.mode == "source":
        df = df.sort_values(["session_id", "user_id", "ts", "position"], kind="mergesort", na_position="last")
    gap = df["ts"].diff().dt.total_seconds().to_numpy()
    user_change = (df["user_id"] != df["user_id"].shift()).to_numpy()
    long_gap = np.nan_to_num(gap, nan=np.inf) > s.gap_minutes * 60
    sid = df["session_id"]
    sid_change = ((sid != sid.shift()) & ~(sid.isna() & sid.shift().isna())).to_numpy()
    if s.mode == "inactivity":
        new = user_change | long_gap
    elif s.mode == "source":
        no_sid = sid.isna().to_numpy()
        new = sid_change | (no_sid & (user_change | long_gap))
    else:
        new = user_change | long_gap | sid_change
    new[0] = True
    df["session"] = np.cumsum(new)
    df["session"] = "S" + df["session"].astype(str).str.zfill(6)
    return df.sort_values(["session", "ts", "position"], kind="mergesort").reset_index(drop=True)


def _sessions(db: Session, tl: TaskLog, s: Sessionization) -> pd.DataFrame:
    return cache.get_or_compute((("tasks", tl.id), tl.version, "sessions", s.key()), lambda: sessionize(load(db, tl), s))


def _labels(df: pd.DataFrame, granularity: str) -> pd.Series:
    if granularity == "application":
        return df["application"]
    return df["application"] + " · " + df["action"]


def _sequences(df: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """One row per session: label sequence (consecutive repeats collapsed at application level)."""
    lab = _labels(df, granularity)
    frame = pd.DataFrame({"session": df["session"], "label": lab})
    if granularity == "application":
        keep = (frame["label"] != frame["label"].shift()) | (frame["session"] != frame["session"].shift())
        frame = frame[keep]
    return frame.groupby("session", sort=False)["label"].agg(tuple).rename("sequence").to_frame()


def _session_table(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("session", sort=False)
    out = g.agg(user_id=("user_id", "first"), events=("action", "size"), first=("ts", "min"), last=("ts", "max"),
                duration_events=("duration_s", "count"), duration_sum=("duration_s", "sum"),
                cases=("business_case_id", lambda x: x.dropna().nunique()))
    out["elapsed_s"] = (out["last"] - out["first"]).dt.total_seconds()
    return out


def summary(db: Session, tl: TaskLog, s: Sessionization) -> dict:
    df = _sessions(db, tl, s)
    st = _session_table(df)
    with_d = int(df["duration_s"].notna().sum())
    return {
        "sessionization": {"mode": s.mode, "gap_minutes": s.gap_minutes},
        "events": int(len(df)), "users": int(df["user_id"].nunique()), "sessions": int(len(st)),
        "applications": int(df["application"].nunique()),
        "actions": int(df[["application", "action"]].drop_duplicates().shape[0]),
        "sessions_linked_to_cases": int((st["cases"] > 0).sum()),
        "business_cases": int(df["business_case_id"].nunique()),
        "median_events_per_session": float(st["events"].median()),
        "median_elapsed_span_s": float(st["elapsed_s"].median()),
        "events_with_explicit_duration": with_d,
        "explicit_duration_coverage": with_d / len(df) if len(df) else None,
        "explicit_duration_total_s": float(df["duration_s"].sum()) if with_d else None,
    }


def variants(db: Session, tl: TaskLog, s: Sessionization, granularity: str = "action", limit: int = 50) -> dict:
    df = _sessions(db, tl, s)
    seq = _sequences(df, granularity).join(_session_table(df))
    total = len(seq)
    rows = []
    for sequence, g in seq.groupby("sequence", sort=False):
        rows.append({"sequence": list(sequence), "sessions": int(len(g)), "share": len(g) / total,
                     "users": int(g["user_id"].nunique()), "median_events": float(g["events"].median()),
                     "median_elapsed_span_s": float(g["elapsed_s"].median()),
                     "explicit_duration_coverage": float(g["duration_events"].sum() / g["events"].sum())})
    rows.sort(key=lambda r: (-r["sessions"], r["sequence"]))
    return {"granularity": granularity, "sessions": total, "variant_count": len(rows), "variants": rows[:limit]}


def repeated_sequences(db: Session, tl: TaskLog, s: Sessionization, granularity: str = "action", min_length: int = 2,
                       max_length: int = 5, min_sessions: int = 2, limit: int = 50) -> dict:
    """Contiguous task sequences (n-grams) that recur across sessions."""
    df = _sessions(db, tl, s)
    seq = _sequences(df, granularity).join(_session_table(df)[["user_id"]])
    occ: Counter = Counter()
    sessions: dict[tuple, set] = {}
    users: dict[tuple, set] = {}
    for sid, row in seq.iterrows():
        labels = row["sequence"]
        for n in range(min_length, max_length + 1):
            for i in range(len(labels) - n + 1):
                gram = labels[i:i + n]
                occ[gram] += 1
                sessions.setdefault(gram, set()).add(sid)
                users.setdefault(gram, set()).add(row["user_id"])
    rows = [{"sequence": list(g), "length": len(g), "occurrences": c, "sessions": len(sessions[g]), "users": len(users[g])}
            for g, c in occ.items() if len(sessions[g]) >= min_sessions]
    # drop sequences fully explained by a longer one with the same session coverage
    rows.sort(key=lambda r: (-r["sessions"], -r["length"], -r["occurrences"], r["sequence"]))
    kept, seen = [], []
    for r in rows:
        key = tuple(r["sequence"])
        if any(r["sessions"] == k["sessions"] and r["occurrences"] == k["occurrences"] and _contains(k["sequence"], key)
               for k in seen):
            continue
        seen.append(r)
        kept.append(r)
    return {"granularity": granularity, "min_length": min_length, "max_length": max_length, "min_sessions": min_sessions,
            "sessions": int(len(seq)), "sequences": kept[:limit], "total_found": len(kept)}


def _contains(longer: list, shorter: tuple) -> bool:
    n = len(shorter)
    return len(longer) > n and any(tuple(longer[i:i + n]) == shorter for i in range(len(longer) - n + 1))


def app_switches(db: Session, tl: TaskLog, s: Sessionization) -> dict:
    df = _sessions(db, tl, s)
    same = (df["session"].to_numpy()[1:] == df["session"].to_numpy()[:-1])
    a = df["application"].to_numpy()
    src, tgt, sess = a[:-1][same], a[1:][same], df["session"].to_numpy()[1:][same]
    switch = src != tgt
    pairs = Counter(zip(src[switch], tgt[switch]))
    per_session = pd.Series(sess[switch]).value_counts().reindex(df["session"].unique(), fill_value=0)
    return {
        "transitions": int(same.sum()), "switches": int(switch.sum()),
        "switch_share": float(switch.sum() / same.sum()) if same.sum() else None,
        "sessions": int(len(per_session)), "sessions_with_switch": int((per_session > 0).sum()),
        "median_switches_per_session": float(per_session.median()) if len(per_session) else None,
        "mean_switches_per_session": float(per_session.mean()) if len(per_session) else None,
        "pairs": [{"from": f, "to": t_, "switches": int(c)} for (f, t_), c in pairs.most_common(50)],
        "applications": sorted(df["application"].unique().tolist()),
    }


def task_dfg(db: Session, tl: TaskLog, s: Sessionization, granularity: str = "action") -> dict:
    df = _sessions(db, tl, s)
    lab = _labels(df, granularity).to_numpy()
    sess = df["session"].to_numpy()
    nodes = []
    for label, g in df.assign(label=lab).groupby("label"):
        d = g["duration_s"].dropna()
        nodes.append({"id": label, "events": int(len(g)), "sessions": int(g["session"].nunique()),
                      "events_with_duration": int(len(d)), "mean_explicit_duration_s": float(d.mean()) if len(d) else None})
    same = sess[1:] == sess[:-1]
    frame = pd.DataFrame({"source": lab[:-1][same], "target": lab[1:][same], "session": sess[1:][same]})
    edges = [{"source": a, "target": b, "count": int(len(g)), "sessions": int(g["session"].nunique())}
             for (a, b), g in frame.groupby(["source", "target"])]
    firsts = df.assign(label=lab).groupby("session", sort=False)["label"].agg(["first", "last"])
    return {"type": "task_dfg", "granularity": granularity, "nodes": sorted(nodes, key=lambda n: -n["events"]),
            "edges": sorted(edges, key=lambda e: -e["count"]),
            "start": [{"activity": a, "sessions": int(c)} for a, c in firsts["first"].value_counts().items()],
            "end": [{"activity": a, "sessions": int(c)} for a, c in firsts["last"].value_counts().items()],
            "note_key": "task.dfg_frequency_only"}


def business_cases(db: Session, tl: TaskLog, s: Sessionization, dataset_id: int | None = None, page: int = 1,
                   page_size: int = 50) -> dict:
    df = _sessions(db, tl, s)
    linked = df.dropna(subset=["business_case_id"])
    known: dict[str, str] | None = None  # object id -> object type
    ds = db.get(Dataset, dataset_id) if dataset_id else None
    if ds is not None:
        ids = sorted(set(linked["business_case_id"].unique()))
        known = {}
        for i in range(0, len(ids), 900):  # matched against objects of every type
            known.update(dict(db.execute(select(OcelObject.object_id, OcelObject.object_type).where(
                OcelObject.log_id == ds.ocel_log_id, OcelObject.object_id.in_(ids[i:i + 900]))).all()))
    rows = []
    for cid, g in linked.groupby("business_case_id"):
        d = g["duration_s"].dropna()
        rows.append({"business_case_id": cid, "sessions": int(g["session"].nunique()), "events": int(len(g)),
                     "users": int(g["user_id"].nunique()), "applications": sorted(g["application"].unique().tolist()),
                     "first": g["ts"].min().isoformat(), "last": g["ts"].max().isoformat(),
                     "explicit_duration_s": float(d.sum()) if len(d) else None,
                     "events_with_duration": int(len(d)),
                     "in_dataset": None if known is None else cid in known,
                     "object_type": None if known is None else known.get(cid)})
    rows.sort(key=lambda r: (-r["events"], r["business_case_id"]))
    total = len(rows)
    return {"dataset_id": ds.id if ds else None, "dataset_name": ds.name if ds else None,
            "linked_events": int(len(linked)), "unlinked_events": int(len(df) - len(linked)),
            "cases": total, "matched_in_dataset": None if known is None else sum(1 for r in rows if r["in_dataset"]),
            "items": rows[(page - 1) * page_size: page * page_size], "page": page, "page_size": page_size}
