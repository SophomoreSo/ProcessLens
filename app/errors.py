"""Uniform error responses: ``{"error": {"code", "message", "details"}}``."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.ingestion import IngestionError

log = logging.getLogger("processlens.errors")


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def not_found(what: str) -> AppError:
    return AppError(404, "not_found", f"{what} not found")


def _body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def _clean_validation_errors(errors: list) -> list:
    out = []
    for e in errors:
        out.append({"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")})
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_req: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(IngestionError)
    async def _ingestion_error(_req: Request, exc: IngestionError):
        status = 413 if exc.code == "file_too_large" else 422
        return JSONResponse(_body(exc.code, exc.message), status_code=status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        errs = _clean_validation_errors(exc.errors())
        first = errs[0]["msg"] if errs else "Invalid request"
        return JSONResponse(_body("validation_error", f"Invalid request: {first}", errs), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed", 403: "forbidden", 413: "payload_too_large"}.get(
            exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):
        log.exception("Unhandled error: %s", type(exc).__name__)
        return JSONResponse(_body("internal_error", "An internal error occurred. See server logs."), status_code=500)
