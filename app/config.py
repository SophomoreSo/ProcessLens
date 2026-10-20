"""Application settings loaded from environment variables (prefix ``PROCESSLENS_``).

All secrets and deployment-specific values come from the environment or a local
``.env`` file; nothing sensitive is hard-coded.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROCESSLENS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ProcessLens"
    environment: str = "local"

    # --- Network binding -------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    # Binding to a non-loopback interface exposes an unauthenticated app.
    # The launcher refuses to do so unless this is explicitly set (Docker sets
    # it because the container port is published on 127.0.0.1 only).
    allow_non_loopback_bind: bool = False
    # Host headers accepted (protects against DNS-rebinding).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])

    # --- Storage ---------------------------------------------------------
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str | None = None  # default: sqlite file in data_dir

    # --- Security --------------------------------------------------------
    # Used to derive the CSRF token. If unset, a random key is generated per
    # process (tokens then change on restart, which only requires a reload).
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32))

    # --- Limits ----------------------------------------------------------
    max_upload_mb: int = 200
    max_rows: int = 1_000_000
    max_columns: int = 200
    max_field_chars: int = 2_000
    max_findings_stored: int = 100_000

    # --- Analytics -------------------------------------------------------
    cache_max_entries: int = 256
    log_level: str = "INFO"

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, v):
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    @field_validator("secret_key", mode="before")
    @classmethod
    def _secret(cls, v):
        # An empty value (e.g. "PROCESSLENS_SECRET_KEY=" in .env) must not yield a predictable key.
        return v if isinstance(v, str) and v.strip() else secrets.token_urlsafe(32)

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'processlens.db').as_posix()}"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
