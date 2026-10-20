"""CSV ingestion: safe staging of uploads, structural parsing and previews.

Every value is read as a string (identifiers keep leading zeros). Structural
problems that affect a single row (wrong field count) are reported per row;
problems that make the whole file unreadable raise :class:`IngestionError`.
"""

from __future__ import annotations

import csv
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from app.config import Settings

ALLOWED_EXTENSIONS = {".csv", ".txt", ".tsv"}
SNIFF_BYTES = 256 * 1024

SYNONYMS: dict[str, list[str]] = {
    "case_id": ["case_id", "caseid", "case", "case:concept:name", "case_key", "order_id", "po_number",
                "purchase_order", "ticket_id", "process_id", "instance_id"],
    "activity": ["activity", "concept:name", "activity_name", "event_name", "event", "task", "step", "action"],
    "timestamp": ["timestamp", "time:timestamp", "end_timestamp", "complete_timestamp", "completion_time",
                  "end_time", "event_time", "time", "datetime", "date"],
    "start_timestamp": ["start_timestamp", "start_time", "start", "started_at", "time:start"],
    "event_id": ["event_id", "eventid", "event_key", "id"],
    "resource": ["resource", "org:resource", "user", "user_name", "agent", "performer", "employee"],
    "department": ["department", "dept", "org:group", "team", "org_unit", "unit"],
    "cost": ["cost", "costs", "amount", "cost:total", "price", "value"],
    "case_status": ["case_status", "status", "case:status", "state", "case_state"],
}

TIMESTAMP_FORMAT_CANDIDATES = [
    "ISO8601",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "unix_s",
    "unix_ms",
]


