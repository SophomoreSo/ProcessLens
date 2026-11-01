"""ProcessLens Miner application: JSON API plus a single-page studio, served from one origin."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import PROJECT_ROOT
from app.config import get_settings as processlens_settings
from app.database import ensure_engine
from app.migrate import upgrade_to_head
from miner import __version__
from miner.api import install_error_handlers, router
from miner.config import get_settings
from miner.security import SecurityMiddleware, csrf_token
from miner.store import init_store

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pl = processlens_settings()
    pl.ensure_dirs()
    upgrade_to_head()  # the ProcessLens schema Miner reads (a no-op when ProcessLens is current)
    ensure_engine()
    init_store()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=processlens_settings().log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="ProcessLens Miner API", version=__version__, lifespan=lifespan,
                  description="Packages, knowledge models (KPIs and filters in PLQL), views and queries over "
                              "ProcessLens datasets. State-changing requests need the `X-CSRF-Token` header "
                              "(`GET /api/session`). No authentication: for this computer only.",
                  docs_url=None, redoc_url=None)
    install_error_handlers(app)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    # Cytoscape, dagre and Plotly are shared with ProcessLens (vendored, MIT; see app/static/vendor/README.md).
    app.mount("/vendor", StaticFiles(directory=str(PROJECT_ROOT / "app" / "static" / "vendor")), name="vendor")
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "csrf_token": csrf_token(), "version": __version__, "processlens_url": settings.processlens_url})

    return app


app = create_app()
