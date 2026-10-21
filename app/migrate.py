"""Programmatic ``alembic upgrade head`` used at application start-up."""

from __future__ import annotations

import threading

from alembic import command
from alembic.config import Config

from app.config import PROJECT_ROOT, get_settings


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
    command.upgrade(cfg, "head")
