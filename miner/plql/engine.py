"""PLQL evaluation over a ProcessLens event log (one lead object type = the "cases").

Two tables are visible to formulas:

* ``case.<column>`` — one row per case (object of the package's lead object type);
* ``event.<column>`` — one row per (case, event).

Every expression has a **level**: constant, case or event. Row expressions of
different levels can be combined; a case value used next to event values is
repeated on each of the case's events. **Aggregations** (``COUNT_CASES``,
``AVG``, ``SHARE``, …) collapse rows into one value per group of the query's
dimensions:

* grouping by case-level dimensions: case values are aggregated per group,
  event values over all events of the group's cases;
* grouping by an event-level dimension (e.g. ``event.activity``): event values
  are aggregated per group, case values over the *distinct* cases that have at
  least one event in the group (each case counted once per group);
* no dimensions: one group with every case.

Filters are case-level (matching cases keep their complete histories, as in
ProcessLens): an event-level condition keeps the cases with at least one
matching event. Missing values are skipped by aggregations, compare as false,
and propagate through arithmetic.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.services.eventlog import EventLog
from miner.plql.parser import Binary, Call, Column, InList, Literal, Node, PlqlError, Unary, parse

CONST, CASE, EVENT = 0, 1, 2
LEVEL_NAMES = {CONST: "constant", CASE: "case", EVENT: "event"}

# --------------------------------------------------------------------------- catalog
# (type, description) — the type is one of number, text, datetime, bool.
CASE_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("text", "Case (object) id"),
    "start": ("datetime", "Timestamp of the first event"),
    "end": ("datetime", "Timestamp of the last event"),
    "duration_s": ("number", "Observed throughput time (last - first event) in seconds"),
    "duration_h": ("number", "Observed throughput time in hours"),
    "duration_d": ("number", "Observed throughput time in days"),
    "events": ("number", "Number of events of the case"),
    "activities": ("number", "Number of distinct activities of the case"),
    "first_activity": ("text", "Activity of the first event"),
    "last_activity": ("text", "Activity of the last event"),
    "variant": ("text", "Process variant (activity sequence)"),
    "variant_id": ("text", "Stable id of the variant"),
    "status": ("text", "Completion status: completed, incomplete or unknown"),
    "rework": ("bool", "True if any activity occurs more than once"),
    "repeated": ("number", "Number of repeated activity occurrences"),
    "cost": ("number", "Sum of event costs (missing if no event has a cost)"),
    "age_d": ("number", "Age of an open case in days at the reference time (missing for completed cases)"),
}
EVENT_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("text", "Event id"),
    "case_id": ("text", "Id of the case the event belongs to"),
    "activity": ("text", "Activity (event type)"),
    "ts": ("datetime", "Event (completion) timestamp"),
    "start_ts": ("datetime", "Start timestamp, if imported"),
    "resource": ("text", "Resource"),
    "department": ("text", "Department"),
    "cost": ("number", "Event cost"),
    "seq": ("number", "Position of the event in its case (0 = first)"),
    "prev_activity": ("text", "Activity of the previous event of the case"),
    "elapsed_h": ("number", "Hours since the previous event of the case"),
    "elapsed_d": ("number", "Days since the previous event of the case"),
    "exec_h": ("number", "Execution time in hours (needs a start timestamp)"),
    "status": ("text", "Status value reported with the event"),
}

AGGREGATIONS: dict[str, tuple[int, int, str, str]] = {
    # name: (min args, max args, signature, description)
    "COUNT_CASES": (0, 1, "COUNT_CASES([condition])", "Number of distinct cases (that meet the condition)"),
    "COUNT_EVENTS": (0, 1, "COUNT_EVENTS([condition])", "Number of events (that meet the condition)"),
    "COUNT": (1, 1, "COUNT(x)", "Number of non-missing values"),
    "COUNT_DISTINCT": (1, 1, "COUNT_DISTINCT(x)", "Number of distinct non-missing values"),
    "SUM": (1, 1, "SUM(x)", "Sum"),
    "AVG": (1, 1, "AVG(x)", "Mean"),
    "MEDIAN": (1, 1, "MEDIAN(x)", "Median"),
    "MIN": (1, 1, "MIN(x)", "Minimum (numbers, text or timestamps)"),
    "MAX": (1, 1, "MAX(x)", "Maximum (numbers, text or timestamps)"),
    "STDEV": (1, 1, "STDEV(x)", "Sample standard deviation"),
    "PERCENTILE": (2, 2, "PERCENTILE(x, p)", "p-quantile, 0 <= p <= 1 (linear interpolation)"),
    "SHARE": (1, 1, "SHARE(condition)",
              "Share of cases meeting a case condition, or of events meeting an event condition (0..1)"),
}
CASE_FUNCTIONS: dict[str, tuple[int, int, str, str]] = {
    "CONTAINS": (1, 50, 'CONTAINS("A"[, "B", ...])', "True if the case has at least one of the activities"),
    "COUNT_ACT": (1, 1, 'COUNT_ACT("A")', "How often the activity occurs in the case"),
    "THROUGHPUT": (2, 3, 'THROUGHPUT("A", "B"[, "d"])',
                   "Time from the first A to the first B at or after it (unit s, min, h, d or w; default d)"),
    "CASE_COUNT": (0, 1, "CASE_COUNT([event condition])", "Number of the case's events (that meet the condition)"),
    "CASE_SUM": (1, 1, "CASE_SUM(event value)", "Sum over the case's events"),
    "CASE_AVG": (1, 1, "CASE_AVG(event value)", "Mean over the case's events"),
    "CASE_MIN": (1, 1, "CASE_MIN(event value)", "Minimum over the case's events"),
    "CASE_MAX": (1, 1, "CASE_MAX(event value)", "Maximum over the case's events"),
    "CASE_FIRST": (1, 1, "CASE_FIRST(event value)", "Value at the case's first event (non-missing)"),
    "CASE_LAST": (1, 1, "CASE_LAST(event value)", "Value at the case's last event (non-missing)"),
    "CASE_COUNT_DISTINCT": (1, 1, "CASE_COUNT_DISTINCT(event value)", "Distinct values over the case's events"),
}
ROW_FUNCTIONS: dict[str, tuple[int, int, str, str]] = {
    "IF": (3, 3, "IF(condition, then, else)", "Conditional value"),
    "COALESCE": (2, 20, "COALESCE(a, b, ...)", "First non-missing value"),
    "ISNULL": (1, 1, "ISNULL(x)", "True if the value is missing"),
    "ROUND": (1, 2, "ROUND(x[, digits])", "Round a number"),
    "ABS": (1, 1, "ABS(x)", "Absolute value"),
    "BUCKET": (2, 2, "BUCKET(x, size)", "Lower bound of the size-wide bucket x falls into (for histograms)"),
    "CONCAT": (2, 20, "CONCAT(a, b, ...)", "Join values as text"),
    "LOWER": (1, 1, "LOWER(text)", "Lower-case text"),
    "UPPER": (1, 1, "UPPER(text)", "Upper-case text"),
    "YEAR": (1, 1, "YEAR(ts)", "Calendar year in the dataset's display timezone"),
    "QUARTER": (1, 1, "QUARTER(ts)", 'Quarter, e.g. "2024-Q1"'),
    "MONTH": (1, 1, "MONTH(ts)", 'Month, e.g. "2024-03"'),
    "WEEK": (1, 1, "WEEK(ts)", 'ISO week, e.g. "2024-W09"'),
    "DAY": (1, 1, "DAY(ts)", 'Calendar day, e.g. "2024-03-14"'),
    "WEEKDAY": (1, 1, "WEEKDAY(ts)", 'Day of the week, "1 Mon" … "7 Sun"'),
    "HOUR": (1, 1, "HOUR(ts)", "Hour of the day (0-23)"),
    "DAYS_BETWEEN": (2, 2, "DAYS_BETWEEN(a, b)", "Days from timestamp a to timestamp b"),
    "HOURS_BETWEEN": (2, 2, "HOURS_BETWEEN(a, b)", "Hours from timestamp a to timestamp b"),
}
REFERENCES: dict[str, tuple[int, int, str, str]] = {
    "KPI": (1, 1, 'KPI("key")', "A KPI of the package's knowledge model"),
    "FILTER": (1, 1, 'FILTER("key")', "A saved filter (condition) of the package's knowledge model"),
}
_UNITS = {"s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}
_TIME_FUNCS = {"YEAR", "QUARTER", "MONTH", "WEEK", "DAY", "WEEKDAY", "HOUR"}


def catalog() -> dict:
    """Reference shown in the editors."""
    def fn(group):
        return [{"name": k, "signature": v[2], "description": v[3]} for k, v in group.items()]
    return {
        "case_columns": [{"name": f"case.{k}", "type": t, "description": d} for k, (t, d) in CASE_COLUMNS.items()],
        "event_columns": [{"name": f"event.{k}", "type": t, "description": d} for k, (t, d) in EVENT_COLUMNS.items()],
        "aggregations": fn(AGGREGATIONS),
        "case_functions": fn(CASE_FUNCTIONS),
        "row_functions": fn(ROW_FUNCTIONS),
        "references": fn(REFERENCES),
        "operators": ["+ - * / %", "= != < <= > >=", "AND OR NOT", "x IN (a, b)", "x NOT IN (a, b)"],
    }


# --------------------------------------------------------------------------- references and levels
@dataclass
class KnowledgeModel:
    """KPI and filter formulas by key, for ``KPI("key")`` and ``FILTER("key")``."""

    kpis: Mapping[str, str] = field(default_factory=dict)
    filters: Mapping[str, str] = field(default_factory=dict)


def _check_arity(node: Call, spec: tuple[int, int, str, str]) -> None:
    lo, hi, sig, _ = spec
    if not lo <= len(node.args) <= hi:
        raise PlqlError(f"{node.name} takes {'no' if hi == 0 else ''}{'' if hi == 0 else _count(lo, hi)} "
                        f"argument{'s' if hi != 1 else ''}: {sig}", node.pos)


def _count(lo: int, hi: int) -> str:
    return str(lo) if lo == hi else f"{lo} to {hi}" if hi < 50 else f"at least {lo}"


def expand(node: Node, km: KnowledgeModel, stack: tuple[str, ...] = ()) -> Node:
    """Replace ``KPI("x")`` / ``FILTER("x")`` by the referenced formulas (cycles are errors)."""
    if isinstance(node, Call):
        if node.name in REFERENCES:
            _check_arity(node, REFERENCES[node.name])
            arg = node.args[0]
            if not (isinstance(arg, Literal) and isinstance(arg.value, str)):
                raise PlqlError(f'{node.name} needs a key in quotes, e.g. {node.name}("my_key")', node.pos)
            source = km.kpis if node.name == "KPI" else km.filters
            label = f"{node.name}({arg.value!r})"
            if arg.value not in source:
                raise PlqlError(f"{label} does not exist in this knowledge model", arg.pos)
            if label in stack:
                raise PlqlError(f"{label} refers to itself: {' → '.join((*stack, label))}", node.pos)
            try:
                inner = parse(source[arg.value])
            except PlqlError as exc:
                raise PlqlError(f"{label} has an error: {exc.message}", node.pos) from None
            try:
                return expand(inner, km, (*stack, label))
            except PlqlError as exc:
                if exc.message.startswith(f"{label} "):
                    raise
                raise PlqlError(f"{label}: {exc.message}", node.pos) from None
        return Call(node.pos, node.name, tuple(expand(a, km, stack) for a in node.args))
    if isinstance(node, Unary):
        return Unary(node.pos, node.op, expand(node.operand, km, stack))
    if isinstance(node, Binary):
        return Binary(node.pos, node.op, expand(node.left, km, stack), expand(node.right, km, stack))
    if isinstance(node, InList):
        return InList(node.pos, expand(node.operand, km, stack), tuple(expand(i, km, stack) for i in node.items),
                      node.negated)
    return node


def has_aggregation(node: Node) -> bool:
    if isinstance(node, Call):
        return node.name in AGGREGATIONS or any(has_aggregation(a) for a in node.args)
    if isinstance(node, Unary):
        return has_aggregation(node.operand)
    if isinstance(node, Binary):
        return has_aggregation(node.left) or has_aggregation(node.right)
    if isinstance(node, InList):
        return has_aggregation(node.operand) or any(has_aggregation(i) for i in node.items)
    return False


def row_level(node: Node) -> int:
    """Level of a row expression (raises for aggregations and unknown names)."""
    if isinstance(node, Literal):
        return CONST
    if isinstance(node, Column):
        if node.table == "case":
            if node.name not in CASE_COLUMNS:
                raise PlqlError(f"Unknown case column case.{node.name}", node.pos)
            return CASE
        if node.table == "event":
            if node.name not in EVENT_COLUMNS:
                raise PlqlError(f"Unknown event column event.{node.name}", node.pos)
            return EVENT
        raise PlqlError(f"Unknown table {node.table!r}: use case.<column> or event.<column>", node.pos)
    if isinstance(node, Unary):
        return row_level(node.operand)
    if isinstance(node, Binary):
        return max(row_level(node.left), row_level(node.right))
    if isinstance(node, InList):
        return max([row_level(node.operand), *(row_level(i) for i in node.items)])
    if isinstance(node, Call):
        if node.name in AGGREGATIONS:
            raise PlqlError(f"{node.name} is an aggregation and cannot be used here (a value per row is needed)",
                            node.pos)
        if node.name in CASE_FUNCTIONS:
            _check_arity(node, CASE_FUNCTIONS[node.name])
            if node.name in ("CONTAINS", "COUNT_ACT", "THROUGHPUT"):
                for a in node.args:
                    if row_level(a) != CONST:
                        raise PlqlError(f"{node.name} needs fixed values (e.g. activity names in quotes)", a.pos)
            else:
                for a in node.args:
                    if row_level(a) == CASE and node.name != "CASE_COUNT":
                        raise PlqlError(f"{node.name} aggregates event values; this argument is a case value", a.pos)
                    row_level(a)
            return CASE
        if node.name in ROW_FUNCTIONS:
            _check_arity(node, ROW_FUNCTIONS[node.name])
            return max([CONST, *(row_level(a) for a in node.args)])
        if node.name in REFERENCES:
            raise PlqlError(f"{node.name} references must be expanded first", node.pos)
        raise PlqlError(f"Unknown function {node.name}", node.pos)
    raise PlqlError("Unsupported expression", getattr(node, "pos", None))


def check_measure(node: Node) -> None:
    """A measure: aggregations combined with constants and row functions; no bare columns."""
    if isinstance(node, Call) and node.name in AGGREGATIONS:
        _check_arity(node, AGGREGATIONS[node.name])
        for a in node.args:
            if has_aggregation(a):
                raise PlqlError("Aggregations cannot be nested", a.pos)
            row_level(a)
        if node.name == "PERCENTILE":
            p = node.args[1]
            if not (isinstance(p, Literal) and isinstance(p.value, (int, float)) and 0 <= p.value <= 1):
                raise PlqlError("PERCENTILE needs a fixed p between 0 and 1, e.g. PERCENTILE(x, 0.9)", p.pos)
        if node.name == "SHARE" and row_level(node.args[0]) == CONST:
            raise PlqlError("SHARE needs a condition on case or event values", node.pos)
        return
    if isinstance(node, Column):
        raise PlqlError(f"{node.table}.{node.name} must be inside an aggregation here, e.g. AVG({node.table}.{node.name})",
                        node.pos)
    if isinstance(node, Call):
        if node.name in CASE_FUNCTIONS:
            raise PlqlError(f"{node.name} gives a value per case; wrap it in an aggregation, e.g. AVG(...) or "
                            "SHARE(...)", node.pos)
        if node.name not in ROW_FUNCTIONS:
            row_level(node)  # raises the right error
        _check_arity(node, ROW_FUNCTIONS[node.name])
        for a in node.args:
            check_measure(a)
    elif isinstance(node, Unary):
        check_measure(node.operand)
    elif isinstance(node, Binary):
        check_measure(node.left)
        check_measure(node.right)
    elif isinstance(node, InList):
        check_measure(node.operand)
        for i in node.items:
            check_measure(i)


def compile_row(formula: str, km: KnowledgeModel) -> tuple[Node, int]:
    node = expand(parse(formula), km)
    if has_aggregation(node):
        _raise_first_aggregation(node)
    return node, row_level(node)


def compile_measure(formula: str, km: KnowledgeModel) -> Node:
    node = expand(parse(formula), km)
    check_measure(node)
    if not has_aggregation(node):
        raise PlqlError("A KPI or measure needs an aggregation, e.g. COUNT_CASES() or AVG(case.duration_d)", node.pos)
    return node


def _raise_first_aggregation(node: Node) -> None:
    if isinstance(node, Call) and node.name in AGGREGATIONS:
        raise PlqlError(f"{node.name} is an aggregation and cannot be used here (a value per row is needed)", node.pos)
    for child in _children(node):
        _raise_first_aggregation(child)


def _children(node: Node) -> tuple:
    if isinstance(node, Call):
        return node.args
    if isinstance(node, Unary):
        return (node.operand,)
    if isinstance(node, Binary):
        return (node.left, node.right)
    if isinstance(node, InList):
        return (node.operand, *node.items)
    return ()


# --------------------------------------------------------------------------- value helpers
def _is_series(x) -> bool:
    return isinstance(x, pd.Series)


def _is_dt(x) -> bool:
    return (_is_series(x) and pd.api.types.is_datetime64_any_dtype(x.dtype)) or isinstance(x, pd.Timestamp)


def _num(x, pos):
    if _is_series(x):
        if pd.api.types.is_bool_dtype(x.dtype):
            return x.astype("float64")
        if pd.api.types.is_numeric_dtype(x.dtype):
            return x.astype("float64")
        if x.isna().all():
            return pd.Series(np.nan, index=x.index, dtype="float64")
        non_null = x.dropna()
        if all(isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) for v in non_null):
            return pd.to_numeric(x.astype(object), errors="coerce").astype("float64")
        raise PlqlError("A number is needed here, but the values are " + _kind(x), pos)
    if x is None:
        return np.nan
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    raise PlqlError(f"A number is needed here, not {x!r}", pos)


def _kind(x) -> str:
    if _is_dt(x):
        return "timestamps"
    if _is_series(x):
        if pd.api.types.is_bool_dtype(x.dtype):
            return "true/false values"
        if pd.api.types.is_numeric_dtype(x.dtype):
            return "numbers"
        return "text"
    return type(x).__name__


def _bool(x, index):
    """Condition values as a bool Series (missing = false)."""
    if _is_series(x):
        if pd.api.types.is_bool_dtype(x.dtype):
            return x.fillna(False).astype(bool)
        if pd.api.types.is_numeric_dtype(x.dtype):
            return x.fillna(0).astype(bool)
        return (x.notna() & x.astype(object).map(lambda v: v not in ("", None, False)).astype(bool)).astype(bool)
    return pd.Series(bool(x) if x is not None and not (isinstance(x, float) and math.isnan(x)) else False,
                     index=index, dtype=bool)


def _series(x, index) -> pd.Series:
    if _is_series(x):
        return x
    if x is None:
        return pd.Series([None] * len(index), index=index, dtype=object)
    return pd.Series([x] * len(index), index=index)


def _to_ts(value: str, tz: str, pos) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        raise PlqlError(f"{value!r} is not a date or timestamp (use e.g. \"2024-03-01\")", pos) from None
    if ts.tzinfo is None:
        ts = ts.tz_localize(tz, nonexistent="shift_forward", ambiguous=True)
    return ts.tz_convert("UTC")


def _local(x, tz: str, pos) -> pd.Series:
    if not _is_dt(x) or not _is_series(x):
        raise PlqlError("A timestamp column is needed here, e.g. case.start or event.ts", pos)
    return x.dt.tz_convert(tz)


def _date_part(name: str, s: pd.Series) -> pd.Series:
    valid = s.notna()
    out = pd.Series([None] * len(s), index=s.index, dtype=object)
    if not valid.any():
        return out
    v = s[valid]
    if name == "YEAR":
        return v.dt.year.astype("float64").reindex(s.index)
    if name == "HOUR":
        return v.dt.hour.astype("float64").reindex(s.index)
    if name == "QUARTER":
        out[valid] = v.dt.year.astype(str) + "-Q" + v.dt.quarter.astype(str)
    elif name == "MONTH":
        out[valid] = v.dt.strftime("%Y-%m")
    elif name == "DAY":
        out[valid] = v.dt.strftime("%Y-%m-%d")
    elif name == "WEEK":
        iso = v.dt.isocalendar()
        out[valid] = iso["year"].astype(str) + "-W" + iso["week"].astype(int).map("{:02d}".format)
    elif name == "WEEKDAY":
        names = ["1 Mon", "2 Tue", "3 Wed", "4 Thu", "5 Fri", "6 Sat", "7 Sun"]
        out[valid] = v.dt.dayofweek.map(lambda d: names[int(d)])
    return out


def _compare(op: str, a, b):
    if op == "=":
        r = a == b
    elif op == "!=":
        r = a != b
    elif op == "<":
        r = a < b
    elif op == "<=":
        r = a <= b
    elif op == ">":
        r = a > b
    else:
        r = a >= b
    return r


# --------------------------------------------------------------------------- data frame
@dataclass
class Frame:
    """The cases (and their events) a formula is evaluated on."""

    log: EventLog
    cases: pd.DataFrame
    events: pd.DataFrame

    @classmethod
    def of(cls, log: EventLog) -> Frame:
        return cls(log, log.cases, log.events)

    def subset(self, mask: pd.Series) -> Frame:
        if bool(mask.all()):
            return self
        cases = self.cases[mask.to_numpy()]
        return Frame(self.log, cases, self.events[self.events["case_id"].isin(cases.index)])

    @property
    def tz(self) -> str:
        return self.log.display_tz or "UTC"


class Evaluator:
    def __init__(self, frame: Frame):
        self.f = frame
        self._case_ids: np.ndarray | None = None

    # ----------------------------------------------------------- columns
    def case_column(self, name: str) -> pd.Series:
        c = self.f.cases
        if name == "id":
            return pd.Series(c.index.to_numpy(dtype=object), index=c.index, dtype=object)
        if name in ("start", "end", "first_activity", "last_activity", "status", "variant_id", "duration_s"):
            return c[name]
        if name == "duration_h":
            return c["duration_s"] / 3600.0
        if name == "duration_d":
            return c["duration_s"] / 86400.0
        if name == "events":
            return c["n_events"].astype("float64")
        if name == "activities":
            return c["n_unique"].astype("float64")
        if name == "variant":
            return c["variant_id"].map(self.f.log.variants["label"])
        if name == "rework":
            return c["rework"].astype(bool)
        if name == "repeated":
            return c["repeated"].astype("float64")
        if name == "cost":
            return c["total_cost"].astype("float64")
        if name == "age_d":
            return c["age_s"] / 86400.0
        raise KeyError(name)

    def event_column(self, name: str) -> pd.Series:
        e = self.f.events
        if name == "id":
            return e["ocel_event_id"]
        if name in ("case_id", "activity", "ts", "start_ts", "resource", "department", "prev_activity"):
            return e[name]
        if name == "cost":
            return e["cost"].astype("float64")
        if name == "seq":
            return e["seq"].astype("float64")
        if name == "elapsed_h":
            return e["elapsed_s"] / 3600.0
        if name == "elapsed_d":
            return e["elapsed_s"] / 86400.0
        if name == "exec_h":
            return e["exec_s"] / 3600.0
        if name == "status":
            return e["case_status"]
        raise KeyError(name)

    def index(self, level: int) -> pd.Index:
        return self.f.events.index if level == EVENT else self.f.cases.index

    def to_events(self, case_values) -> pd.Series:
        """Repeat case values on each of the case's events."""
        if not _is_series(case_values):
            return case_values
        return case_values.reindex(self.f.events["case_id"].to_numpy()).set_axis(self.f.events.index)

    def to_cases_any(self, event_values) -> pd.Series:
        """Case-level "at least one event" of an event condition."""
        b = _bool(event_values, self.f.events.index)
        anyv = b.groupby(self.f.events["case_id"].to_numpy()).any()
        return anyv.reindex(self.f.cases.index, fill_value=False).astype(bool)

    # ----------------------------------------------------------- row evaluation
    def row(self, node: Node, level: int):
        """Value of a row expression at ``level`` (CASE or EVENT): a Series on that level's index or a scalar."""
        if isinstance(node, Literal):
            return node.value
        if isinstance(node, Column):
            if node.table == "case":
                v = self.case_column(node.name)
                return self.to_events(v) if level == EVENT else v
            if level != EVENT:
                raise PlqlError(f"event.{node.name} is an event value; use it in an event-level context or "
                                f"pull it up to the case, e.g. CASE_FIRST(event.{node.name})", node.pos)
            return self.event_column(node.name)
        if isinstance(node, Unary):
            return self._unary(node.op, self.row(node.operand, level), node.pos, self.index(level))
        if isinstance(node, Binary):
            return self._binary(node.op, self.row(node.left, level), self.row(node.right, level), node.pos,
                                self.index(level))
        if isinstance(node, InList):
            return self._in(self.row(node.operand, level), [self.row(i, level) for i in node.items], node,
                            self.index(level))
        if isinstance(node, Call):
            if node.name in CASE_FUNCTIONS:
                v = self._case_function(node)
                return self.to_events(v) if level == EVENT else v
            return self._row_function(node.name, [self.row(a, level) for a in node.args], node,
                                      self.index(level))
        raise PlqlError("Unsupported expression", None)

    # ----------------------------------------------------------- operators (also used for measures)
    def _unary(self, op, v, pos, index):
        if op == "NOT":
            return ~_bool(v, index)
        n = _num(v, pos)
        return -n

    def _binary(self, op, a, b, pos, index):
        if op in ("AND", "OR"):
            x, y = _bool(a, index), _bool(b, index)
            return (x & y) if op == "AND" else (x | y)
        if op in ("=", "!=", "<", "<=", ">", ">="):
            a, b = self._comparable(a, b, pos)
            if a is None or b is None:
                # "x = NULL" tests for a missing value, "x != NULL" for a present one; ordering is false
                other = _series(b if a is None else a, index)
                if op == "=":
                    return other.isna().astype(bool)
                if op == "!=":
                    return other.notna().astype(bool)
                return pd.Series(False, index=index, dtype=bool)
            r = _compare(op, a, b)
            if _is_series(r):
                missing = (a.isna() if _is_series(a) else False) | (b.isna() if _is_series(b) else False)
                return r.fillna(False).astype(bool) & ~missing
            return pd.Series(bool(r), index=index, dtype=bool)
        if op == "-" and _is_dt(a) and _is_dt(b):
            return (a - b) / pd.Timedelta(days=1)
        if _is_dt(a) or _is_dt(b):
            raise PlqlError("Timestamps can only be subtracted from each other (result in days) or compared", pos)
        x, y = _num(a, pos), _num(b, pos)
        if op == "+":
            r = x + y
        elif op == "-":
            r = x - y
        elif op == "*":
            r = x * y
        elif op == "/":
            with np.errstate(divide="ignore", invalid="ignore"):
                r = x / y
        else:
            with np.errstate(divide="ignore", invalid="ignore"):
                r = x % y
        if _is_series(r):
            return r.replace([np.inf, -np.inf], np.nan)
        return np.nan if isinstance(r, float) and math.isinf(r) else r

    def _comparable(self, a, b, pos):
        if _is_dt(a) and isinstance(b, str):
            b = _to_ts(b, self.f.tz, pos)
        elif _is_dt(b) and isinstance(a, str):
            a = _to_ts(a, self.f.tz, pos)
        for x, y in ((a, b), (b, a)):
            if _is_series(x) and pd.api.types.is_numeric_dtype(x.dtype) and not pd.api.types.is_bool_dtype(x.dtype) \
                    and isinstance(y, str):
                raise PlqlError(f"Numbers cannot be compared with the text {y!r}", pos)
        if _is_series(a) and not pd.api.types.is_numeric_dtype(a.dtype) and not _is_dt(a):
            a = a.astype(object)
        if _is_series(b) and not pd.api.types.is_numeric_dtype(b.dtype) and not _is_dt(b):
            b = b.astype(object)
        return a, b

    def _in(self, v, items, node: InList, index):
        if not all(not _is_series(i) for i in items):
            r = pd.Series(False, index=index, dtype=bool)
            for i in items:
                r |= self._binary("=", v, i, node.pos, index)
        else:
            values = [i for i in items if i is not None]
            if _is_dt(v):
                values = [_to_ts(i, self.f.tz, node.pos) if isinstance(i, str) else i for i in values]
            s = _series(v, index)
            r = s.isin(values) & s.notna()
            if any(i is None for i in items):
                r |= s.isna()
        return ~r if node.negated else r

    def _row_function(self, name: str, args: list, node: Call, index):
        pos = node.pos
        if name == "IF":
            cond = _bool(args[0], index)
            a, b = args[1], args[2]
            if not _is_series(a) and not _is_series(b) and not _is_series(args[0]):
                return a if (bool(cond.iloc[0]) if len(cond) else False) else b
            a, b = _series(a, index), _series(b, index)
            if pd.api.types.is_numeric_dtype(a.dtype) and pd.api.types.is_numeric_dtype(b.dtype):
                return pd.Series(np.where(cond.to_numpy(), a.to_numpy(dtype="float64"), b.to_numpy(dtype="float64")),
                                 index=index)
            if a.isna().all() and pd.api.types.is_numeric_dtype(b.dtype):
                return b.astype("float64").where(~cond)
            if b.isna().all() and pd.api.types.is_numeric_dtype(a.dtype):
                return a.astype("float64").where(cond)
            if _is_dt(a) and (b.isna().all() or _is_dt(b)):
                return a.where(cond, b if _is_dt(b) else pd.NaT)
            if _is_dt(b) and a.isna().all():
                return b.where(~cond, pd.NaT)
            return pd.Series(np.where(cond.to_numpy(), a.astype(object).to_numpy(), b.astype(object).to_numpy()),
                             index=index, dtype=object)
        if name == "COALESCE":
            out = _series(args[0], index)
            for a in args[1:]:
                out = out.where(out.notna(), _series(a, index) if _is_series(a) else a)
            return out
        if name == "ISNULL":
            v = args[0]
            return _series(v, index).isna() if _is_series(v) else pd.Series(v is None, index=index, dtype=bool)
        if name == "ROUND":
            digits = args[1] if len(args) > 1 else 0
            if _is_series(digits) or not isinstance(digits, (int, float)):
                raise PlqlError("ROUND needs a fixed number of digits", pos)
            v = _num(args[0], pos)
            return v.round(int(digits)) if _is_series(v) else round(v, int(digits))
        if name == "ABS":
            v = _num(args[0], pos)
            return v.abs() if _is_series(v) else abs(v)
        if name == "BUCKET":
            size = args[1]
            if _is_series(size) or not isinstance(size, (int, float)) or size <= 0:
                raise PlqlError("BUCKET needs a fixed positive bucket size", pos)
            v = _num(args[0], pos)
            return np.floor(v / size) * size
        if name == "CONCAT":
            out = None
            for a in args:
                s = _series(a, index).astype(object)
                s = s.map(lambda x: "" if x is None or (isinstance(x, float) and math.isnan(x)) else
                          (str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)))
                out = s if out is None else out + s
            return out
        if name in ("LOWER", "UPPER"):
            v = args[0]
            if not _is_series(v):
                return None if v is None else (str(v).lower() if name == "LOWER" else str(v).upper())
            s = v.astype(object)
            return s.map(lambda x: x if x is None or (isinstance(x, float) and math.isnan(x)) else
                         (str(x).lower() if name == "LOWER" else str(x).upper()))
        if name in _TIME_FUNCS:
            return _date_part(name, _local(args[0], self.f.tz, pos))
        if name in ("DAYS_BETWEEN", "HOURS_BETWEEN"):
            a, b = args
            a = _to_ts(a, self.f.tz, pos) if isinstance(a, str) else a
            b = _to_ts(b, self.f.tz, pos) if isinstance(b, str) else b
            if not (_is_dt(a) and _is_dt(b)):
                raise PlqlError(f"{name} needs two timestamps", pos)
            return (b - a) / (pd.Timedelta(days=1) if name == "DAYS_BETWEEN" else pd.Timedelta(hours=1))
        raise PlqlError(f"Unknown function {name}", pos)

    # ----------------------------------------------------------- case functions
    def _case_function(self, node: Call) -> pd.Series:
        ev, cases = self.f.events, self.f.cases
        name = node.name
        if name in ("CONTAINS", "COUNT_ACT"):
            acts = [self.row(a, CASE) for a in node.args]
            if any(not isinstance(a, str) for a in acts):
                raise PlqlError(f"{name} needs activity names in quotes", node.pos)
            hit = ev["activity"].isin(acts).to_numpy()
            counts = pd.Series(hit, index=ev.index).groupby(ev["case_id"].to_numpy()).sum()
            counts = counts.reindex(cases.index, fill_value=0)
            return counts > 0 if name == "CONTAINS" else counts.astype("float64")
        if name == "THROUGHPUT":
            vals = [self.row(a, CASE) for a in node.args]
            a, b = vals[0], vals[1]
            unit = vals[2] if len(vals) > 2 else "d"
            if not isinstance(a, str) or not isinstance(b, str):
                raise PlqlError("THROUGHPUT needs two activity names in quotes", node.pos)
            if unit not in _UNITS:
                raise PlqlError(f"Unknown time unit {unit!r}: use one of {', '.join(_UNITS)}", node.pos)
            first_a = ev.loc[ev["activity"] == a].groupby("case_id")["ts"].min().rename("ta")
            eb = ev.loc[ev["activity"] == b, ["case_id", "ts"]].merge(first_a, left_on="case_id", right_index=True)
            first_b = eb.loc[eb["ts"] >= eb["ta"]].groupby("case_id")["ts"].min()
            diff = (first_b - first_a.reindex(first_b.index)).dt.total_seconds() / _UNITS[unit]
            return diff.reindex(cases.index).astype("float64")
        # pull-up aggregations of event values
        keys = ev["case_id"].to_numpy()
        if name == "CASE_COUNT":
            if node.args:
                v = _bool(self.row(node.args[0], EVENT), ev.index)
            else:
                v = pd.Series(True, index=ev.index)
            return v.groupby(keys).sum().reindex(cases.index, fill_value=0).astype("float64")
        v = _series(self.row(node.args[0], EVENT), ev.index)
        g = v.groupby(keys)
        if name == "CASE_SUM":
            out = _num(v, node.pos).groupby(keys).sum(min_count=1)
        elif name == "CASE_AVG":
            out = _num(v, node.pos).groupby(keys).mean()
        elif name == "CASE_MIN":
            out = g.min()
        elif name == "CASE_MAX":
            out = g.max()
        elif name == "CASE_FIRST":
            out = g.first()
        elif name == "CASE_LAST":
            out = g.last()
        else:
            out = g.nunique().astype("float64")
            return out.reindex(cases.index, fill_value=0.0)
        return out.reindex(cases.index)

    # ----------------------------------------------------------- filters
    def condition(self, node: Node, level: int) -> pd.Series:
        """Case mask (bool Series on the cases' index) for a row condition."""
        if level == EVENT:
            return self.to_cases_any(self.row(node, EVENT))
        return _bool(self.row(node, CASE), self.f.cases.index)


