"""Translation catalogs for the UI and for server-generated messages.

Catalogs are flat JSON files in ``app/i18n/locales/<lang>.json`` with
``{placeholder}`` parameters. English is the reference catalog; a missing key
falls back to English, then to the key itself.

Server-generated messages are passed around as ``(key, params)`` and rendered
per request language. A parameter value of the form ``{"$key": ..., "$params":
{...}}`` is rendered recursively, so messages can embed other translatable
messages (e.g. an alert that quotes a conformance explanation).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

LANGUAGES: dict[str, str] = {"en": "English", "ko": "한국어", "pl": "Polski"}
DEFAULT_LANGUAGE = "en"
# BCP 47 tags used for Intl/number formatting in the browser
LOCALE_TAGS = {"en": "en-US", "ko": "ko-KR", "pl": "pl-PL"}
LOCALES_DIR = Path(__file__).parent / "locales"


def normalize_language(value: str | None) -> str:
    if not value:
        return DEFAULT_LANGUAGE
    code = value.strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


@lru_cache
def catalog(lang: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


class _Params(dict):
    def __missing__(self, key):  # leave unknown placeholders visible instead of failing
        return "{" + key + "}"


def msg(key: str, **params: Any) -> dict:
    """A translatable message reference usable as a parameter of another message."""
    return {"$key": key, "$params": params}


def t(lang: str, key: str, **params: Any) -> str:
    lang = normalize_language(lang)
    template = catalog(lang).get(key) or catalog(DEFAULT_LANGUAGE).get(key)
    if template is None:
        return key
    rendered = _Params()
    for name, value in params.items():
        if isinstance(value, dict) and "$key" in value:
            rendered[name] = t(lang, value["$key"], **(value.get("$params") or {}))
        elif isinstance(value, float):
            rendered[name] = str(int(value)) if value.is_integer() else str(round(value, 2))
        else:
            rendered[name] = value
    try:
        return template.format_map(rendered)
    except (ValueError, IndexError):  # malformed template: never break a response
        return template


def has_key(key: str) -> bool:
    return key in catalog(DEFAULT_LANGUAGE)


def render(lang: str, key: str | None, params: dict | None, fallback: str | None = None) -> str:
    """Render a stored (key, params) pair, falling back to stored text for legacy rows."""
    if key and has_key(key):
        return t(lang, key, **(params or {}))
    return fallback if fallback is not None else (key or "")


def join_or(lang: str, items: list[str]) -> str:
    quoted = [f"'{i}'" for i in items]
    if len(quoted) <= 1:
        return "".join(quoted)
    return t(lang, "common.list_or", head=", ".join(quoted[:-1]), last=quoted[-1])


def client_catalog(lang: str) -> dict[str, str]:
    """Catalog shipped to the browser: English merged with the selected language."""
    merged = dict(catalog(DEFAULT_LANGUAGE))
    merged.update(catalog(normalize_language(lang)))
    return merged
