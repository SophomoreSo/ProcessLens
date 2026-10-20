"""Persistence models for uploads, datasets, events, findings and import jobs.

All timestamps are stored as naive UTC ``DateTime`` values. The schema is
case-centric: every event belongs to exactly one case. Object-centric event
logs (many-to-many event/object relations) are a roadmap item and would be
added as separate tables rather than by overloading ``case_id``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Upload(Base):
    """A staged CSV file awaiting (or used by) an import."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4 hex
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    encoding: Mapped[str] = mapped_column(String(32))
    delimiter: Mapped[str] = mapped_column(String(4))
    columns: Mapped[list] = mapped_column(JSON)
    row_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    # Incremented on every successful (re-)import; part of every cache key.
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Incremented when analysis settings change (completion rule, reference time ...).
    settings_revision: Mapped[int] = mapped_column(Integer, default=1)

    # Source metadata
    source_filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_size_bytes: Mapped[int] = mapped_column(BigInteger)
    upload_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    import_options: Mapped[dict] = mapped_column(JSON)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Row accounting
    source_row_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    case_count: Mapped[int] = mapped_column(Integer, default=0)
    quarantined_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)

    # Analysis settings
    display_timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    completion_mode: Mapped[str] = mapped_column(String(20), default="none")
    completion_activities: Mapped[list] = mapped_column(JSON, default=list)
    completed_status_values: Mapped[list] = mapped_column(JSON, default=list)
    open_status_values: Mapped[list] = mapped_column(JSON, default=list)
    reference_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attribute_columns: Mapped[list] = mapped_column(JSON, default=list)

    events: Mapped[list[Event]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_dataset_case", "dataset_id", "case_id"),
        Index("ix_events_dataset_activity", "dataset_id", "activity"),
        Index("ix_events_dataset_ts", "dataset_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    source_row: Mapped[int] = mapped_column(Integer)  # 1-based data row in the source file
    seq: Mapped[int] = mapped_column(Integer)  # position within case after deterministic sort
    case_id: Mapped[str] = mapped_column(String(255))
    activity: Mapped[str] = mapped_column(String(500))
    ts: Mapped[datetime] = mapped_column(DateTime)  # completion timestamp, UTC
    start_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    case_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    dataset: Mapped[Dataset] = relationship(back_populates="events")


class ImportFinding(Base):
    __tablename__ = "import_findings"
    __table_args__ = (Index("ix_findings_dataset", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    dataset_version: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(10))  # error | warning | info
    code: Mapped[str] = mapped_column(String(50))
    row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field: Mapped[str | None] = mapped_column(String(100), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuarantinedRow(Base):
    """An invalid source row kept aside (never used in analytics)."""

    __tablename__ = "quarantined_rows"
    __table_args__ = (Index("ix_quarantine_dataset", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    dataset_version: Mapped[int] = mapped_column(Integer)
    row: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON)


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="import")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    # queued | running | succeeded | failed | interrupted
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(200), default="Queued")
    upload_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
