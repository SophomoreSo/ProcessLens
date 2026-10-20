"""Server-rendered page shells. Data is loaded by page scripts via the JSON API."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import PROJECT_ROOT
from app.security import csrf_token

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))
router = APIRouter(include_in_schema=False)

PAGES = [
    ("overview", "/", "Overview"),
    ("process", "/process", "Process explorer"),
    ("variants", "/variants", "Variants"),
    ("cases", "/cases", "Cases"),
    ("performance", "/performance", "Performance"),
    ("conformance", "/conformance", "Conformance"),
    ("alerts", "/alerts", "Alerts"),
    ("datasets", "/datasets", "Datasets"),
    ("import", "/import", "Import"),
]


def _render(request: Request, page: str, title: str) -> HTMLResponse:
    return templates.TemplateResponse(request, f"{page}.html", {
        "page": page, "title": title, "pages": PAGES, "csrf_token": csrf_token(), "version": __version__,
    })


def _make(page: str, title: str):
    def view(request: Request):
        return _render(request, page, title)
    view.__name__ = f"page_{page}"
    return view


for _page, _path, _title in PAGES:
    router.add_api_route(_path, _make(_page, _title), methods=["GET"], response_class=HTMLResponse)
