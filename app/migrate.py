"""Programmatic ``alembic upgrade head`` used at application start-up."""

from __future__ import annotations

import logging
from contextlib import closing
import sqlite3
import threading
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import PROJECT_ROOT, get_settings


log = logging.getLogger("processlens.migrate")
# Revisions that convert data in place: the SQLite file is copied first (``<db>.bak-<rev>``).
BACKUP_BEFORE = ("0007",)

# Alembic keeps its migration context in module globals, so concurrent upgrades (the admin
# and guest sites start in parallel threads of one process) must not overlap.
_lock = threading.Lock()


def upgrade_to_head(database_url: str | None = None) -> None:
    with _lock:
        _upgrade(database_url)


def _upgrade(database_url: str | None) -> None:
    settings = get_settings()
    settings.ensure_dirs()
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url or settings.resolved_database_url)
    cfg.attributes["configure_logger"] = False
    _backup_if_needed(database_url or settings.resolved_database_url)
    command.upgrade(cfg, "head")


def _current_revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("alembic_version"):
                return None
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def _backup_if_needed(url: str) -> None:
    """Copy an existing SQLite database before a revision that converts its data."""
    if not url.startswith("sqlite:///"):
        return
    path = Path(url.removeprefix("sqlite:///"))
    if not path.exists():
        return
    current = _current_revision(url)
    if current is None:
        return
    for rev in BACKUP_BEFORE:
        if current < rev:
            target = path.with_name(f"{path.name}.bak-{rev}")
            if not target.exists():
                # SQLite's backup API also includes changes still in the write-ahead log.
                with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
                log.info("Copied the database to %s before migration %s", target, rev)
