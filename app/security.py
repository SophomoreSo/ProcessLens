"""Local-deployment protections.

* CSRF: every state-changing ``/api`` request must carry ``X-CSRF-Token``
  matching an HMAC of the server secret. Browsers cannot add that header to
  cross-site form posts, and without CORS other origins cannot read the token.
* Origin check: if an ``Origin`` header is present it must match the request host.
* Host header allow-list (TrustedHostMiddleware, configured in main.py) blocks
  DNS-rebinding attacks against the localhost server.
* Security headers including a restrictive Content-Security-Policy.

This is not authentication. A shared deployment needs real authentication,
authorization, TLS and tenant isolation (see README "Deployment boundaries").
"""

from __future__ import annotations

import hashlib
import hmac
import re
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.i18n import t
from app.services.preferences import language_for

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# The guest site is read-only. The only non-GET request it accepts computes a
# cohort comparison without storing anything.
GUEST_ALLOWED_WRITES = (re.compile(r"^/api/datasets/\d+/performance/compare$"),)
# Admin-only read endpoints (staged raw upload files are part of the admin import workflow).
GUEST_BLOCKED_PREFIXES = ("/api/uploads", "/api/jobs")

APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)
# FastAPI's interactive docs load Swagger UI / ReDoc assets from jsDelivr.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com; worker-src 'self' blob:; connect-src 'self'; "
    "object-src 'none'; frame-ancestors 'none'"
)


def csrf_token(role: str = "admin") -> str:
    """Per-site token: the guest site's pages never reveal the admin site's token."""
    secret = get_settings().secret_key.encode("utf-8")
    label = b"processlens-csrf-v1" if role == "admin" else b"processlens-guest-csrf-v1"
    return hmac.new(secret, label, hashlib.sha256).hexdigest()


def request_role(request: Request) -> str:
    return getattr(getattr(request.scope.get("app"), "state", None), "role", "admin")


def _forbidden(request: Request, code: str) -> JSONResponse:
    message = t(language_for(request), f"error.{code}")
    return JSONResponse({"error": {"code": code, "message": message, "details": None}}, status_code=403)


class GuestReadOnlyMiddleware(BaseHTTPMiddleware):
    """Enforced on the server for the guest site, whatever the page offers: no request may change data."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method not in SAFE_METHODS and not any(p.match(path) for p in GUEST_ALLOWED_WRITES):
            return _forbidden(request, "read_only")
        if path.startswith(GUEST_BLOCKED_PREFIXES):
            return _forbidden(request, "read_only")
        return await call_next(request)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin != "null":
                if urlsplit(origin).netloc != request.headers.get("host", ""):
                    return _forbidden(request, "forbidden_origin")
            token = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(token, csrf_token(request_role(request))):
                return _forbidden(request, "csrf_failed")
        response = await call_next(request)
        is_docs = path in ("/docs", "/redoc") or path.startswith("/docs/")
        response.headers.setdefault("Content-Security-Policy", DOCS_CSP if is_docs else APP_CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith("/static/"):
            # Revalidate (cheap ETag check) so upgrades never run stale scripts.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response