# --------------------------------------------------------------------------- grouping and measures
class Grouping:
    """Group ids of cases and events for the query's dimensions."""

    def __init__(self, ev: Evaluator, level: int | None, case_gid: pd.Series | None, event_gid: pd.Series | None,
                 n_groups: int):
        self.ev = ev
        self.level = level
        self.n = n_groups
        cases, events = ev.f.cases, ev.f.events
        if level is None:
            self.case_gid = pd.Series(0, index=cases.index, dtype="int64")
            self.event_gid = pd.Series(0, index=events.index, dtype="int64")
        elif level == CASE:
            self.case_gid = case_gid
            self.event_gid = pd.Series(case_gid.reindex(events["case_id"].to_numpy()).to_numpy(), index=events.index)
        else:
            self.case_gid = None
            self.event_gid = event_gid
        self._pairs: pd.DataFrame | None = None

    @property
    def pairs(self) -> pd.DataFrame:
        """Distinct (group, case) pairs for event-level grouping."""
        if self._pairs is None:
            p = pd.DataFrame({"g": self.event_gid.to_numpy(), "c": self.ev.f.events["case_id"].to_numpy()})
            self._pairs = p.drop_duplicates().reset_index(drop=True)
        return self._pairs

    def align(self, values, level: int) -> tuple[pd.Series, np.ndarray]:
        """(values, group ids) at the rows aggregated for a value of ``level``."""
        if level == EVENT:
            idx = self.ev.f.events.index
            return _series(values, idx), self.event_gid.to_numpy()
        idx = self.ev.f.cases.index
        values = _series(values, idx)
        if self.level == EVENT:
            p = self.pairs
            return values.reindex(p["c"].to_numpy()).reset_index(drop=True), p["g"].to_numpy()
        return values, self.case_gid.to_numpy()

    def full(self, s: pd.Series, fill=np.nan) -> pd.Series:
        return s.reindex(pd.RangeIndex(self.n), fill_value=fill)


