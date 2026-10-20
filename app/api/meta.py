from __future__ import annotations

from zoneinfo import available_timezones

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.database import get_db
from app.security import csrf_token
from app.services.cache import cache

router = APIRouter(prefix="/api", tags=["meta"])
_TIMEZONES = sorted(tz for tz in available_timezones() if "/" in tz or tz == "UTC")


@router.get("/health", summary="Liveness and database connectivity")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__, "database": "ok", "cache_entries": len(cache),
            "deployment": "single-user local (no authentication)"}


@router.get("/session", summary="CSRF token for state-changing requests (same-origin only)")
def session_info():
    return {"csrf_token": csrf_token(), "header": "X-CSRF-Token"}


@router.get("/meta/timezones", summary="IANA timezone names")
def timezones():
    return {"timezones": _TIMEZONES}
