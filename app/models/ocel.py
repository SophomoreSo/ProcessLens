"""Object-centric event logs (OCEL 2.0): events, objects, typed relationships.

Kept separate from the case-centric ``events`` table: an event here can relate
to many objects of many types (many-to-many), and each event is stored once,
so shared events are never duplicated.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.dataset import utcnow


class OcelLog(Base):
    __tablename__ = "ocel_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_size_bytes: Mapped[int] = mapped_column(Integer)
    event_types: Mapped[list] = mapped_column(JSON)  # [{"name", "attributes": [{"name", "type"}]}]
    object_types: Mapped[list] = mapped_column(JSON)
    counts: Mapped[dict] = mapped_column(JSON)
    findings: Mapped[list] = mapped_column(JSON)  # validation warnings kept with the log
    display_timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OcelEvent(Base):
    __tablename__ = "ocel_events"
    __table_args__ = (Index("ix_ocel_events_log_eid", "log_id", "event_id"), Index("ix_ocel_events_log_ts", "log_id", "ts"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("ocel_logs.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(255))
    ts: Mapped[datetime] = mapped_column(DateTime)  # UTC
    position: Mapped[int] = mapped_column(Integer)  # order in the source file (tie-breaker)
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Standard event fields used by the analyses (exported as org:resource, org:group, cost:total ...).
    start_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # UTC
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-based CSV row
    id_generated: Mapped[bool] = mapped_column(default=False, server_default="0")  # no event id in the source

class OcelObject(Base):
    __tablename__ = "ocel_objects"
    __table_args__ = (Index("ix_ocel_objects_log_oid", "log_id", "object_id"), Index("ix_ocel_objects_log_type", "log_id", "object_type"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("ocel_logs.id", ondelete="CASCADE"))
    object_id: Mapped[str] = mapped_column(String(255))
    object_type: Mapped[str] = mapped_column(String(255))


class OcelObjectAttribute(Base):
    """One attribute value of an object, valid from ``ts`` (OCEL 2.0 changing attributes)."""

    __tablename__ = "ocel_object_attributes"
    __table_args__ = (Index("ix_ocel_oattr_log_oid", "log_id", "object_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("ocel_logs.id", ondelete="CASCADE"))
    object_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    # Event whose source row reported this value (CSV/XES imports), if any.
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OcelE2O(Base):
    __tablename__ = "ocel_e2o"
    __table_args__ = (Index("ix_ocel_e2o_log_eid", "log_id", "event_id"), Index("ix_ocel_e2o_log_oid", "log_id", "object_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("ocel_logs.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(String(255))
    object_id: Mapped[str] = mapped_column(String(255))
    qualifier: Mapped[str] = mapped_column(String(255), default="")


class OcelO2O(Base):
    __tablename__ = "ocel_o2o"
    __table_args__ = (Index("ix_ocel_o2o_log_src", "log_id", "source_id"), Index("ix_ocel_o2o_log_tgt", "log_id", "target_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("ocel_logs.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(String(255))
    target_id: Mapped[str] = mapped_column(String(255))
    qualifier: Mapped[str] = mapped_column(String(255), default="")
