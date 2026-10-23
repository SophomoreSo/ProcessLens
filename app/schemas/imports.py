from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import validate_timezone

OPTIONAL_FIELDS = ("event_id", "resource", "department", "cost", "start_timestamp", "case_status")
REQUIRED_FIELDS = ("case_id", "activity", "timestamp")
ALLOWED_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
ALLOWED_DELIMITERS = (",", ";", "\t", "|")


def normalize_encoding(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if v.lower() not in ALLOWED_ENCODINGS:
        raise ValueError(f"Encoding must be one of {list(ALLOWED_ENCODINGS)}")
    return v.lower()


def normalize_delimiter(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if v in ("\\t", "tab"):
        v = "\t"
    if v not in ALLOWED_DELIMITERS:
        raise ValueError("Delimiter must be one of , ; | or tab")
    return v


class ObjectColumn(BaseModel):
    """A column whose values identify further objects related to each row's event."""

    column: str = Field(min_length=1)
    object_type: str = Field(min_length=1, max_length=255)
    separator: str | None = Field(None, max_length=3, description="Split multi-valued cells (e.g. ';'); none = one id")

    @field_validator("object_type")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("object_type must not be blank")
        return v

    @field_validator("separator", mode="before")
    @classmethod
    def _blank(cls, v):
        return None if isinstance(v, str) and v == "" else v


class ColumnMapping(BaseModel):
    """Maps source columns onto the object-centric event schema.

    Each row is one event. ``case_id`` identifies the event's object of the lead type
    ``case_object_type``; ``object_columns`` relate the event to further objects;
    ``case_attributes`` are attribute values of the case object (kept as its history)."""

    case_id: str
    activity: str
    timestamp: str
    event_id: str | None = None
    resource: str | None = None
    department: str | None = None
    cost: str | None = None
    start_timestamp: str | None = None
    case_status: str | None = None
    attributes: list[str] = Field(default_factory=list, description="Extra scalar columns kept as event attributes")
    case_object_type: str | None = Field("case", max_length=255,
                                         description="Object type of the objects identified by the case column")
    object_columns: list[ObjectColumn] = Field(default_factory=list)
    case_attributes: list[str] = Field(default_factory=list,
                                       description="Columns holding attributes of the case object (not of the event)")

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @model_validator(mode="after")
    def _distinct(self):
        self.case_object_type = (self.case_object_type or "").strip() or "case"
        used = [getattr(self, f) for f in REQUIRED_FIELDS + OPTIONAL_FIELDS if getattr(self, f)]
        dupes = {c for c in used if used.count(c) > 1}
        if dupes:
            raise ValueError(f"Each source column can be mapped to only one field: {sorted(dupes)}")
        overlap = set(used) & set(self.attributes)
        if overlap:
            raise ValueError(f"Columns mapped to standard fields cannot also be attributes: {sorted(overlap)}")
        roles = used + self.attributes + self.case_attributes + [o.column for o in self.object_columns]
        twice = {c for c in roles if roles.count(c) > 1}
        if twice:
            raise ValueError(f"Each source column can have only one role: {sorted(twice)}")
        return self

    def all_columns(self) -> list[str]:
        return [*self.mapped_columns().values(), *self.attributes, *self.case_attributes,
                *(o.column for o in self.object_columns)]

    def mapped_columns(self) -> dict[str, str]:
        return {f: getattr(self, f) for f in REQUIRED_FIELDS + OPTIONAL_FIELDS if getattr(self, f)}


class TimestampOptions(BaseModel):
    format: str = Field(
        "ISO8601",
        description="'ISO8601', 'unix_s', 'unix_ms' or a strftime pattern such as '%d/%m/%Y %H:%M'",
    )
    source_timezone: str = Field("UTC", description="IANA zone applied to timestamps without an explicit offset")
    ambiguous_policy: Literal["reject", "earliest", "latest"] = Field(
        "reject",
        description="Local times occurring twice (DST fall-back). 'reject' marks the row invalid.",
    )
    nonexistent_policy: Literal["reject", "shift_forward", "shift_backward"] = Field(
        "reject",
        description="Local times skipped by DST spring-forward. 'reject' marks the row invalid.",
    )

    @field_validator("source_timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        return validate_timezone(v)

    @field_validator("format")
    @classmethod
    def _fmt(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Timestamp format must not be empty")
        if v not in ("ISO8601", "unix_s", "unix_ms") and "%" not in v:
            raise ValueError("Use 'ISO8601', 'unix_s', 'unix_ms' or a strftime pattern containing '%' directives")
        return v


class CompletionSettings(BaseModel):
    """How a case is determined to be complete. The last observed event alone is never proof."""

    mode: Literal["none", "end_activity", "case_status"] = "none"
    end_activities: list[str] = Field(default_factory=list)
    completed_status_values: list[str] = Field(default_factory=list)
    open_status_values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "end_activity" and not self.end_activities:
            raise ValueError("Completion mode 'end_activity' requires at least one end activity")
        if self.mode == "case_status" and not self.completed_status_values:
            raise ValueError("Completion mode 'case_status' requires at least one completed status value")
        return self


class ImportOptions(BaseModel):
    mapping: ColumnMapping
    timestamps: TimestampOptions = Field(default_factory=TimestampOptions)
    encoding: str | None = Field(None, description="Override detected encoding (utf-8, utf-8-sig, cp1252, latin-1)")
    delimiter: str | None = Field(None, description="Override detected delimiter")
    invalid_row_policy: Literal["reject_import", "quarantine"] = Field(
        "reject_import",
        description="reject_import: refuse the import if any row is invalid. "
        "quarantine: import valid rows and keep invalid rows aside with their reasons.",
    )

    @field_validator("encoding")
    @classmethod
    def _enc(cls, v):
        return normalize_encoding(v)

    @field_validator("delimiter")
    @classmethod
    def _delim(cls, v):
        return normalize_delimiter(v)


class ConfirmImportRequest(ImportOptions):
    dataset_name: str | None = Field(None, max_length=200)
    target_dataset_id: int | None = Field(None, description="Re-import into an existing dataset (creates a new version)")
    display_timezone: str | None = None
    completion: CompletionSettings = Field(default_factory=CompletionSettings)
    reference_time: datetime | None = Field(
        None, description="Fixed 'now' for open-case age. Defaults to the latest event timestamp."
    )

    @field_validator("display_timezone")
    @classmethod
    def _dtz(cls, v):
        return validate_timezone(v) if v else v


class OcelFieldMapping(BaseModel):
    """Event attributes (by name) used as the standard fields of the analyses."""

    resource: str | None = None
    department: str | None = None
    cost: str | None = None
    start_timestamp: str | None = None


class OcelImportRequest(BaseModel):
    main_object_type: str = Field(min_length=1, max_length=255)
    fields: OcelFieldMapping = Field(default_factory=OcelFieldMapping)
    dataset_name: str | None = Field(None, max_length=200)
    target_dataset_id: int | None = Field(None, description="Re-import into an existing dataset (creates a new version)")
    display_timezone: str | None = None
    completion: CompletionSettings = Field(default_factory=CompletionSettings)
    reference_time: datetime | None = None

    @field_validator("display_timezone")
    @classmethod
    def _dtz(cls, v):
        return validate_timezone(v) if v else v


class UploadPreview(BaseModel):
    upload_id: str
    original_filename: str
    size_bytes: int
    encoding: str
    delimiter: str
    columns: list[str]
    column_warnings: list[str]
    row_count: int
    sample_rows: list[dict[str, str]]
    suggested_mapping: dict[str, str | None]
    suggested_attributes: list[str]
    suggested_timestamp_format: str
    timestamp_format_candidates: list[dict]
    suggested_case_attributes: list[str] = Field(default_factory=list)
    source_format: str = "csv"
    conversion: dict | None = Field(None, description="XES only: how the log was converted to rows")
    conversion_findings: list[dict] = Field(default_factory=list)


class Finding(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    row: int | None = None
    field: str | None = None
    case_id: str | None = None
    value: str | None = None


class ValidationSummary(BaseModel):
    upload_id: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    case_count: int
    error_count: int
    warning_count: int
    info_count: int
    counts_by_code: dict[str, int]
    findings: list[Finding] = Field(description="First findings (see report download for all)")
    findings_truncated: bool
    preview_rows: list[dict] = Field(description="First normalized valid rows, timestamps in UTC")
    can_import_with_reject_policy: bool
    timestamp_tie_cases: int


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    progress: float
    stage: str
    dataset_id: int | None
    target_dataset_id: int | None
    result: dict | None
    error: str | None
    created_at: datetime
    updated_at: datetime
