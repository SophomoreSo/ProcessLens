"""Case-level filtering shared by all analyses (see app/schemas/filters.py)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.schemas.filters import END_MARKER, START_MARKER, FilterSpec
from app.services.cache import cache
from app.services.eventlog import EventLog

# Resolves a conformance rule id -> (rule revision, per-case result Series)
ConformanceLookup = Callable[[int], tuple[int, pd.Series]]


@dataclass
class FilteredView:
    log: EventLog
    spec: FilterSpec
    cases: pd.DataFrame
    events: pd.DataFrame
    cache_id: str = ""  # identifies the population for caching; defaults to the filter key

    def __post_init__(self):
        if not self.cache_id:
            self.cache_id = self.spec.cache_key()

    @property
    def case_count(self) -> int:
        return len(self.cases)

    @property
    def total_case_count(self) -> int:
        return len(self.log.cases)


def _local_midnight_utc(d: date, tz: str) -> pd.Timestamp:
    ts = pd.Timestamp(d).tz_localize(tz, nonexistent="shift_forward", ambiguous=True)
    return ts.tz_convert("UTC")


def _cases_with(events: pd.DataFrame, mask: pd.Series | np.ndarray) -> np.ndarray:
    return pd.unique(events.loc[mask, "case_id"].to_numpy())


def case_mask(log: EventLog, spec: FilterSpec, conformance: pd.Series | None = None) -> np.ndarray:
    cases, ev = log.cases, log.events
    mask = np.ones(len(cases), dtype=bool)
    idx = cases.index

    if spec.start_from:
        mask &= (cases["start"] >= _local_midnight_utc(spec.start_from, log.display_tz)).to_numpy()
    if spec.start_to:
        mask &= (cases["start"] < _local_midnight_utc(spec.start_to + timedelta(days=1), log.display_tz)).to_numpy()
    for act in spec.activity:
        mask &= idx.isin(_cases_with(ev, ev["activity"] == act))
    if spec.exclude_activity:
        mask &= ~idx.isin(_cases_with(ev, ev["activity"].isin(spec.exclude_activity)))
    if spec.resource:
        mask &= idx.isin(_cases_with(ev, ev["resource"].isin(spec.resource)))
    if spec.department:
        mask &= idx.isin(_cases_with(ev, ev["department"].isin(spec.department)))
    if spec.variant:
        mask &= cases["variant_id"].isin(spec.variant).to_numpy()
    if spec.min_duration_h is not None:
        mask &= (cases["duration_s"] >= spec.min_duration_h * 3600).to_numpy()
    if spec.max_duration_h is not None:
        mask &= (cases["duration_s"] <= spec.max_duration_h * 3600).to_numpy()
    if spec.rework is not None:
        mask &= (cases["rework"] == spec.rework).to_numpy()
    if spec.status:
        mask &= cases["status"].isin(spec.status).to_numpy()
    if spec.transition_from is not None and spec.transition_to is not None:
        a, b = spec.transition_from, spec.transition_to
        if a == START_MARKER and b == END_MARKER:
            mask &= False
        elif a == START_MARKER:
            mask &= (cases["first_activity"] == b).to_numpy()
        elif b == END_MARKER:
            mask &= (cases["last_activity"] == a).to_numpy()
        else:
            mask &= idx.isin(_cases_with(ev, (ev["prev_activity"] == a) & (ev["activity"] == b)))
    if conformance is not None and spec.conformance_result:
        res = conformance.reindex(idx)
        mask &= res.isin(spec.conformance_result).to_numpy()
    return mask


def apply_filters(log: EventLog, spec: FilterSpec, conformance_lookup: ConformanceLookup | None = None) -> FilteredView:
    norm = spec.normalized()
    conformance = None
    extra: tuple = ()
    if "conformance_rule" in norm and conformance_lookup is not None:
        revision, conformance = conformance_lookup(spec.conformance_rule)  # type: ignore[arg-type]
        extra = (spec.conformance_rule, revision)

    cache_id = spec.cache_key() + (f"#rule{extra[0]}r{extra[1]}" if extra else "")

    def compute() -> FilteredView:
        if not norm:
            return FilteredView(log, spec, log.cases, log.events, cache_id=cache_id)
        mask = case_mask(log, spec, conformance)
        cases = log.cases[mask]
        events = log.events[log.events["case_id"].isin(cases.index)]
        return FilteredView(log, spec, cases, events, cache_id=cache_id)

    return cache.get_or_compute((*log.key, "view", spec.cache_key(), extra), compute)


def intersect(view: FilteredView, other_spec: FilterSpec, conformance_lookup: ConformanceLookup | None = None) -> FilteredView:
    """Cases of ``view`` that also satisfy ``other_spec`` (used for cohorts)."""
    other = apply_filters(view.log, other_spec, conformance_lookup)
    keep = view.cases.index.intersection(other.cases.index, sort=False)
    cases = view.cases.loc[keep]
    events = view.log.events[view.log.events["case_id"].isin(keep)]
    return FilteredView(view.log, other_spec, cases, events,
                        cache_id=f"{view.cache_id}&{other.cache_id}")
