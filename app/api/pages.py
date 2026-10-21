"""Server-rendered page shells. Data is loaded by page scripts via the JSON API."""

from __future__ import annotations

from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import PROJECT_ROOT
from app.i18n import LANGUAGES, LOCALE_TAGS, client_catalog, t
from app.security import csrf_token
from app.services.preferences import get_prefs

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))
router = APIRouter(include_in_schema=False)

# (template / nav key, path). Labels come from the catalog: nav.<key>.
# Entries with key "|" are navigation group headings (nav.group.<path>), not pages.
PAGES = [
    ("overview", "/"),
    ("process", "/process"),
    ("variants", "/variants"),
    ("cases", "/cases"),
    ("performance", "/performance"),
    ("conformance", "/conformance"),
    ("alerts", "/alerts"),
    ("|", "mining"),
    ("discovery", "/discovery"),
    ("compare", "/compare"),
    ("|", "data"),
    ("datasets", "/datasets"),
    ("import", "/import"),
    ("settings", "/settings"),
]


def _render(request: Request, page: str) -> HTMLResponse:
    prefs = get_prefs()
    lang = prefs.language
    tr = partial(t, lang)
    return templates.TemplateResponse(request, f"{page}.html", {
        "page": page, "title": tr(f"nav.{page}"), "pages": PAGES, "csrf_token": csrf_token(),
        "version": __version__, "t": tr, "lang": lang, "locale_tag": LOCALE_TAGS[lang], "motion": prefs.motion,
        "languages": LANGUAGES, "catalog": client_catalog(lang),
    })


def _make(page: str):
    def view(request: Request):
        return _render(request, page)
    view.__name__ = f"page_{page}"
    return view


for _page, _path in PAGES:
    if _page == "|":
        continue
    router.add_api_route(_path, _make(_page), methods=["GET", "HEAD"], response_class=HTMLResponse)
