"""Task-event logs: user-interaction events (application + action) imported from CSV.

Only the mapped fields are stored. ProcessLens does not record screens or
keystrokes; it analyses events that another tool exported, keyed by a
pseudonymous user id.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.dataset import utcnow


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_size_bytes: Mapped[int] = mapped_column(Integer)
    mapping: Mapped[dict] = mapped_column(JSON)  # source column per field
    source_timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    counts: Mapped[dict] = mapped_column(JSON)
    findings: Mapped[list] = mapped_column(JSON)
    ignored_columns: Mapped[list] = mapped_column(JSON)  # source columns that were not stored
    linked_dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # case-centric dataset for business_case_id
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_log_user_ts", "log_id", "user_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("task_logs.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # source row order (tie-breaker)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[str] = mapped_column(String(255))  # pseudonymous
    ts: Mapped[datetime] = mapped_column(DateTime)  # UTC
    application: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(255))
    business_case_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)  # explicit only, never inferred
