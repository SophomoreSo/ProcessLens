"""Miner's own database: packages with their knowledge models, views and saved queries.

Kept apart from the ProcessLens database so Miner never writes to it. The schema
is created on start-up (``init_store``); ``miner_schema.version`` records it for
future upgrades.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from miner.config import get_settings

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class SchemaInfo(Base):
    __tablename__ = "miner_schema"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class Package(Base):
    """A workspace for one ProcessLens dataset seen through one lead object type."""

    __tablename__ = "miner_packages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    dataset_id: Mapped[int] = mapped_column(Integer)  # ProcessLens dataset (another database: no foreign key)
    object_type: Mapped[str | None] = mapped_column(String(255), nullable=True)  # None = the dataset's main type
    base_filter: Mapped[str] = mapped_column(Text, default="")  # PLQL condition applied everywhere in the package
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    kpis: Mapped[list[KpiDef]] = relationship(back_populates="package", cascade="all, delete-orphan",
                                              order_by="KpiDef.position")
    filters: Mapped[list[FilterDef]] = relationship(back_populates="package", cascade="all, delete-orphan",
                                                    order_by="FilterDef.position")
    views: Mapped[list[ViewDef]] = relationship(back_populates="package", cascade="all, delete-orphan",
                                                order_by="ViewDef.position")
    queries: Mapped[list[SavedQuery]] = relationship(back_populates="package", cascade="all, delete-orphan",
                                                     order_by="SavedQuery.id")


class KpiDef(Base):
    __tablename__ = "miner_kpis"
    __table_args__ = (UniqueConstraint("package_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("miner_packages.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    formula: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(20), default="auto")
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    higher_is_better: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    package: Mapped[Package] = relationship(back_populates="kpis")


class FilterDef(Base):
    __tablename__ = "miner_filters"
    __table_args__ = (UniqueConstraint("package_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("miner_packages.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    formula: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    package: Mapped[Package] = relationship(back_populates="filters")


class ViewDef(Base):
    """A dashboard: a grid of components (KPI tiles, charts, tables, process graph, variants, text)."""

    __tablename__ = "miner_views"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("miner_packages.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    filter: Mapped[str] = mapped_column(Text, default="")
    components: Mapped[list] = mapped_column(JSON, default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    package: Mapped[Package] = relationship(back_populates="views")


class SavedQuery(Base):
    __tablename__ = "miner_queries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("miner_packages.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    package: Mapped[Package] = relationship(back_populates="queries")


# --------------------------------------------------------------------------- engine
_engine: Engine | None = None
_engine_url: str | None = None
_Session: sessionmaker[Session] | None = None


def init_store(url: str | None = None) -> Engine:
    """Create (or re-point) the engine and the schema. Idempotent; tests call it per temporary data dir."""
    global _engine, _engine_url, _Session
    url = url or get_settings().resolved_database_url
    if _engine is not None and url == _engine_url:
        return _engine
    if _engine is not None:
        _engine.dispose()
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        if s.get(SchemaInfo, "version") is None:
            s.add(SchemaInfo(key="version", value=str(SCHEMA_VERSION)))
            s.commit()
    _engine, _engine_url = engine, url
    _Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return engine


def get_store() -> Iterator[Session]:
    """FastAPI dependency for the Miner database."""
    init_store()  # no-op unless the configured location changed
    assert _Session is not None
    session = _Session()
    try:
        yield session
    finally:
        session.close()