def _agg(values: pd.Series, gids: np.ndarray, how: str, pos, extra=None) -> pd.Series:
    if how in ("COUNT", "COUNT_DISTINCT", "MIN", "MAX"):
        v = values
        if how in ("MIN", "MAX") and not _is_dt(v) and not pd.api.types.is_numeric_dtype(v.dtype):
            v = v.astype(object)
            v = v.where(v.notna())
    else:
        v = _num(values, pos)
    g = v.reset_index(drop=True).groupby(gids)
    if how == "COUNT":
        return g.count().astype("float64")
    if how == "COUNT_DISTINCT":
        return g.nunique().astype("float64")
    if how == "SUM":
        return g.sum(min_count=1)
    if how == "AVG":
        return g.mean()
    if how == "MEDIAN":
        return g.median()
    if how == "MIN":
        return g.min()
    if how == "MAX":
        return g.max()
    if how == "STDEV":
        return g.std()
    if how == "PERCENTILE":
        return g.quantile(float(extra))
    raise PlqlError(f"Unknown aggregation {how}", pos)


class MeasureEvaluator:
    def __init__(self, grouping: Grouping):
        self.g = grouping
        self.ev = grouping.ev
        self.index = pd.RangeIndex(grouping.n)

    def value(self, node: Node):
        if isinstance(node, Literal):
            return node.value
        if isinstance(node, Call) and node.name in AGGREGATIONS:
            return self._aggregate(node)
        if isinstance(node, Unary):
            return self.ev._unary(node.op, self.value(node.operand), node.pos, self.index)
        if isinstance(node, Binary):
            return self.ev._binary(node.op, self.value(node.left), self.value(node.right), node.pos, self.index)
        if isinstance(node, InList):
            return self.ev._in(self.value(node.operand), [self.value(i) for i in node.items], node, self.index)
        if isinstance(node, Call):
            return self.ev._row_function(node.name, [self.value(a) for a in node.args], node, self.index)
        raise PlqlError("Unsupported expression in a measure", getattr(node, "pos", None))

    def _count_cases(self, cond: Node | None) -> pd.Series:
        g, ev = self.g, self.ev
        if cond is None:
            ones = pd.Series(1.0, index=ev.f.cases.index)
            v, gid = g.align(ones, CASE)
            return g.full(pd.Series(v.to_numpy()).groupby(gid).sum(), 0.0)
        lvl = max(row_level(cond), CASE)
        if lvl == EVENT and g.level == EVENT:
            mask = _bool(ev.row(cond, EVENT), ev.f.events.index).to_numpy()
            p = pd.DataFrame({"g": g.event_gid.to_numpy()[mask], "c": ev.f.events["case_id"].to_numpy()[mask]})
            return g.full(p.drop_duplicates().groupby("g").size().astype("float64"), 0.0)
        case_mask = ev.condition(cond, lvl)
        v, gid = g.align(case_mask.astype("float64"), CASE)
        return g.full(pd.Series(v.to_numpy()).groupby(gid).sum(), 0.0)

    def _count_events(self, cond: Node | None) -> pd.Series:
        g, ev = self.g, self.ev
        idx = ev.f.events.index
        v = _bool(ev.row(cond, EVENT), idx) if cond is not None else pd.Series(True, index=idx)
        return g.full(pd.Series(v.to_numpy(dtype="float64")).groupby(g.event_gid.to_numpy()).sum(), 0.0)

    def _aggregate(self, node: Call):
        name = node.name
        if name == "COUNT_CASES":
            return self._count_cases(node.args[0] if node.args else None)
        if name == "COUNT_EVENTS":
            return self._count_events(node.args[0] if node.args else None)
        if name == "SHARE":
            cond = node.args[0]
            if row_level(cond) == EVENT:
                num, den = self._count_events(cond), self._count_events(None)
            else:
                num, den = self._count_cases(cond), self._count_cases(None)
            with np.errstate(divide="ignore", invalid="ignore"):
                return (num / den.replace(0, np.nan)).astype("float64")
        arg = node.args[0]
        lvl = max(row_level(arg), CASE)
        values = self.ev.row(arg, lvl)
        v, gid = self.g.align(values, lvl)
        extra = node.args[1].value if name == "PERCENTILE" else None
        out = _agg(v, gid, name, node.pos, extra)
        return self.g.full(out, 0.0 if name in ("COUNT", "COUNT_DISTINCT") else np.nan)


