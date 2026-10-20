"""Database engine/session setup.

SQLite is the default for local installs; any SQLAlchemy URL (for example
``postgresql+psycopg://...``) can be supplied via ``PROCESSLENS_DATABASE_URL``.
All queries go through SQLAlchemy Core/ORM constructs with bound parameters.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


def init_engine(url: str | None = None) -> Engine:
    """(Re)create the global engine. Tests call this with a temp database."""
    global _engine, _SessionLocal
    settings = get_settings()
    url = url or settings.resolved_database_url
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        _configure_sqlite(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def SessionLocal() -> Session:  # noqa: N802 - factory used like a class
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
