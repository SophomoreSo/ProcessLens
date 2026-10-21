"""FastAPI application factory. Serves the API and the frontend from one origin."""

from __future__ import annotations

import atexit
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api import (
    alerts,
    analytics,
    conformance,
    datasets,
    exports,
    guide,
    imports,
    meta,
    mining,
    ocel,
    pages,
    preferences,
    tasks,
)
from app.config import PROJECT_ROOT, get_settings
from app.database import ensure_engine, init_engine
from app.errors import install_error_handlers
from app.migrate import upgrade_to_head
from app.mining.runs import recover_interrupted_runs
from app.mining.worker import worker as mining_worker
from app.security import GuestReadOnlyMiddleware, SecurityMiddleware
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

GUEST_DESCRIPTION = """
Read-only guest site of ProcessLens. Every request that would change data is refused
(HTTP 403 `read_only`); the only accepted POST is the stateless cohort comparison.
"""
ROUTERS = (meta, preferences, imports, datasets, analytics, conformance, alerts, exports, mining, ocel, tasks, guide)


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
    recover_interrupted_runs()
    yield


@asynccontextmanager
async def guest_lifespan(_app: FastAPI):
    """The guest site shares the admin site's database; it never recovers or changes jobs and runs."""
    settings = get_settings()
    settings.ensure_dirs()
    upgrade_to_head()
    ensure_engine()
    yield


def create_app(role: str = "admin") -> FastAPI:
    """``admin``: full read/write site for the local user. ``guest``: read-only site for other people."""
    if role not in ("admin", "guest"):
        raise ValueError(role)
    settings = get_settings()
    configure_logging(settings.log_level)
    guest = role == "guest"
    app = FastAPI(title="ProcessLens API" + (" (guest, read-only)" if guest else ""), version=__version__,
                  description=GUEST_DESCRIPTION if guest else DESCRIPTION,
                  lifespan=guest_lifespan if guest else lifespan,
                  docs_url=None if guest else "/docs", redoc_url=None if guest else "/redoc",
                  openapi_url=None if guest else "/openapi.json")
    app.state.role = role
    install_error_handlers(app)
    app.add_middleware(SecurityMiddleware)
    if guest:
        app.add_middleware(GuestReadOnlyMiddleware)
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=settings.guest_allowed_hosts if guest else settings.allowed_hosts)
    app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "app" / "static")), name="static")
    for module in ROUTERS:
        app.include_router(module.router)
    app.include_router(conformance.summary_router)
    app.include_router(pages.guest_router if guest else pages.router)
    return app


app = create_app()
guest_app = create_app("guest")
atexit.register(mining_worker.kill)  # stop the isolated mining worker process on exit
