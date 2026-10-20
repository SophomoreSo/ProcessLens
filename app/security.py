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
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

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


def csrf_token() -> str:
    secret = get_settings().secret_key.encode("utf-8")
    return hmac.new(secret, b"processlens-csrf-v1", hashlib.sha256).hexdigest()


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin != "null":
                if urlsplit(origin).netloc != request.headers.get("host", ""):
                    return JSONResponse({"error": {"code": "forbidden_origin",
                                                   "message": "Cross-origin requests are not allowed.",
                                                   "details": None}}, status_code=403)
            token = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(token, csrf_token()):
                return JSONResponse({"error": {"code": "csrf_failed",
                                               "message": "Missing or invalid CSRF token. Reload the page and retry.",
                                               "details": None}}, status_code=403)
        response = await call_next(request)
        is_docs = path in ("/docs", "/redoc") or path.startswith("/docs/")
        response.headers.setdefault("Content-Security-Policy", DOCS_CSP if is_docs else APP_CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
