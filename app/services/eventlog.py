"""Loading a dataset into an analysis-ready, cached event log for one object type.

Datasets are object-centric (OCEL tables). An ``EventLog`` is the view of a
dataset through a *lead object type*: every object of that type plays the role
of a case, and its events are the events related to it. An event related to
several objects of the type therefore appears once per object (``ocel_event_id``
keeps its identity); unique-event counts and costs are computed separately.

Definitions implemented here (see docs/METRICS.md), with "case" = object of the lead type:

* Events within a case are ordered by (timestamp, source row). ``seq`` is that
  position. Equal timestamps are ordered by source row only.
* Observed case duration = last event timestamp - first event timestamp.
* Process variant = the ordered tuple of activity names of a case.
* Repeated-activity count = n_events - n_distinct_activities (occurrences after
  the first occurrence of each activity). Rework case = repeated count > 0.
* Completion status (never inferred from "last observed event" alone):
    - mode ``none``: every case is ``unknown``.
    - mode ``end_activity``: ``completed`` if the case's last event activity is one
      of the configured end activities, otherwise ``incomplete``.
    - mode ``case_status``: the case's last non-empty mapped status value;
      ``completed`` if listed as a completed value, ``incomplete`` if listed as an
      open value, otherwise ``unknown``.
* Open-case age = reference time - first event timestamp (for incomplete/unknown
  cases). The reference time is configured per dataset or defaults to the
  latest event timestamp in the dataset (deterministic, never "now").
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dataset, OcelE2O, OcelEvent, OcelObject, OcelObjectAttribute
from app.services.cache import cache
from app.services.ocel_store import STATUS_ATTRIBUTE

VARIANT_SEP = " → "


@dataclass
class EventLog:
    dataset_id: int
    version: int
    settings_revision: int
    events: pd.DataFrame
    cases: pd.DataFrame
    variants: pd.DataFrame
    display_tz: str
    reference_time: pd.Timestamp | None
    reference_time_source: str
    completion_mode: str
    has_start_ts: bool
    has_resource: bool
    has_department: bool
    has_cost: bool
    has_case_status: bool
    object_type: str = "case"

    @property
    def key(self) -> tuple:
        return (self.dataset_id, self.version, self.settings_revision, self.object_type)


def variant_id_for(activities: tuple[str, ...]) -> str:
    return hashlib.sha1("\x1f".join(activities).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class TypeSettings:
    """Analysis settings of one object type (the shape ``build_event_log`` reads)."""

    id: int
    version: int
    settings_revision: int
    display_timezone: str
    completion_mode: str
    completion_activities: list
    completed_status_values: list
    open_status_values: list
    reference_time: object  # naive UTC datetime or None


def settings_for(ds: Dataset, object_type: str) -> TypeSettings:
    if object_type == ds.main_object_type:
        src = {"completion_mode": ds.completion_mode, "completion_activities": ds.completion_activities,
               "completed_status_values": ds.completed_status_values, "open_status_values": ds.open_status_values,
               "reference_time": ds.reference_time}
    else:
        raw = (ds.object_type_settings or {}).get(object_type, {})
        ref = raw.get("reference_time")
        src = {"completion_mode": raw.get("completion_mode", "none"),
               "completion_activities": raw.get("completion_activities", []),
               "completed_status_values": raw.get("completed_status_values", []),
               "open_status_values": raw.get("open_status_values", []),
               "reference_time": pd.Timestamp(ref).tz_localize(None).to_pydatetime() if ref else None}
    return TypeSettings(id=ds.id, version=ds.version, settings_revision=ds.settings_revision,
                        display_timezone=ds.display_timezone or "UTC", **src)


EVENT_COLUMNS = ["case_id", "activity", "ts", "start_ts", "resource", "department", "cost", "case_status",
                 "event_id", "source_row", "seq", "ocel_event_id"]


def _load_events(db: Session, log_id: int, object_type: str) -> pd.DataFrame:
    """Events of every object of ``object_type``, one row per (object, event)."""
    stmt = (
        select(OcelE2O.object_id, OcelEvent.event_type, OcelEvent.ts, OcelEvent.start_ts, OcelEvent.resource,
               OcelEvent.department, OcelEvent.cost, OcelEvent.event_id, OcelEvent.id_generated,
               OcelEvent.source_row, OcelEvent.position)
        .join(OcelObject, (OcelObject.log_id == OcelE2O.log_id) & (OcelObject.object_id == OcelE2O.object_id))
        .join(OcelEvent, (OcelEvent.log_id == OcelE2O.log_id) & (OcelEvent.event_id == OcelE2O.event_id))
        .where(OcelE2O.log_id == log_id, OcelObject.object_type == object_type)
    )
    raw = pd.DataFrame.from_records(db.execute(stmt).all(), columns=[
        "case_id", "activity", "ts", "start_ts", "resource", "department", "cost", "ocel_event_id", "id_generated",
        "source_row", "position"])
    # an event related to the same object twice (different qualifiers) is still one event of that object
    raw = raw.drop_duplicates(["case_id", "ocel_event_id"])
    raw["ts"] = pd.to_datetime(raw["ts"]).dt.as_unit("us").dt.tz_localize("UTC")
    raw["start_ts"] = pd.to_datetime(raw["start_ts"]).dt.as_unit("us").dt.tz_localize("UTC")
    raw["cost"] = pd.to_numeric(raw["cost"], errors="coerce").astype("float64")
    for c in ("case_id", "activity", "ocel_event_id"):
        raw[c] = raw[c].astype(str)
    raw["source_row"] = raw["source_row"].where(raw["source_row"].notna(), raw["position"] + 1).astype("int64")
    raw = raw.sort_values(["case_id", "ts", "position"], kind="mergesort").reset_index(drop=True)
    raw["seq"] = raw.groupby("case_id", sort=False).cumcount().astype("int64")
    raw["event_id"] = raw["ocel_event_id"].where(~raw["id_generated"].astype(bool), None)
    raw["case_status"] = _status_per_row(db, log_id, object_type, raw)
    return raw[EVENT_COLUMNS]


def _status_per_row(db: Session, log_id: int, object_type: str, ev: pd.DataFrame) -> pd.Series:
    """The object's ``status`` attribute values placed on its event rows.

    A value reported by an event row (CSV/XES import) goes to that row; a value without a
    reporting event (OCEL attribute history) goes to the object's last event at or before its
    time (else its first event). The completion rule "case status" uses the last value."""
    out = pd.Series([None] * len(ev), index=ev.index, dtype=object)
    stmt = (select(OcelObjectAttribute.object_id, OcelObjectAttribute.value, OcelObjectAttribute.ts,
                   OcelObjectAttribute.event_id, OcelObjectAttribute.id)
            .join(OcelObject, (OcelObject.log_id == OcelObjectAttribute.log_id)
                  & (OcelObject.object_id == OcelObjectAttribute.object_id))
            .where(OcelObjectAttribute.log_id == log_id, OcelObjectAttribute.name == STATUS_ATTRIBUTE,
                   OcelObject.object_type == object_type)
            .order_by(OcelObjectAttribute.ts, OcelObjectAttribute.id))
    attrs = pd.DataFrame.from_records(db.execute(stmt).all(), columns=["case_id", "value", "ts", "event_id", "id"])
    if attrs.empty or ev.empty:
        return out
    attrs = attrs[attrs["value"].notna() & (attrs["value"] != "")]
    linked = attrs[attrs["event_id"].notna()].astype({"case_id": str, "event_id": str})
    if not linked.empty:
        hit = linked.merge(ev[["case_id", "ocel_event_id"]].reset_index(),
                           left_on=["case_id", "event_id"], right_on=["case_id", "ocel_event_id"])
        hit = hit.drop_duplicates("index", keep="last")  # attrs are ordered by time, then insertion
        out.iloc[hit["index"].to_numpy()] = hit["value"].to_numpy()
    free = attrs[attrs["event_id"].isna()].copy()
    if not free.empty:
        free["case_id"] = free["case_id"].astype(str)
        free["ts"] = pd.to_datetime(free["ts"]).dt.as_unit("us").dt.tz_localize("UTC")
        free = free.sort_values(["ts", "id"], kind="mergesort")
        rows = ev[["case_id", "ts"]].reset_index().sort_values("ts", kind="mergesort")
        hit = pd.merge_asof(free, rows, on="ts", by="case_id", direction="backward")
        first = rows.groupby("case_id")["index"].min()
        hit["index"] = hit["index"].fillna(hit["case_id"].map(first))
        for idx, value in zip(hit["index"], hit["value"]):  # later values overwrite earlier ones
            if pd.notna(idx):
                out.iloc[int(idx)] = value
    return out


def build_event_log(dataset: Dataset | TypeSettings, events: pd.DataFrame, object_type: str = "case") -> EventLog:
    ev = events
    n = len(ev)
    same_case_prev = np.zeros(n, dtype=bool)
    if n:
        cid = ev["case_id"].to_numpy()
        same_case_prev[1:] = cid[1:] == cid[:-1]
    prev_ts = ev["ts"].shift(1)
    ev["elapsed_s"] = np.where(same_case_prev, (ev["ts"] - prev_ts).dt.total_seconds(), np.nan)
    ev["prev_activity"] = ev["activity"].shift(1).where(same_case_prev, None)
    ev["exec_s"] = (ev["ts"] - ev["start_ts"]).dt.total_seconds()  # NaN without start timestamp

    g = ev.groupby("case_id", sort=False)
    seqs = g["activity"].agg(tuple)
    cases = pd.DataFrame({
        "start": g["ts"].first(),
        "end": g["ts"].last(),
        "n_events": g.size(),
        "n_unique": g["activity"].nunique(),
        "first_activity": g["activity"].first(),
        "last_activity": g["activity"].last(),
    })
    cases["duration_s"] = (cases["end"] - cases["start"]).dt.total_seconds()
    cases["repeated"] = (cases["n_events"] - cases["n_unique"]).astype("int64")
    cases["rework"] = cases["repeated"] > 0
    total_cost = g["cost"].sum(min_count=1)
    cases["total_cost"] = total_cost

    unique_seqs = pd.unique(seqs.to_numpy()) if len(seqs) else []
    vid_map = {s: variant_id_for(s) for s in unique_seqs}
    cases["variant_id"] = seqs.map(vid_map)
    variants = pd.DataFrame(
        {"activities": list(vid_map.keys())}, index=pd.Index(list(vid_map.values()), name="variant_id")
    )
    variants["n_activities"] = variants["activities"].map(len)
    variants["repeated"] = variants["activities"].map(lambda t: len(t) - len(set(t)))
    variants["label"] = variants["activities"].map(VARIANT_SEP.join)

    # Completion status
    mode = dataset.completion_mode or "none"
    if mode == "end_activity":
        ends = set(dataset.completion_activities or [])
        status = np.where(cases["last_activity"].isin(ends), "completed", "incomplete")
    elif mode == "case_status":
        last_status = g["case_status"].last()  # GroupBy.last skips missing values
        done = set(dataset.completed_status_values or [])
        open_ = set(dataset.open_status_values or [])
        status = np.where(last_status.isin(done), "completed",
                          np.where(last_status.isin(open_), "incomplete", "unknown"))
    else:
        status = np.full(len(cases), "unknown", dtype=object)
    cases["status"] = pd.Series(status, index=cases.index, dtype=object)

    if dataset.reference_time is not None:
        ref = pd.Timestamp(dataset.reference_time).tz_localize("UTC")
        ref_src = "configured"
    elif n:
        ref = ev["ts"].max()
        ref_src = "latest_event"
    else:
        ref, ref_src = None, "none"
    if ref is not None:
        cases["age_s"] = np.where(cases["status"] != "completed", (ref - cases["start"]).dt.total_seconds(), np.nan)
    else:
        cases["age_s"] = np.nan
    cases.index.name = "case_id"

    return EventLog(
        dataset_id=dataset.id, version=dataset.version, settings_revision=dataset.settings_revision,
        events=ev, cases=cases, variants=variants, display_tz=dataset.display_timezone or "UTC",
        reference_time=ref, reference_time_source=ref_src, completion_mode=mode,
        has_start_ts=bool(ev["start_ts"].notna().any()) if n else False,
        has_resource=bool(ev["resource"].notna().any()) if n else False,
        has_department=bool(ev["department"].notna().any()) if n else False,
        has_cost=bool(ev["cost"].notna().any()) if n else False,
        has_case_status=bool(ev["case_status"].notna().any()) if n else False,
        object_type=object_type,
    )


def get_event_log(db: Session, dataset: Dataset, object_type: str | None = None) -> EventLog:
    """The dataset seen through ``object_type`` (default: the dataset's main object type)."""
    ot = object_type or dataset.main_object_type
    key = (dataset.id, dataset.version, dataset.settings_revision, "eventlog", ot)
    return cache.get_or_compute(key, lambda: build_event_log(
        settings_for(dataset, ot), _load_events(db, dataset.ocel_log_id, ot) if dataset.ocel_log_id
        else pd.DataFrame(columns=EVENT_COLUMNS), ot))