# --------------------------------------------------------------------------- queries
MAX_LIMIT = 10_000


@dataclass
class QueryItem:
    formula: str
    label: str = ""


@dataclass
class QueryResult:
    columns: list[dict]
    rows: list[list]
    total_groups: int
    case_count: int
    total_case_count: int


def _json_value(v, tz: str):
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.tz_convert(tz).isoformat() if v.tzinfo else v.isoformat()
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if v is pd.NaT or (not isinstance(v, str) and pd.isna(v)):
        return None
    return str(v)


def _col_type(s) -> str:
    if _is_dt(s):
        return "datetime"
    if _is_series(s):
        if pd.api.types.is_bool_dtype(s.dtype):
            return "bool"
        if pd.api.types.is_numeric_dtype(s.dtype):
            return "number"
        non_null = s.dropna()
        if len(non_null) and all(isinstance(x, (bool, np.bool_)) for x in non_null.iloc[:50]):
            return "bool"
        if len(non_null) and all(isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool)
                                 for x in non_null.iloc[:50]):
            return "number"
        return "text"
    if isinstance(s, bool):
        return "bool"
    if isinstance(s, (int, float)):
        return "number"
    return "text" if s is not None else "number"


def filter_frame(frame: Frame, filters: list[str], km: KnowledgeModel) -> Frame:
    """Cases meeting every filter condition (AND)."""
    ev = Evaluator(frame)
    mask = pd.Series(True, index=frame.cases.index, dtype=bool)
    for f in filters:
        if not f or not f.strip():
            continue
        node, level = compile_row(f, km)
        mask &= ev.condition(node, max(level, CASE)).reindex(frame.cases.index, fill_value=False).to_numpy()
    return frame.subset(mask)


