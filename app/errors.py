"""Uniform error responses: ``{"error": {"code", "message", "details"}}``."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.i18n import has_key, msg, t
from app.services.ingestion import IngestionError
from app.services.preferences import language_for

log = logging.getLogger("processlens.errors")


class AppError(Exception):
    """API error. The message is the catalog entry ``key`` (default ``error.<code>``)
    rendered in the request language; ``message`` (if given) is passed as ``{detail}``."""

    def __init__(self, status_code: int, code: str, message: str | None = None, details=None,
                 key: str | None = None, **params):
        self.status_code = status_code
        self.code = code
        self.key = key or f"error.{code}"
        if message is not None:
            params.setdefault("detail", message)
        self.params = params
        self.message = t("en", self.key, **params) if has_key(self.key) else (message or code)
        super().__init__(self.message)
        self.details = details

    def localized(self, lang: str) -> str:
        return t(lang, self.key, **self.params) if has_key(self.key) else self.message


def not_found(entity: str) -> AppError:
    """``entity`` is a catalog suffix, e.g. ``dataset`` -> ``entity.dataset``."""
    return AppError(404, "not_found", what=msg(f"entity.{entity}"))


def _body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def _clean_validation_errors(errors: list) -> list:
    out = []
    for e in errors:
        out.append({"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")})
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(req: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.localized(language_for(req)), exc.details),
                            status_code=exc.status_code)

    @app.exception_handler(IngestionError)
    async def _ingestion_error(req: Request, exc: IngestionError):
        status = 413 if exc.code == "file_too_large" else 422
        return JSONResponse(_body(exc.code, t(language_for(req), f"error.{exc.code}", **exc.params),
                                  getattr(exc, "details", None)), status_code=status)

    @app.exception_handler(RequestValidationError)
    async def _validation(req: Request, exc: RequestValidationError):
        errs = _clean_validation_errors(exc.errors())
        first = errs[0]["msg"] if errs else "-"
        return JSONResponse(_body("validation_error", t(language_for(req), "error.validation_error", detail=first),
                                  errs), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(req: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed", 403: "forbidden", 413: "payload_too_large"}.get(
            exc.status_code, "http_error")
        key = f"error.http_{exc.status_code}"
        message = t(language_for(req), key) if has_key(key) else str(exc.detail)
        return JSONResponse(_body(code, message), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception):
        log.exception("Unhandled error: %s", type(exc).__name__)
        return JSONResponse(_body("internal_error", t(language_for(req), "error.internal_error")), status_code=500)
