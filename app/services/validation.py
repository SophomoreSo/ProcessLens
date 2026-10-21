"""Row-level validation and normalization of a raw (all-string) event table.

Rules (all explicit, nothing is silently dropped):

* ``case_id`` / ``activity`` empty after trimming -> error.
* ``timestamp`` empty or unparseable -> error.
* Local timestamps that are ambiguous (DST fall-back) or nonexistent
  (DST spring-forward) in the source timezone -> error unless an explicit
  resolution policy is chosen.
* ``start_timestamp`` (optional) unparseable -> error; later than the
  completion timestamp -> error.
* ``cost`` (optional) non-empty but not a finite number -> error; negative -> warning.
* ``event_id`` (optional) used by more than one row -> error on every row sharing it.
* Rows with the wrong number of fields -> error.
* Repeated activities within a case are legitimate (rework) and are kept.
* Equal timestamps within a case are kept; their order falls back to the
  source row order, which is reported as a warning because it does not
  establish a reliable causal order.

Timestamps are normalized to UTC. Values with an explicit offset (ISO 8601
``Z`` / ``+02:00``) use that offset; others are interpreted in the configured
source timezone.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.i18n import t
from app.schemas.imports import ColumnMapping, TimestampOptions
from app.services.ingestion import RawTable

OFFSET_PATTERN = r"(?:[Zz]|[+-]\d{2}:?\d{2})$"
MAX_VALUE_CHARS = 200


@dataclass
class ValidationResult:
    normalized: pd.DataFrame  # valid rows only, deterministically sorted, with ``seq``
    findings: list[dict]
    invalid_rows: dict[int, list[str]]  # source_row -> reasons
    raw: pd.DataFrame  # original string frame (with ``__row``)
    total_rows: int
    counts_by_code: Counter = field(default_factory=Counter)
    severity_by_code: dict[str, str] = field(default_factory=dict)
    tie_cases: int = 0

    def count(self, severity: str) -> int:
        """Total findings of a severity (complete even when stored findings are capped)."""
        return sum(n for code, n in self.counts_by_code.items() if self.severity_by_code.get(code) == severity)

    @property
    def valid_rows(self) -> int:
        return len(self.normalized)


def _trunc(v) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "…"


def parse_timestamps(raw: pd.Series, opts: TimestampOptions) -> tuple[pd.Series, pd.Series]:
    """Return (UTC timestamps, problem code per value).

    Problem codes: '' (ok), 'missing', 'malformed', 'ambiguous', 'nonexistent'.
    Also returns resolved-but-adjusted values via codes 'ambiguous_resolved' /
    'nonexistent_resolved' (these are *not* errors).
    """
    s = raw.astype(str).str.strip()
    n = len(s)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[us, UTC]")
    code = pd.Series("", index=s.index, dtype=object)
    missing = s == ""
    code[missing] = "missing"
    present = ~missing
    if n == 0:
        return out, code

    fmt = opts.format
    if fmt in ("unix_s", "unix_ms"):
        nums = pd.to_numeric(s.where(present), errors="coerce")
        unit = "s" if fmt == "unix_s" else "ms"
        parsed = pd.to_datetime(nums, unit=unit, utc=True, errors="coerce")
        out[present] = parsed[present].dt.as_unit("us")
        code[present & out.isna()] = "malformed"
        return out, code

    if fmt == "ISO8601":
        aware = present & s.str.contains(OFFSET_PATTERN, regex=True)
    elif "%z" in fmt or "%Z" in fmt:
        aware = present.copy()
    else:
        aware = pd.Series(False, index=s.index)
    naive = present & ~aware

    if aware.any():
        parsed = pd.to_datetime(s[aware], format=fmt, utc=True, errors="coerce")
        out[aware] = parsed.dt.as_unit("us")

    if naive.any():
        parsed = pd.to_datetime(s[naive], format=fmt, errors="coerce")
        if getattr(parsed.dt, "tz", None) is not None:  # format carried tz info
            out[naive] = parsed.dt.tz_convert("UTC").dt.as_unit("us")
        else:
            tz = opts.source_timezone
            if tz == "UTC":
                out[naive] = parsed.dt.tz_localize("UTC").dt.as_unit("us")
            else:
                ok = parsed.notna()
                ones = np.ones(len(parsed), dtype=bool)
                probe_amb = parsed.dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
                probe_non = parsed.dt.tz_localize(tz, ambiguous=ones, nonexistent="NaT")
                is_amb = ok & probe_amb.isna()
                is_non = ok & probe_non.isna()
                amb_arr = ones if opts.ambiguous_policy in ("earliest", "reject") else ~ones
                non_param = opts.nonexistent_policy if opts.nonexistent_policy != "reject" else "shift_forward"
                localized = parsed.dt.tz_localize(tz, ambiguous=amb_arr, nonexistent=non_param)
                localized = localized.dt.tz_convert("UTC").dt.as_unit("us")
                if opts.ambiguous_policy == "reject":
                    localized[is_amb] = pd.NaT
                if opts.nonexistent_policy == "reject":
                    localized[is_non] = pd.NaT
                out[naive] = localized
                idx_amb = is_amb[is_amb].index
                idx_non = is_non[is_non].index
                code[idx_amb] = "ambiguous" if opts.ambiguous_policy == "reject" else "ambiguous_resolved"
                code[idx_non] = "nonexistent" if opts.nonexistent_policy == "reject" else "nonexistent_resolved"

    malformed = present & out.isna() & ~code.isin(["ambiguous", "nonexistent"])
    code[malformed] = "malformed"
    return out, code


def validate_table(table: RawTable, mapping: ColumnMapping, ts_opts: TimestampOptions,
                   max_findings: int | None = None) -> ValidationResult:
    missing_cols = [c for c in mapping.mapped_columns().values() if c not in table.columns]
    missing_cols += [c for c in mapping.attributes if c not in table.columns]
    if missing_cols:
        raise ValueError(f"Mapped columns not found in file: {sorted(set(missing_cols))}")

    raw = table.frame.copy()
    raw["__row"] = table.source_rows
    n = len(raw)
    findings: list[dict] = []
    invalid: dict[int, list[str]] = {}
    counts: Counter = Counter()
    severities: dict[str, str] = {}
    rows = raw["__row"].to_numpy()

    def add(severity: str, code: str, params: dict | None = None, row=None, fld=None, case_id=None, value=None):
        """Record a finding. Its message is the catalog entry ``finding.<code>`` rendered with ``params``."""
        counts[code] += 1
        severities[code] = severity
        if severity == "error" and row is not None:
            invalid.setdefault(int(row), []).append(code)
        if max_findings is None or len(findings) < max_findings or severity == "error" and row is None:
            key = f"finding.{code}"
            findings.append({"severity": severity, "code": code, "message": t("en", key, **(params or {})),
                             "message_key": key, "message_params": params or {},
                             "row": None if row is None else int(row), "field": fld, "case_id": case_id,
                             "value": _trunc(value)})

    def add_rows(mask: np.ndarray, severity: str, code: str, params: dict | None, fld: str, values: pd.Series | None):
        idx = np.flatnonzero(mask)
        for i in idx:
            add(severity, code, params, row=rows[i], fld=fld, value=None if values is None else values.iat[i])

    # --- structural problems -------------------------------------------------
    for r, (exp, act) in sorted(table.field_count_issues.items()):
        add("error", "field_count_mismatch", {"actual": act, "expected": exp}, row=r)
    if table.blank_lines:
        add("info", "blank_lines_skipped", {"count": table.blank_lines})

    def col(fld: str) -> pd.Series | None:
        c = getattr(mapping, fld)
        return raw[c].astype(str) if c else None

    trimmed_count = 0

    def clean(series: pd.Series) -> pd.Series:
        nonlocal trimmed_count
        stripped = series.str.strip()
        trimmed_count += int((stripped != series).sum())
        return stripped

    # --- required fields --------------------------------------------------------
    case_raw = col("case_id")
    case = clean(case_raw)
    add_rows((case == "").to_numpy(), "error", "missing_case_id", None, mapping.case_id, None)

    act_raw = col("activity")
    activity = clean(act_raw)
    add_rows((activity == "").to_numpy(), "error", "empty_activity", None, mapping.activity, None)

    ts, ts_code = parse_timestamps(col("timestamp"), ts_opts)
    ts_raw = col("timestamp")
    add_rows((ts_code == "missing").to_numpy(), "error", "missing_timestamp", None, mapping.timestamp, None)
    add_rows((ts_code == "malformed").to_numpy(), "error", "malformed_timestamp",
             {"format": ts_opts.format}, mapping.timestamp, ts_raw)
    add_rows((ts_code == "ambiguous").to_numpy(), "error", "ambiguous_local_time",
             {"timezone": ts_opts.source_timezone}, mapping.timestamp, ts_raw)
    add_rows((ts_code == "nonexistent").to_numpy(), "error", "nonexistent_local_time",
             {"timezone": ts_opts.source_timezone}, mapping.timestamp, ts_raw)
    n_amb_res = int((ts_code == "ambiguous_resolved").sum())
    n_non_res = int((ts_code == "nonexistent_resolved").sum())
    if n_amb_res:
        add("warning", "ambiguous_time_resolved", {"count": n_amb_res, "policy": ts_opts.ambiguous_policy})
    if n_non_res:
        add("warning", "nonexistent_time_resolved", {"count": n_non_res, "policy": ts_opts.nonexistent_policy})

    # --- optional fields --------------------------------------------------------
    start_ts = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[us, UTC]")
    if mapping.start_timestamp:
        st, st_code = parse_timestamps(col("start_timestamp"), ts_opts)
        st_raw = col("start_timestamp")
        add_rows((st_code == "malformed").to_numpy(), "error", "malformed_start_timestamp",
                 {"format": ts_opts.format}, mapping.start_timestamp, st_raw)
        add_rows(st_code.isin(["ambiguous", "nonexistent"]).to_numpy(), "error", "dst_start_timestamp", None,
                 mapping.start_timestamp, st_raw)
        start_ts = st
        later = (start_ts.notna() & ts.notna() & (start_ts > ts)).to_numpy()
        add_rows(later, "error", "start_after_end", None, mapping.start_timestamp, st_raw)

    cost = pd.Series(np.nan, index=raw.index, dtype="float64")
    if mapping.cost:
        cost_raw = clean(col("cost"))
        num = pd.to_numeric(cost_raw.where(cost_raw != ""), errors="coerce")
        bad = ((cost_raw != "") & (num.isna() | ~np.isfinite(num.fillna(0)))).to_numpy()
        add_rows(bad, "error", "invalid_cost", None, mapping.cost, cost_raw)
        cost = num.where(~pd.Series(bad, index=raw.index))
        neg = (cost < 0).to_numpy()
        if neg.any():
            add("warning", "negative_cost", {"count": int(neg.sum())}, fld=mapping.cost)

    event_id = None
    if mapping.event_id:
        event_id = clean(col("event_id"))
        nonempty = event_id != ""
        dup = (nonempty & event_id.duplicated(keep=False)).to_numpy()
        add_rows(dup, "error", "duplicate_event_id",
                 None, mapping.event_id, event_id)
        n_empty = int((~nonempty).sum())
        if n_empty:
            add("info", "missing_event_id", {"count": n_empty}, fld=mapping.event_id)

    def optional_text(fld: str) -> pd.Series | None:
        s = col(fld)
        if s is None:
            return None
        s = clean(s)
        return s.where(s != "", None)

    resource = optional_text("resource")
    department = optional_text("department")
    case_status = optional_text("case_status")

    if trimmed_count:
        add("info", "whitespace_trimmed", {"count": trimmed_count})

    # --- assemble valid rows ----------------------------------------------------
    invalid_mask = raw["__row"].isin(list(invalid.keys())).to_numpy()
    norm = pd.DataFrame({
        "source_row": raw["__row"].astype("int64"),
        "case_id": case,
        "activity": activity,
        "ts": ts,
        "start_ts": start_ts,
        "event_id": event_id.where(event_id != "", None) if event_id is not None else None,
        "resource": resource,
        "department": department,
        "cost": cost,
        "case_status": case_status,
    })
    if mapping.attributes:
        attr_frame = raw[mapping.attributes].astype(str)
        norm["attributes"] = [
            {k: v for k, v in rec.items() if v.strip() != ""} or None
            for rec in attr_frame.to_dict(orient="records")
        ]
    else:
        norm["attributes"] = None
    norm = norm[~invalid_mask]
    norm = norm.sort_values(["case_id", "ts", "source_row"], kind="mergesort").reset_index(drop=True)
    norm["seq"] = norm.groupby("case_id", sort=False).cumcount().astype("int64")

    # --- equal timestamps within a case (order = source row order) --------------
    tie_cases = 0
    if len(norm):
        tied = norm.duplicated(subset=["case_id", "ts"], keep=False)
        if tied.any():
            per_case = norm[tied].groupby("case_id").size()
            tie_cases = len(per_case)
            add("warning", "timestamp_ties", {"cases": tie_cases, "events": int(tied.sum())})
            for cid, cnt in per_case.head(max_findings or len(per_case)).items():
                add("warning", "timestamp_tie_case", {"count": int(cnt)}, case_id=str(cid))

    if n == 0:
        add("error", "no_data_rows")

    findings.sort(key=lambda f: ({"error": 0, "warning": 1, "info": 2}[f["severity"]], f["row"] or 0))
    return ValidationResult(norm, findings, invalid, raw, n, counts, severities, tie_cases)


def preview_records(norm: pd.DataFrame, limit: int = 20) -> list[dict]:
    out = []
    for rec in norm.head(limit).to_dict(orient="records"):
        item = {}
        for k, v in rec.items():
            if isinstance(v, pd.Timestamp):
                item[k] = v.isoformat()
            elif v is pd.NaT or (isinstance(v, float) and np.isnan(v)):
                item[k] = None
            else:
                item[k] = v
        out.append(item)
    return out
