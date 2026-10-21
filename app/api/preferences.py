from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.i18n import LANGUAGES
from app.services.preferences import get_prefs, update_prefs

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

Language = Literal["en", "ko", "pl"]
Motion = Literal["system", "on", "off"]


class PreferencesOut(BaseModel):
    language: Language
    motion: Motion
    languages: dict[str, str]


class PreferencesUpdate(BaseModel):
    language: Language | None = None
    motion: Motion | None = None


def _out(prefs) -> PreferencesOut:
    return PreferencesOut(language=prefs.language, motion=prefs.motion, languages=LANGUAGES)


@router.get("", response_model=PreferencesOut, summary="User settings: language and animations")
def read_preferences():
    return _out(get_prefs())


@router.put("", response_model=PreferencesOut, summary="Update user settings")
def write_preferences(body: PreferencesUpdate, db: Session = Depends(get_db)):
    return _out(update_prefs(db, language=body.language, motion=body.motion))