class IngestionError(Exception):
    """A file-level problem that prevents parsing the upload."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class StagedFile:
    upload_id: str
    stored_name: str
    path: Path
    size_bytes: int
    sha256: str


@dataclass
class RawTable:
    columns: list[str]
    frame: pd.DataFrame  # all-string values, one row per data row
    source_rows: list[int]  # 1-based data row number for each frame row
    field_count_issues: dict[int, tuple[int, int]] = field(default_factory=dict)  # row -> (expected, actual)
    blank_lines: int = 0
    column_warnings: list[str] = field(default_factory=list)


def sanitize_display_filename(name: str | None) -> str:
    """Keep the original name for display only; never used as a filesystem path."""
    base = Path(name or "upload.csv").name
    base = re.sub(r"[^\w.\- ()]", "_", base)[:200]
    return base or "upload.csv"


def stage_upload(stream: BinaryIO, original_filename: str | None, settings: Settings) -> StagedFile:
    """Stream an upload to a server-generated filename, enforcing the size limit."""
    ext = Path(original_filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestionError("unsupported_file_type", f"Only CSV files are accepted (got extension {ext or 'none'!r}).")
    settings.ensure_dirs()
    upload_id = uuid.uuid4().hex
    stored_name = f"{upload_id}.csv"
    path = settings.upload_dir / stored_name
    hasher = hashlib.sha256()
    size = 0
    first_chunk = True
    try:
        with path.open("wb") as out:
            while chunk := stream.read(1024 * 1024):
                if first_chunk:
                    if b"\x00" in chunk[:8192]:
                        raise IngestionError("binary_file", "The file appears to be binary, not a text CSV file.")
                    first_chunk = False
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise IngestionError(
                        "file_too_large", f"The file exceeds the upload limit of {settings.max_upload_mb} MB."
                    )
                hasher.update(chunk)
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise IngestionError("empty_file", "The uploaded file is empty.")
    return StagedFile(upload_id, stored_name, path, size, hasher.hexdigest())


def detect_encoding(path: Path) -> str:
    sample = path.read_bytes()[:SNIFF_BYTES]
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte char may be cut at the sample boundary; only fail when the
        # error is not at the very end of the sample.
        if exc.start < len(sample) - 4:
            return "cp1252"
    return "utf-8-sig" if sample.startswith(b"\xef\xbb\xbf") else "utf-8"


def detect_delimiter(path: Path, encoding: str) -> str:
    with path.open("r", encoding=encoding, errors="replace", newline="") as fh:
        sample = fh.read(64 * 1024)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _normalize_header(raw: list[str]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, name in enumerate(raw, start=1):
        name = name.strip()
        if not name:
            name = f"column_{i}"
            warnings.append(f"Column {i} has no name; it is shown as '{name}'.")
        if name in seen:
            seen[name] += 1
            new = f"{name} ({seen[name]})"
            warnings.append(f"Duplicate column name '{name}' renamed to '{new}'.")
            name = new
        else:
            seen[name] = 1
        out.append(name)
    return out, warnings


def read_raw_table(path: Path, encoding: str, delimiter: str, settings: Settings,
                   limit_rows: int | None = None) -> RawTable:
    """Parse the CSV into an all-string DataFrame without dropping any data row."""
    rows: list[list[str]] = []
    source_rows: list[int] = []
    issues: dict[int, tuple[int, int]] = {}
    blank = 0
    try:
        with path.open("r", encoding=encoding, newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            try:
                header_raw = next(reader)
            except StopIteration:
                raise IngestionError("empty_file", "The file contains no header row.") from None
            columns, col_warnings = _normalize_header(header_raw)
            if len(columns) > settings.max_columns:
                raise IngestionError("too_many_columns", f"The file has {len(columns)} columns; the limit is {settings.max_columns}.")
            ncol = len(columns)
            data_row = 0
            for rec in reader:
                if not rec or (len(rec) == 1 and rec[0].strip() == "" and ncol > 1):
                    blank += 1
                    continue
                data_row += 1
                if data_row > settings.max_rows:
                    raise IngestionError("too_many_rows", f"The file has more than {settings.max_rows:,} data rows (configurable limit).")
                if len(rec) != ncol:
                    issues[data_row] = (ncol, len(rec))
                    rec = (rec + [""] * ncol)[:ncol]
                for j, v in enumerate(rec):
                    if len(v) > settings.max_field_chars:
                        raise IngestionError(
                            "field_too_long",
                            f"Row {data_row}, column '{columns[j]}' exceeds {settings.max_field_chars} characters.",
                        )
                rows.append(rec)
                source_rows.append(data_row)
                if limit_rows is not None and data_row >= limit_rows:
                    break
    except UnicodeDecodeError as exc:
        raise IngestionError(
            "encoding_error",
            f"The file is not valid {encoding} text (byte offset {exc.start}). Choose another encoding or re-save the file as UTF-8.",
        ) from None
    except csv.Error as exc:
        raise IngestionError("csv_parse_error", f"The CSV could not be parsed: {exc}") from None
    frame = pd.DataFrame(rows, columns=columns, dtype=str) if rows else pd.DataFrame(columns=columns, dtype=str)
    return RawTable(columns, frame, source_rows, issues, blank, col_warnings)


def count_data_rows(path: Path, encoding: str, delimiter: str) -> int:
    n = 0
    with path.open("r", encoding=encoding, errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        next(reader, None)
        for rec in reader:
            if rec and not (len(rec) == 1 and rec[0].strip() == ""):
                n += 1
    return n


def _norm(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def suggest_mapping(columns: list[str]) -> tuple[dict[str, str | None], list[str]]:
    normalized = {c: _norm(c) for c in columns}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for fld, syns in SYNONYMS.items():
        choice = None
        for syn in syns:
            for col, norm in normalized.items():
                if col not in used and norm == syn:
                    choice = col
                    break
            if choice:
                break
        mapping[fld] = choice
        if choice:
            used.add(choice)
    attributes = [c for c in columns if c not in used]
    return mapping, attributes


def score_timestamp_formats(values: pd.Series) -> list[dict]:
    """Share of non-empty sample values each candidate format parses."""
    sample = values.astype(str).str.strip()
    sample = sample[sample != ""].head(500)
    results = []
    if sample.empty:
        return [{"format": f, "parsed_share": 0.0} for f in TIMESTAMP_FORMAT_CANDIDATES]
    for fmt in TIMESTAMP_FORMAT_CANDIDATES:
        if fmt in ("unix_s", "unix_ms"):
            nums = pd.to_numeric(sample, errors="coerce")
            ok = nums.notna()
            if fmt == "unix_s":
                ok &= nums.between(0, 4_102_444_800)  # up to 2100
            else:
                ok &= nums.between(1e11, 4_102_444_800_000)
            share = float(ok.mean())
        else:
            parsed = pd.to_datetime(sample, format=fmt, errors="coerce", utc=True)
            share = float(parsed.notna().mean())
        results.append({"format": fmt, "parsed_share": round(share, 4)})
    return results


def build_preview(path: Path, encoding: str, delimiter: str, settings: Settings, sample_size: int = 20) -> dict:
    table = read_raw_table(path, encoding, delimiter, settings, limit_rows=max(sample_size, 500))
    mapping, attributes = suggest_mapping(table.columns)
    candidates: list[dict] = []
    suggested_format = "ISO8601"
    if mapping.get("timestamp"):
        candidates = score_timestamp_formats(table.frame[mapping["timestamp"]])
        best = max(candidates, key=lambda c: c["parsed_share"])  # first wins on ties (ISO8601 first)
        if best["parsed_share"] > 0:
            suggested_format = best["format"]
    sample = table.frame.head(sample_size).to_dict(orient="records")
    return {
        "columns": table.columns,
        "column_warnings": table.column_warnings,
        "sample_rows": sample,
        "suggested_mapping": mapping,
        "suggested_attributes": attributes,
        "suggested_timestamp_format": suggested_format,
        "timestamp_format_candidates": candidates,
    }