def run_query(frame: Frame, dimensions: list[QueryItem], measures: list[QueryItem], km: KnowledgeModel,
              filters: list[str] | None = None, order_by: list[tuple[int, bool]] | None = None,
              limit: int = 500) -> QueryResult:
    """Group the (filtered) cases by the dimensions and compute the measures per group.

    ``order_by`` items are (column index, descending); columns are the dimensions followed by the measures.
    Errors name the failing item: ``PlqlError.message`` is prefixed with "Dimension n:" / "Measure n:".
    """
    if not dimensions and not measures:
        raise PlqlError("Add at least one dimension or measure", None)
    total = len(frame.cases)
    frame = filter_frame(frame, filters or [], km)
    ev = Evaluator(frame)

    dim_nodes = []
    for i, d in enumerate(dimensions, start=1):
        dim_nodes.append(_labelled(f"Dimension {i}", lambda d=d: compile_row(d.formula, km)))
    meas_nodes = [_labelled(f"Measure {i}", lambda m=m: compile_measure(m.formula, km))
                  for i, m in enumerate(measures, start=1)]

    level = max((lv for _, lv in dim_nodes), default=None)
    if level is not None:
        level = max(level, CASE)
        idx = ev.index(level)
        dim_values = [_labelled(f"Dimension {i}", lambda n=n: _series(ev.row(n, level), idx))
                      for i, (n, _) in enumerate(dim_nodes, start=1)]
        keyframe = pd.DataFrame({f"d{i}": s.astype(object).where(s.notna(), None).to_numpy()
                                 if not _is_dt(s) else s.to_numpy() for i, s in enumerate(dim_values)}, index=idx)
        gid = keyframe.groupby(list(keyframe.columns), dropna=False, sort=False).ngroup()
        n_groups = int(gid.max()) + 1 if len(gid) else 0
        firsts = keyframe.groupby(gid.to_numpy(), sort=True).first() if len(gid) else keyframe.iloc[:0]
        grouping = Grouping(ev, level, gid if level == CASE else None, gid if level == EVENT else None, n_groups)
        dim_series = [firsts[c].reindex(pd.RangeIndex(n_groups)) for c in keyframe.columns]
        # restore the dtype of datetime/numeric dimensions (groupby.first keeps them; object keys come back as object)
        dim_series = [s if not _is_dt(src) else pd.to_datetime(s, utc=True)
                      for s, src in zip(dim_series, dim_values)]
        dim_types = [_col_type(src) for src in dim_values]
    else:
        n_groups = 1
        grouping = Grouping(ev, None, None, None, 1)
        dim_series, dim_types = [], []

    me = MeasureEvaluator(grouping)
    meas_series = []
    for i, node in enumerate(meas_nodes, start=1):
        v = _labelled(f"Measure {i}", lambda node=node: me.value(node))
        meas_series.append(_series(v, pd.RangeIndex(n_groups)))

    columns = [{"label": d.label or d.formula, "formula": d.formula, "kind": "dimension", "type": t}
               for d, t in zip(dimensions, dim_types)]
    columns += [{"label": m.label or m.formula, "formula": m.formula, "kind": "measure", "type": _col_type(s)}
                for m, s in zip(measures, meas_series)]
    table = pd.DataFrame({i: s.to_numpy() for i, s in enumerate([*dim_series, *meas_series])},
                         index=pd.RangeIndex(n_groups))
    # an empty population still has one "total" row for measure-only queries (counts = 0)
    if level is not None and n_groups == 0:
        table = table.iloc[:0]

    order = order_by if order_by is not None else _default_order(dimensions, len(dim_series), len(meas_series))
    if order and len(table):
        cols, asc = [], []
        for col, desc in order:
            if 0 <= col < len(table.columns):
                cols.append(col)
                asc.append(not desc)
        if cols:
            table = table.sort_values(cols, ascending=asc, na_position="last", kind="mergesort",
                                      key=lambda s: s.astype(object).map(_sort_key) if s.dtype == object else s)
    limit = max(1, min(int(limit or 500), MAX_LIMIT))
    tz = frame.tz
    rows = [[_json_value(v, tz) for v in rec] for rec in table.head(limit).itertuples(index=False, name=None)]
    return QueryResult(columns, rows, len(table), len(frame.cases), total)


