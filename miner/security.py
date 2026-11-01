"""Local-deployment protections, the same as the ProcessLens admin site (see app/security.py):
CSRF header on state-changing API requests, Origin check, Host allow-list (in main.py) and a
restrictive Content-Security-Policy. This is not authentication: Miner is for this computer only."""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings as processlens_settings
from app.security import APP_CSP, SAFE_METHODS


def csrf_token() -> str:
    """Differs from the ProcessLens tokens, so a Miner page never reveals a token ProcessLens accepts."""
    secret = processlens_settings().secret_key.encode("utf-8")
    return hmac.new(secret, b"processlens-miner-csrf-v1", hashlib.sha256).hexdigest()


def _forbidden(code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": None}}, status_code=403)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin != "null" and urlsplit(origin).netloc != request.headers.get("host", ""):
                return _forbidden("forbidden_origin", "Requests from other sites are not accepted.")
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf_token()):
                return _forbidden("csrf_failed", "The page is out of date. Reload it and try again.")
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", APP_CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith(("/static/", "/vendor/")):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response
