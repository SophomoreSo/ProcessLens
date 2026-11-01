"""Starter knowledge model and views for a new package, adapted to what the data contains."""

from __future__ import annotations

import math

from miner.data import duration_quantile_days
from miner.plql import Frame


def _round_nice(x: float) -> float:
    if x <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(x))
    for step in (1, 2, 5, 10):
        if x <= step * mag:
            return float(step * mag)
    return float(10 * mag)


def starter_content(frame: Frame) -> dict:
    """{"kpis": [...], "filters": [...], "views": [...]} in the exchange format."""
    log = frame.log
    has_status = log.completion_mode != "none"
    kpis = [
        {"key": "cases", "name": "Cases", "formula": "COUNT_CASES()", "format": "integer",
         "description": "Number of cases (objects of the lead type)."},
        {"key": "avg_throughput_d", "name": "Avg. throughput time", "formula": "AVG(case.duration_d)",
         "format": "days", "higher_is_better": False,
         "description": "Mean observed time from the first to the last event of a case."},
        {"key": "median_throughput_d", "name": "Median throughput time", "formula": "MEDIAN(case.duration_d)",
         "format": "days", "higher_is_better": False},
        {"key": "rework_rate", "name": "Rework rate", "formula": "SHARE(case.rework)", "format": "percent",
         "target": 0.1, "higher_is_better": False,
         "description": "Share of cases in which at least one activity is repeated."},
        {"key": "events_per_case", "name": "Events per case", "formula": "AVG(case.events)", "format": "number",
         "higher_is_better": False},
        {"key": "variants", "name": "Variants", "formula": "COUNT_DISTINCT(case.variant)", "format": "integer",
         "higher_is_better": False},
    ]
    if has_status:
        kpis.append({"key": "completion_rate", "name": "Completion rate",
                     "formula": 'SHARE(case.status = "completed")', "format": "percent"})
    if log.has_cost:
        kpis.append({"key": "cost_per_case", "name": "Cost per case", "formula": "AVG(case.cost)",
                     "format": "currency", "higher_is_better": False})

    slow = duration_quantile_days(frame, 0.75)
    slow_days = _round_nice(slow) if slow else 30.0
    filters = [
        {"key": "rework", "name": "Cases with rework", "formula": "case.rework"},
        {"key": "slow", "name": f"Slower than {slow_days:g} days", "formula": f"case.duration_d > {slow_days:g}",
         "description": "Threshold suggested from the 75th percentile of throughput times when the package was created."},
    ]
    if has_status:
        filters.insert(0, {"key": "completed", "name": "Completed cases", "formula": 'case.status = "completed"'})

    tiles = ["cases", "avg_throughput_d", "rework_rate", "completion_rate" if has_status else "variants"]
    overview = [
        {"id": f"k{i}", "type": "kpi", "title": "", "width": 3,
         "config": {"measure": {"formula": f'KPI("{key}")', "label": "", "format": "auto"}}}
        for i, key in enumerate(tiles, start=1)
    ]
    overview += [
        {"id": "trend", "type": "line", "title": "New cases and throughput time per month", "width": 8,
         "config": {"dimension": {"formula": "MONTH(case.start)", "label": "Month"},
                    "measures": [{"formula": 'KPI("cases")', "label": "Cases", "format": "integer"},
                                 {"formula": 'KPI("avg_throughput_d")', "label": "Avg. throughput (days)",
                                  "format": "days"}],
                    "limit": 120}},
        {"id": "status", "type": "pie", "title": "Cases by status" if has_status else "Cases by end activity",
         "width": 4,
         "config": {"dimension": {"formula": "case.status" if has_status else "case.last_activity", "label": ""},
                    "measures": [{"formula": 'KPI("cases")', "label": "Cases", "format": "integer"}],
                    "limit": 8}},
        {"id": "graph", "type": "process", "title": "Process", "width": 8, "config": {"max_activities": 15}},
        {"id": "acts", "type": "bar", "title": "Cases per activity", "width": 4,
         "config": {"dimension": {"formula": "event.activity", "label": "Activity"},
                    "measures": [{"formula": "COUNT_CASES()", "label": "Cases", "format": "integer"}],
                    "limit": 15}},
        {"id": "variants", "type": "variants", "title": "Top variants", "width": 12, "config": {"limit": 8}},
    ]
    if log.has_resource:
        overview.append(
            {"id": "resources", "type": "table", "title": "Resources", "width": 12,
             "config": {"dimensions": [{"formula": "event.resource", "label": "Resource"}],
                        "measures": [{"formula": "COUNT_EVENTS()", "label": "Events", "format": "integer"},
                                     {"formula": "COUNT_CASES()", "label": "Cases", "format": "integer"},
                                     {"formula": "AVG(event.elapsed_h)", "label": "Avg. wait before (h)",
                                      "format": "hours"},
                                     {"formula": "SHARE(case.rework)", "label": "Cases with rework",
                                      "format": "percent"}],
                        "limit": 25}})

    bucket = _round_nice(max(slow_days / 5, 0.1))
    throughput = [
        {"id": "t1", "type": "kpi", "title": "", "width": 4,
         "config": {"measure": {"formula": 'KPI("median_throughput_d")', "label": "", "format": "auto"}}},
        {"id": "t2", "type": "kpi", "title": "90th percentile", "width": 4,
         "config": {"measure": {"formula": "PERCENTILE(case.duration_d, 0.9)", "label": "", "format": "days"}}},
        {"id": "t3", "type": "kpi", "title": "Slow cases", "width": 4,
         "config": {"measure": {"formula": 'SHARE(FILTER("slow"))', "label": "", "format": "percent"}}},
        {"id": "hist", "type": "bar", "title": f"Throughput time distribution ({bucket:g}-day buckets)", "width": 12,
         "config": {"dimension": {"formula": f"BUCKET(case.duration_d, {bucket:g})", "label": "Days (from)"},
                    "measures": [{"formula": "COUNT_CASES()", "label": "Cases", "format": "integer"}],
                    "limit": 60, "orientation": "v"}},
        {"id": "byfirst", "type": "table", "title": "Throughput by variant", "width": 12,
         "config": {"dimensions": [{"formula": "case.variant", "label": "Variant"}],
                    "measures": [{"formula": "COUNT_CASES()", "label": "Cases", "format": "integer"},
                                 {"formula": "MEDIAN(case.duration_d)", "label": "Median days", "format": "days"},
                                 {"formula": "PERCENTILE(case.duration_d, 0.9)", "label": "P90 days",
                                  "format": "days"}],
                    "limit": 20}},
        {"id": "rework_trend", "type": "line", "title": "Rework rate per month", "width": 12,
         "config": {"dimension": {"formula": "MONTH(case.start)", "label": "Month"},
                    "measures": [{"formula": 'KPI("rework_rate")', "label": "Rework rate", "format": "percent"}],
                    "limit": 120}},
    ]
    views = [
        {"name": "Process overview", "description": "Volume, throughput, flow and variants at a glance.",
         "filter": "", "components": overview},
        {"name": "Throughput analysis", "description": "Where the time goes.", "filter": "",
         "components": throughput},
    ]
    return {"kpis": kpis, "filters": filters, "views": views}
