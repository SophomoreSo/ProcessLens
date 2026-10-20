"""Loading a persisted dataset into an analysis-ready, cached event log.

Definitions implemented here (see docs/METRICS.md):

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

from app.models import Dataset, Event
from app.services.cache import cache

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

    @property
    def key(self) -> tuple:
        return (self.dataset_id, self.version, self.settings_revision)


def variant_id_for(activities: tuple[str, ...]) -> str:
    return hashlib.sha1("\x1f".join(activities).encode("utf-8")).hexdigest()[:12]


def _load_events(db: Session, dataset_id: int) -> pd.DataFrame:
    stmt = (
        select(Event.case_id, Event.activity, Event.ts, Event.start_ts, Event.resource, Event.department,
               Event.cost, Event.case_status, Event.event_id, Event.source_row, Event.seq)
        .where(Event.dataset_id == dataset_id)
    )
    rows = db.execute(stmt).all()
    cols = ["case_id", "activity", "ts", "start_ts", "resource", "department", "cost", "case_status",
            "event_id", "source_row", "seq"]
    df = pd.DataFrame.from_records(rows, columns=cols)
    df["ts"] = pd.to_datetime(df["ts"]).dt.as_unit("us").dt.tz_localize("UTC")
    df["start_ts"] = pd.to_datetime(df["start_ts"]).dt.as_unit("us").dt.tz_localize("UTC")
    df["cost"] = pd.to_numeric(df["cost"], errors="coerce").astype("float64")
    for c in ("case_id", "activity"):
        df[c] = df[c].astype(str)
    df = df.sort_values(["case_id", "seq"], kind="mergesort").reset_index(drop=True)
    return df


def build_event_log(dataset: Dataset, events: pd.DataFrame) -> EventLog:
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
    )


def get_event_log(db: Session, dataset: Dataset) -> EventLog:
    key = (dataset.id, dataset.version, dataset.settings_revision, "eventlog")
    return cache.get_or_compute(key, lambda: build_event_log(dataset, _load_events(db, dataset.id)))
