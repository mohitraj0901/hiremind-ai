"""
Structured logging configuration.

Why JSON logs instead of plain text:
  - This app makes concurrent, async calls to multiple external services
    (Gemini, Deepgram, Judge0, MongoDB). Plain-text logs become unreadable
    once requests interleave. JSON logs can be shipped to any log
    aggregator (CloudWatch, Datadog, Railway/Render's own log viewer) and
    queried/filtered reliably.
  - Every log line includes a `request_id` (bound via middleware in
    main.py), so all logs for a single HTTP request — or a single
    interview turn — can be correlated even under concurrent load.

This module intentionally has zero external dependencies (no
python-json-logger, no structlog) to keep Module 1's dependency
footprint minimal; the JSON formatter below is ~30 lines and gives us
full control over the schema.
"""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings

# Context variable holding the current request's correlation ID.
# Set by RequestContextMiddleware in main.py at the start of each request
# and automatically picked up by every log record emitted during that
# request's lifecycle — no need to pass request_id through every function call.
request_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class JSONLogFormatter(logging.Formatter):
    """Renders each LogRecord as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx_var.get(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Attach exception info (stack trace) when logging via logger.exception()
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        # Allow callers to attach arbitrary structured context, e.g.:
        #   logger.info("interview started", extra={"extra_fields": {"user_id": "..."}})
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            log_payload.update(extra_fields)

        return json.dumps(log_payload, default=str)


def configure_logging() -> None:
    """
    Configures the root logger once at application startup.

    Called from the FastAPI lifespan handler in main.py before anything
    else runs, so every subsequent log line — including startup/shutdown
    events — uses the structured JSON format.
    """
    settings = get_settings()

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)

    # Remove any handlers uvicorn/other libs may have already attached,
    # so we don't get duplicate or inconsistently-formatted log lines.
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONLogFormatter())
    root_logger.addHandler(handler)

    # Tame noisy third-party loggers to WARNING so our own logs aren't
    # drowned out, while still surfacing real problems from those libs.
    for noisy_logger in ("uvicorn.access", "pymongo", "motor"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Standard logger accessor used throughout the app, e.g.:
        logger = get_logger(__name__)
    Keeping this as a thin wrapper (rather than importing `logging`
    directly everywhere) gives us one place to change logger behavior
    later (e.g. injecting a default `extra_fields` context) without
    touching every call site.
    """
    return logging.getLogger(name)
