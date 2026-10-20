"""Programmatic ``alembic upgrade head`` used at application start-up."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.config import PROJECT_ROOT, get_settings


def upgrade_to_head(database_url: str | None = None) -> None:
    settings = get_settings()
    settings.ensure_dirs()
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url or settings.resolved_database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
