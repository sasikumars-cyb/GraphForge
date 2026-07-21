"""Registers FastAPI exception handlers so every error response, expected or
not, has the same JSON shape:

    {"error": {"code": "not_found", "message": "..."}}

Three handlers are registered, from most to least specific:
  1. `AppError`               - expected application errors, logged at WARNING
  2. `RequestValidationError` - FastAPI/Pydantic request validation, 422
  3. `Exception`              - anything unanticipated, logged at ERROR with
                                 a traceback, and never leaks its message to
                                 the client
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    logger.warning(
        "Handled application error on %s: %s",
        request.url.path,
        exc.message,
    )
    return _error_response(exc.status_code, exc.error_code, exc.message)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    logger.info("Request validation failed on %s: %s", request.url.path, exc.errors())
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "Request validation failed.",
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s", request.url.path)
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "An unexpected error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = ["register_exception_handlers"]