def _sort_key(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return (2, "")
    if isinstance(v, (bool, np.bool_)):
        return (0, float(v))
    if isinstance(v, (int, float, np.integer, np.floating)):
        return (0, float(v))
    return (1, str(v))


def _default_order(dimensions: list[QueryItem], n_dims: int, n_meas: int) -> list[tuple[int, bool]]:
    """Time-like dimensions sort ascending by themselves; otherwise the first measure descending."""
    if n_dims and any(f.formula.strip().upper().startswith(tuple(f"{t}(" for t in _TIME_FUNCS | {"BUCKET"}))
                      for f in dimensions):
        return [(i, False) for i in range(n_dims)]
    if n_meas:
        return [(n_dims, True), *((i, False) for i in range(n_dims))]
    return [(i, False) for i in range(n_dims)]


def _labelled(label: str, fn: Callable):
    try:
        return fn()
    except PlqlError as exc:
        raise PlqlError(f"{label}: {exc.message}", exc.position) from None


def evaluate_measure(frame: Frame, formula: str, km: KnowledgeModel, filters: list[str] | None = None):
    """Single value of a measure over the (filtered) cases."""
    result = run_query(frame, [], [QueryItem(formula)], km, filters)
    value = result.rows[0][0] if result.rows else None
    return value, result


def count_matching(frame: Frame, condition: str, km: KnowledgeModel) -> tuple[int, int]:
    """(matching cases, all cases) for a filter condition."""
    return len(filter_frame(frame, [condition], km).cases), len(frame.cases)


def strip_label(message: str) -> str:
    for prefix in ("Dimension ", "Measure "):
        if message.startswith(prefix) and ": " in message:
            return message.split(": ", 1)[1]
    return message
