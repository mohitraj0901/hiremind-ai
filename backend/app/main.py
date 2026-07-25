"""
Application entrypoint.

Uses the app-factory pattern (`create_app()`) rather than a bare
module-level `app = FastAPI()`. This means:
  - Tests can call `create_app()` to get a fresh app instance wired
    against a test database/settings, instead of importing a
    already-configured global.
  - Startup/shutdown wiring (DB connection, logging config) lives in
    one place (`lifespan`) instead of scattered `@app.on_event` calls,
    which are deprecated in modern FastAPI.

Run locally with:
    poetry run uvicorn app.main:app --reload
"""

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.routes.auth import router as auth_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import configure_logging, get_logger, request_id_ctx_var
from app.db.indexes import create_indexes
from app.db.mongodb import mongo_db

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manages resources that must be set up once at process startup and
    torn down cleanly at shutdown — currently just logging config and
    the MongoDB connection. Later modules (e.g. a warm Gemini client,
    a vector store connection) will initialize here too.
    """
    configure_logging()
    logger.info("app_startup_begin")

    await mongo_db.connect()
    await create_indexes(mongo_db.get_database())

    logger.info("app_startup_complete")
    yield  # ---- app runs here ----

    logger.info("app_shutdown_begin")
    await mongo_db.close()
    logger.info("app_shutdown_complete")


def create_app() -> FastAPI:
    """
    Builds and configures the FastAPI application instance.
    This is the single place where routers, middleware, and exception
    handlers get wired together — every future module's routes get
    registered here (see the TODO-free placeholder comment below,
    which will be replaced by real `app.include_router(...)` calls as
    each module's routes are built).
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Production-grade HireMind AI — conducts adaptive, "
            "AI-driven mock interviews with speech support, real-time "
            "evaluation, and personalized feedback."
        ),
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    _configure_middleware(app, settings)
    register_exception_handlers(app)
    _register_routes(app)

    return app


def _configure_middleware(app: FastAPI, settings) -> None:  # type: ignore[no-untyped-def]
    """CORS + request-correlation-ID + request-timing middleware."""

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        """
        Assigns a unique request ID to every incoming request, stores it
        in a ContextVar (so every log line emitted during this request
        automatically includes it — see logging_config.py), and returns
        it to the client via a response header for client-side tracing.

        Also logs request duration, which is the cheapest possible
        performance signal to have in place before any real traffic hits
        the app.
        """
        request_id = str(uuid.uuid4())
        token = request_id_ctx_var.set(request_id)
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # Let the registered exception handlers deal with the actual
            # error response; we only need this try/except so we can log
            # timing/context even when a request fails.
            logger.exception("request_failed", extra={"extra_fields": {"path": request.url.path}})
            raise
        finally:
            request_id_ctx_var.reset(token)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                }
            },
        )
        return response


def _register_routes(app: FastAPI) -> None:
    """
    Registers all API routers under the versioned API prefix.

    Each module adds one `include_router` line here, keeping route
    registration centralized and easy to audit.
    """
    settings = get_settings()

    app.include_router(auth_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["System"], summary="Liveness and DB connectivity check")
    async def health_check() -> JSONResponse:
        """
        Used by Railway/Render/Vercel-adjacent uptime checks and by
        developers to verify the deployed app can actually reach
        MongoDB Atlas — not just that the process is running.
        """
        db_status = "unknown"
        try:
            db = mongo_db.get_database()
            await db.command("ping")
            db_status = "connected"
        except Exception:
            db_status = "unreachable"
            logger.error("health_check_db_ping_failed", exc_info=True)

        overall_status = "ok" if db_status == "connected" else "degraded"
        status_code = 200 if overall_status == "ok" else 503

        return JSONResponse(
            status_code=status_code,
            content={
                "status": overall_status,
                "app": settings.app_name,
                "version": settings.app_version,
                "environment": settings.environment,
                "database": db_status,
            },
        )


app = create_app()
