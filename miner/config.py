"""Miner settings from environment variables (prefix ``PROCESSLENS_MINER_``).

Miner reads datasets from the ProcessLens database (configured with the usual
``PROCESSLENS_*`` variables, e.g. ``PROCESSLENS_DATA_DIR``) and keeps its own
workspace (packages, knowledge models, views, saved queries) in a separate
database, by default ``<data dir>/miner.db``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.config import get_settings as processlens_settings


class MinerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROCESSLENS_MINER_", env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8100
    # Same rule as the ProcessLens admin site: Miner can change data and has no login.
    allow_non_loopback_bind: bool = False
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])
    database_url: str | None = None  # default: sqlite file next to the ProcessLens database
    # Address of the ProcessLens site, for the "Open in ProcessLens" links.
    processlens_url: str = "http://127.0.0.1:8000"

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, v):
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{processlens_settings().data_dir / 'miner.db'}"


@lru_cache
def get_settings() -> MinerSettings:
    return MinerSettings()
