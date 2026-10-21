"""FastAPI application factory. Serves the API and the frontend from one origin."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api import alerts, analytics, conformance, datasets, exports, imports, meta, pages, preferences
from app.config import PROJECT_ROOT, get_settings
from app.database import init_engine
from app.errors import install_error_handlers
from app.migrate import upgrade_to_head
from app.security import SecurityMiddleware
from app.services.jobs import recover_interrupted_jobs
from app.services.preferences import reset_cache as reset_preferences_cache

DESCRIPTION = """
ProcessLens is a local-first, single-user process-mining workbench for case-centric event logs.

* **Filters** are case-level: matching cases keep their complete event histories. All analysis
  endpoints accept the same filter query parameters.
* **Durations** are in seconds; timestamps are UTC ISO-8601.
* **State-changing requests** require the `X-CSRF-Token` header (see `GET /api/session`).
* This API has **no authentication**; do not expose it beyond localhost.
"""


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    upgrade_to_head()
    init_engine()
    reset_preferences_cache()
    recover_interrupted_jobs()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="ProcessLens API", version=__version__, description=DESCRIPTION, lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "app" / "static")), name="static")
    for module in (meta, preferences, imports, datasets, analytics, conformance, alerts, exports):
        app.include_router(module.router)
    app.include_router(conformance.summary_router)
    app.include_router(pages.router)
    return app


app = create_app()
