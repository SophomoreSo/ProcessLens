"""Conformance rules, alert rules and persisted alerts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.dataset import utcnow


class ConformanceRule(Base):
    __tablename__ = "conformance_rules"
    __table_args__ = (Index("ix_conformance_rules_dataset", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    rule_type: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_dataset", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    alert_type: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSON)
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    enabled: Mapped[bool] = mapped_column(default=True)
    conformance_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("conformance_rules.id", ondelete="CASCADE"), nullable=True
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Alert(Base):
    """One alert per (alert rule, case). Re-evaluation updates, never duplicates."""

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("alert_rule_id", "case_id", name="uq_alert_rule_case"),
        Index("ix_alerts_dataset_status", "dataset_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"))
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    case_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|acknowledged|resolved
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    message: Mapped[str] = mapped_column(Text)  # English rendering (fallback)
    message_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Whether the condition still held at the most recent evaluation.
    condition_active: Mapped[bool] = mapped_column(default=True)
    dataset_version: Mapped[int] = mapped_column(Integer)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
