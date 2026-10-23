"""Process analytics over a :class:`FilteredView`.

All durations are seconds. Medians and p90 values use numpy's default linear
interpolation. "Elapsed" times are differences between consecutive event
timestamps in a case; they mix waiting and processing and are never labeled
as either. See docs/METRICS.md for the full definitions.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from app.i18n import t
from app.schemas.filters import END_MARKER, START_MARKER
from app.services.cache import cache
from app.services.eventlog import EventLog
from app.services.filtering import FilteredView

BOTTLENECK_MIN_CASES = 5


def duration_stats(values: Iterable[float] | pd.Series) -> dict:
    arr = np.asarray(pd.Series(values, dtype="float64").dropna(), dtype="float64")
    if arr.size == 0:
        return {"n": 0, "mean": None, "median": None, "p90": None, "min": None, "max": None}
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "p90": float(np.percentile(arr, 90)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def _iso(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).isoformat()


def _cached(view: FilteredView, name: str, fn, *extra):
    return cache.get_or_compute((*view.log.key, name, view.cache_id, *extra), fn)


def histogram(seconds: pd.Series, bins: int = 30, edges: np.ndarray | None = None) -> dict:
    hours = pd.Series(seconds, dtype="float64").dropna() / 3600.0
    if hours.empty:
        return {"unit": "hours", "edges": [], "counts": []}
    if edges is None:
        hi = float(hours.max())
        edges = np.linspace(0.0, hi if hi > 0 else 1.0, bins + 1)
    counts, edges = np.histogram(hours.clip(upper=edges[-1]), bins=edges)
    return {"unit": "hours", "edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


# --------------------------------------------------------------------------- overview
def status_counts(cases: pd.DataFrame) -> dict:
    vc = cases["status"].value_counts()
    return {k: int(vc.get(k, 0)) for k in ("completed", "incomplete", "unknown")}


def _unique_event_cost(ev: pd.DataFrame) -> float:
    """Total cost with every event counted once, however many objects of the lead type it relates to."""
    if "ocel_event_id" in ev:
        ev = ev.drop_duplicates("ocel_event_id")
    return float(ev["cost"].sum())


def overview(view: FilteredView) -> dict:
    def compute():
        cases, ev, log = view.cases, view.events, view.log
        n_cases = len(cases)
        completed = cases[cases["status"] == "completed"]
        not_completed = cases[cases["status"] != "completed"]
        starts_local = cases["start"].dt.tz_convert(log.display_tz).dt.date
        daily = starts_local.value_counts().sort_index()
        if len(daily):
            full = pd.date_range(daily.index.min(), daily.index.max(), freq="D").date
            daily = daily.reindex(full, fill_value=0)
        top = variants(view, limit=5)["items"]
        return {
            "case_count": n_cases,
            # unique events (an event shared by several objects of the lead type counts once) ...
            "event_count": int(ev["ocel_event_id"].nunique()) if "ocel_event_id" in ev else int(len(ev)),
            # ... and object-event rows (one per object of the lead type and event)
            "object_event_count": int(len(ev)),
            "activity_count": int(ev["activity"].nunique()),
            "total_case_count": view.total_case_count,
            "status_counts": status_counts(cases),
            "completion_mode": log.completion_mode,
            "observed_duration": duration_stats(cases["duration_s"]),
            "completed_throughput": duration_stats(completed["duration_s"]),
            "open_case_age": duration_stats(not_completed["age_s"]),
            "reference_time": _iso(log.reference_time),
            "reference_time_source": log.reference_time_source,
            "rework_case_count": int(cases["rework"].sum()),
            "rework_rate": float(cases["rework"].mean()) if n_cases else None,
            "repeated_activity_count": int(cases["repeated"].sum()),
            "variant_count": int(cases["variant_id"].nunique()),
            "daily_case_starts": {
                "timezone": log.display_tz,
                "dates": [d.isoformat() for d in daily.index],
                "counts": [int(c) for c in daily.to_numpy()],
            },
            "duration_distribution": {
                "observed_all_cases": histogram(cases["duration_s"]),
                "completed_throughput": histogram(completed["duration_s"]),
            },
            "top_variants": top,
            "bottleneck_candidates": transitions(view, min_cases=BOTTLENECK_MIN_CASES, sort="median", limit=5),
            "total_cost": _unique_event_cost(ev) if log.has_cost else None,
        }

    return _cached(view, "overview", compute)


# --------------------------------------------------------------------------- DFG
def _edge_frame(view: FilteredView) -> pd.DataFrame:
    ev = view.events
    m = ev["prev_activity"].notna()
    return pd.DataFrame({
        "source": ev.loc[m, "prev_activity"].astype(str),
        "target": ev.loc[m, "activity"],
        "case_id": ev.loc[m, "case_id"],
        "elapsed_s": ev.loc[m, "elapsed_s"],
    })


def _stats_by(frame: pd.DataFrame, keys: list[str], value: str) -> pd.DataFrame:
    g = frame.groupby(keys, sort=False)[value]
    out = pd.DataFrame({
        "mean": g.mean(),
        "median": g.median(),
        "p90": g.quantile(0.9),
    })
    return out


def process_graph(view: FilteredView) -> dict:
    """Directly-follows graph with synthetic start/end markers."""

    def compute():
        ev, cases = view.events, view.cases
        edges = _edge_frame(view)
        nodes_out = []
        if len(ev):
            ng = ev.groupby("activity", sort=False)
            node_df = pd.DataFrame({"occurrences": ng.size(), "case_count": ng["case_id"].nunique()})
            exec_stats = None
            if view.log.has_start_ts:
                exec_stats = _stats_by(ev[ev["exec_s"].notna()], ["activity"], "exec_s")
                exec_n = ev[ev["exec_s"].notna()].groupby("activity").size()
            for act, row in node_df.sort_values(["occurrences"], ascending=False).iterrows():
                item = {"id": act, "label": act, "occurrences": int(row["occurrences"]),
                        "case_count": int(row["case_count"]), "execution": None}
                if exec_stats is not None and act in exec_stats.index:
                    s = exec_stats.loc[act]
                    item["execution"] = {"n": int(exec_n.get(act, 0)), "mean": float(s["mean"]),
                                         "median": float(s["median"]), "p90": float(s["p90"])}
                nodes_out.append(item)

        edges_out = []
        if len(edges):
            eg = edges.groupby(["source", "target"], sort=False)
            edf = pd.DataFrame({"occurrences": eg.size(), "case_count": eg["case_id"].nunique()})
            edf = edf.join(_stats_by(edges, ["source", "target"], "elapsed_s"))
            for (s, t), row in edf.sort_values("occurrences", ascending=False).iterrows():
                edges_out.append({
                    "source": s, "target": t, "occurrences": int(row["occurrences"]),
                    "case_count": int(row["case_count"]),
                    "elapsed": {"mean": float(row["mean"]), "median": float(row["median"]), "p90": float(row["p90"])},
                })
        for act, cnt in cases["first_activity"].value_counts().items():
            edges_out.append({"source": START_MARKER, "target": act, "occurrences": int(cnt), "case_count": int(cnt),
                              "elapsed": None})
        for act, cnt in cases["last_activity"].value_counts().items():
            edges_out.append({"source": act, "target": END_MARKER, "occurrences": int(cnt), "case_count": int(cnt),
                              "elapsed": None})
        return {
            "kind": "directly_follows_graph",
            "case_count": len(cases),
            "event_count": int(len(ev)),
            "nodes": nodes_out,
            "edges": edges_out,
            "markers": {"start": START_MARKER, "end": END_MARKER},
            "note_keys": ["note.dfg.edge", "note.dfg.not_bpmn", "note.dfg.elapsed"],
        }

    return _cached(view, "graph", compute)


def transitions(view: FilteredView, min_cases: int = 1, sort: str = "median", limit: int | None = None) -> list[dict]:
    def compute():
        edges = _edge_frame(view)
        if edges.empty:
            return []
        eg = edges.groupby(["source", "target"], sort=False)
        df = pd.DataFrame({"occurrences": eg.size(), "case_count": eg["case_id"].nunique()})
        df = df.join(_stats_by(edges, ["source", "target"], "elapsed_s")).reset_index()
        return df.to_dict(orient="records")

    rows = _cached(view, "transitions", compute)
    rows = [r for r in rows if r["case_count"] >= min_cases]
    key = sort if sort in ("median", "p90", "mean", "occurrences", "case_count") else "median"
    rows = sorted(rows, key=lambda r: (-r[key], r["source"], r["target"]))
    out = [{"source": r["source"], "target": r["target"], "occurrences": int(r["occurrences"]),
            "case_count": int(r["case_count"]),
            "elapsed": {"mean": float(r["mean"]), "median": float(r["median"]), "p90": float(r["p90"])}} for r in rows]
    return out[:limit] if limit else out


# --------------------------------------------------------------------------- variants
def variants(view: FilteredView, limit: int | None = None, offset: int = 0) -> dict:
    def compute():
        cases = view.cases
        n = len(cases)
        if n == 0:
            return []
        g = cases.assign(is_completed=cases["status"] == "completed").groupby("variant_id", sort=False)
        df = pd.DataFrame({
            "case_count": g.size(),
            "median": g["duration_s"].median(),
            "p90": g["duration_s"].quantile(0.9),
            "mean": g["duration_s"].mean(),
            "completed": g["is_completed"].sum(),
        })
        df = df.join(view.log.variants[["activities", "label", "n_activities", "repeated"]])
        df = df.reset_index().sort_values(["case_count", "label"], ascending=[False, True], kind="mergesort")
        df["share"] = df["case_count"] / n
        df["cumulative_share"] = df["share"].cumsum()
        rows = []
        for rank, rec in enumerate(df.to_dict(orient="records"), start=1):
            rows.append({
                "variant_id": rec["variant_id"], "rank": rank, "activities": list(rec["activities"]),
                "label": rec["label"], "n_activities": int(rec["n_activities"]),
                "case_count": int(rec["case_count"]), "share": float(rec["share"]),
                "cumulative_share": float(min(rec["cumulative_share"], 1.0)),
                "observed_duration": {"median": float(rec["median"]), "p90": float(rec["p90"]), "mean": float(rec["mean"])},
                "completed_cases": int(rec["completed"]),
                "has_rework": rec["repeated"] > 0, "repeated_activities": int(rec["repeated"]),
            })
        return rows

    rows = _cached(view, "variants", compute)
    sliced = rows[offset: offset + limit] if limit else rows[offset:]
    return {"total": len(rows), "case_count": view.case_count, "items": sliced}


# --------------------------------------------------------------------------- cases
CASE_SORTS = {"case_id": "case_id", "start": "start", "end": "end", "duration": "duration_s",
              "events": "n_events", "status": "status", "rework": "repeated", "cost": "total_cost"}


def case_rows(view: FilteredView, page: int, page_size: int, sort: str, order: str) -> dict:
    col = CASE_SORTS.get(sort, "start")
    cases = view.cases.reset_index()
    asc = order != "desc"
    cases = cases.sort_values([col, "case_id"], ascending=[asc, True], kind="mergesort", na_position="last")
    total = len(cases)
    chunk = cases.iloc[(page - 1) * page_size: page * page_size]
    labels = view.log.variants["label"]
    ranks = {v["variant_id"]: v["rank"] for v in variants(view)["items"]}
    items = [case_summary(rec, labels, ranks) for rec in chunk.to_dict(orient="records")]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def case_summary(rec: dict, labels: pd.Series, ranks: dict | None = None) -> dict:
    return {
        "case_id": rec["case_id"],
        "start": _iso(rec["start"]),
        "end": _iso(rec["end"]),
        "n_events": int(rec["n_events"]),
        "observed_duration_s": float(rec["duration_s"]),
        "status": rec["status"],
        "open_case_age_s": None if pd.isna(rec["age_s"]) else float(rec["age_s"]),
        "rework": bool(rec["rework"]),
        "repeated_activities": int(rec["repeated"]),
        "variant_id": rec["variant_id"],
        "variant_rank": (ranks or {}).get(rec["variant_id"]),
        "variant_label": labels.get(rec["variant_id"]),
        "first_activity": rec["first_activity"],
        "last_activity": rec["last_activity"],
        "total_cost": None if pd.isna(rec["total_cost"]) else float(rec["total_cost"]),
    }


# --------------------------------------------------------------------------- performance
def _participation_stats(view: FilteredView, column: str) -> list[dict]:
    ev, cases = view.events, view.cases
    sub = ev[ev[column].notna()]
    if sub.empty:
        return []
    g = sub.groupby(column, sort=False)
    df = pd.DataFrame({
        "events": g.size(),
        "cases": g["case_id"].nunique(),
        "elapsed_n": g["elapsed_s"].count(),
        "elapsed_median": g["elapsed_s"].median(),
        "elapsed_p90": g["elapsed_s"].quantile(0.9),
    })
    pairs = sub[["case_id", column]].drop_duplicates()
    pairs = pairs.join(cases[["duration_s", "rework"]], on="case_id")
    pg = pairs.groupby(column, sort=False)
    df["case_duration_median"] = pg["duration_s"].median()
    df["case_duration_p90"] = pg["duration_s"].quantile(0.9)
    df["rework_rate"] = pg["rework"].mean()
    df = df.sort_values(["events"], ascending=False)
    out = []
    for name, r in df.iterrows():
        out.append({
            "name": name, "events": int(r["events"]), "cases": int(r["cases"]),
            "elapsed_before_event": {"n": int(r["elapsed_n"]),
                                     "median": None if pd.isna(r["elapsed_median"]) else float(r["elapsed_median"]),
                                     "p90": None if pd.isna(r["elapsed_p90"]) else float(r["elapsed_p90"])},
            "participating_case_duration": {"n": int(r["cases"]), "median": float(r["case_duration_median"]),
                                            "p90": float(r["case_duration_p90"])},
            "participating_case_rework_rate": float(r["rework_rate"]),
        })
    return out


def performance(view: FilteredView) -> dict:
    def compute():
        ev = view.events
        activity_exec = []
        if view.log.has_start_ts:
            sub = ev[ev["exec_s"].notna()]
            for act, grp in sub.groupby("activity"):
                activity_exec.append({"activity": act, **duration_stats(grp["exec_s"])})
            activity_exec.sort(key=lambda r: -(r["median"] or 0))
        return {
            "case_count": view.case_count,
            "transitions": transitions(view, sort="median"),
            "resources": _participation_stats(view, "resource") if view.log.has_resource else [],
            "departments": _participation_stats(view, "department") if view.log.has_department else [],
            "activity_execution": activity_exec,
            "has_start_timestamps": view.log.has_start_ts,
            "note_keys": ["note.perf.transitions", "note.perf.participation", "note.perf.descriptive"],
        }

    return _cached(view, "performance", compute)


def cohort_metrics(view: FilteredView, edges: np.ndarray | None = None) -> dict:
    cases, ev = view.cases, view.events
    n = len(cases)
    completed = cases[cases["status"] == "completed"]
    acts = ev.groupby("activity")["case_id"].nunique() if len(ev) else pd.Series(dtype="int64")
    return {
        "case_count": n,
        "event_count": int(len(ev)),
        "events_per_case": float(len(ev) / n) if n else None,
        "observed_duration": duration_stats(cases["duration_s"]),
        "completed_throughput": duration_stats(completed["duration_s"]),
        "status_counts": status_counts(cases),
        "completion_rate": float(len(completed) / n) if n else None,
        "rework_rate": float(cases["rework"].mean()) if n else None,
        "variant_count": int(cases["variant_id"].nunique()),
        "activity_case_share": {a: float(c / n) for a, c in acts.items()} if n else {},
        "duration_histogram": histogram(cases["duration_s"], edges=edges),
        "total_cost": float(ev["cost"].sum()) if view.log.has_cost and n else None,
    }


def compare_cohorts(base: FilteredView, a: FilteredView, b: FilteredView) -> dict:
    both = pd.concat([a.cases["duration_s"], b.cases["duration_s"]]) / 3600.0
    hi = float(both.max()) if len(both) else 1.0
    edges = np.linspace(0.0, hi if hi > 0 else 1.0, 26)
    overlap = int(len(a.cases.index.intersection(b.cases.index)))
    return {
        "base_case_count": base.case_count,
        "a": cohort_metrics(a, edges),
        "b": cohort_metrics(b, edges),
        "overlapping_cases": overlap,
        "note_keys": ["note.cmp.population", "note.cmp.descriptive", "note.cmp.small"],
    }


def with_notes(data: dict, lang: str) -> dict:
    """Copy of a (possibly cached) result with its note keys rendered in ``lang``."""
    out = dict(data)
    out["notes"] = [t(lang, k) for k in data.get("note_keys", [])]
    return out


def log_capabilities(log: EventLog) -> dict:
    return {"has_start_timestamps": log.has_start_ts, "has_resource": log.has_resource,
            "has_department": log.has_department, "has_cost": log.has_cost, "has_case_status": log.has_case_status}
