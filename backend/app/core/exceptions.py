"""
Domain exception hierarchy + FastAPI exception handlers.

Why this exists:
  Without it, an unhandled error anywhere in a service (e.g. "user not
  found" three layers deep in the resume service) either crashes with a
  raw 500 and a stack trace leaking to the client, or forces every
  route handler to write its own try/except HTTPException boilerplate.

  Instead, services and repositories raise semantically meaningful
  exceptions (`NotFoundException`, `UnauthorizedException`, ...) with no
  knowledge of HTTP at all. A single set of exception handlers,
  registered once in main.py, translates those into consistent JSON
  error responses. This keeps business logic decoupled from the web
  framework — a service function is just as reusable from a background
  worker or a CLI script as it is from an API route.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class AppException(Exception):
    """
    Base class for all application-raised (as opposed to framework- or
    library-raised) exceptions. Carries an HTTP status code and a
    machine-readable error code so the frontend can branch on
    `error.code` instead of parsing human-readable messages.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


class NotFoundException(AppException):
    """Raised when a requested resource (user, interview, resume, ...) doesn't exist."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class UnauthorizedException(AppException):
    """Raised when authentication is missing or invalid (not the same as forbidden)."""

    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "unauthorized"


class ForbiddenException(AppException):
    """Raised when an authenticated user lacks permission for the requested action."""

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "forbidden"


class ConflictException(AppException):
    """Raised on uniqueness violations, e.g. signing up with an email already in use."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"


class ValidationException(AppException):
    """
    Raised for business-rule validation failures that aren't already
    caught by Pydantic request-schema validation (e.g. "resume file
    must be a PDF or DOCX").
    """

    # Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY -> _CONTENT; fall back
    # for compatibility with older Starlette versions pinned elsewhere.
    status_code = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", None) or status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "validation_error"


class ExternalServiceException(AppException):
    """
    Raised when a downstream dependency (Gemini, Deepgram, Judge0, Mongo
    Atlas itself) fails or times out. Kept distinct from `AppException`'s
    generic 500 so logs and alerting can distinguish "our bug" from
    "a vendor is down."
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "external_service_error"


def _error_response(status_code: int, error_code: str, message: str) -> JSONResponse:
    """Builds the consistent error envelope returned to every client."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": error_code, "message": message}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """
    Registers all exception handlers on the FastAPI app instance.
    Called once from the app factory in main.py.
    """

    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        logger.warning(
            "handled_app_exception",
            extra={
                "extra_fields": {
                    "error_code": exc.error_code,
                    "path": request.url.path,
                    "details": exc.details,
                }
            },
        )
        return _error_response(exc.status_code, exc.error_code, exc.message)

    @app.exception_handler(Exception)
    async def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        # Anything reaching here is a genuine bug (not an anticipated
        # domain error), so we log it at ERROR with the full traceback
        # but still never leak internals to the client.
        logger.error(
            "unhandled_exception",
            exc_info=True,
            extra={"extra_fields": {"path": request.url.path}},
        )
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "An unexpected error occurred. Please try again later.",
        )
