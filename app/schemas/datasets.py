from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ApiModel, validate_timezone


class DatasetOut(ApiModel):
    id: int
    name: str
    description: str | None
    is_demo: bool
    status: str
    version: int
    settings_revision: int
    source_filename: str
    source_sha256: str
    source_size_bytes: int
    imported_at: datetime
    created_at: datetime
    source_row_count: int
    event_count: int
    case_count: int
    quarantined_count: int
    error_count: int
    warning_count: int
    display_timezone: str
    completion_mode: str
    completion_activities: list[str]
    completed_status_values: list[str]
    open_status_values: list[str]
    reference_time: datetime | None
    attribute_columns: list[str]
    import_options: dict


class DatasetUpdate(BaseModel):
    """Editable analysis settings. Changing them bumps ``settings_revision``."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    display_timezone: str | None = None
    completion_mode: str | None = None
    completion_activities: list[str] | None = None
    completed_status_values: list[str] | None = None
    open_status_values: list[str] | None = None
    reference_time: datetime | None = None
    clear_reference_time: bool = False

    @field_validator("display_timezone")
    @classmethod
    def _tz(cls, v):
        return validate_timezone(v) if v else v

    @field_validator("completion_mode")
    @classmethod
    def _mode(cls, v):
        if v is not None and v not in ("none", "end_activity", "case_status"):
            raise ValueError("completion_mode must be none, end_activity or case_status")
        return v

    @model_validator(mode="after")
    def _ref(self):
        if self.clear_reference_time and self.reference_time is not None:
            raise ValueError("Use either reference_time or clear_reference_time")
        return self


class FindingOut(ApiModel):
    severity: str
    code: str
    row: int | None
    field: str | None
    case_id: str | None
    message: str
    value: str | None
