"""The local user's preferences (language, animations), stored in the database."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.i18n import DEFAULT_LANGUAGE, LANGUAGES
from app.models import UserPreferences

MOTION_CHOICES = ("system", "on", "off")


@dataclass(frozen=True)
class Prefs:
    language: str = DEFAULT_LANGUAGE
    motion: str = "system"


_lock = threading.Lock()
_cached: Prefs | None = None


def _row(db: Session) -> UserPreferences:
    row = db.get(UserPreferences, 1)
    if row is None:
        row = UserPreferences(id=1, language=DEFAULT_LANGUAGE, motion="system")
        db.add(row)
        db.commit()
    return row


def get_prefs() -> Prefs:
    """Current preferences (cached in memory; the app has a single user and process)."""
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
    with SessionLocal() as db:
        row = _row(db)
        prefs = Prefs(language=row.language if row.language in LANGUAGES else DEFAULT_LANGUAGE,
                      motion=row.motion if row.motion in MOTION_CHOICES else "system")
    with _lock:
        _cached = prefs
    return prefs


def update_prefs(db: Session, language: str | None = None, motion: str | None = None) -> Prefs:
    global _cached
    row = _row(db)
    if language is not None:
        row.language = language
    if motion is not None:
        row.motion = motion
    db.commit()
    with _lock:
        _cached = Prefs(language=row.language, motion=row.motion)
        return _cached


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None


LANGUAGE_HEADER = "X-ProcessLens-Language"


def language_for(request) -> str:
    """Language for an API response: explicit header (sent by the UI), else the saved preference."""
    header = request.headers.get(LANGUAGE_HEADER)
    if header and header.strip().lower() in LANGUAGES:
        return header.strip().lower()
    try:
        return get_prefs().language
    except Exception:  # database not ready (e.g. during start-up errors)
        return DEFAULT_LANGUAGE
