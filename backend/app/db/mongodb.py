"""
MongoDB Atlas connection lifecycle management (async, via Motor).

Why a class instead of a bare global client:
  - Encapsulates the client/database objects behind explicit `connect()`
    and `close()` methods that the FastAPI `lifespan` handler calls
    exactly once each, at startup and shutdown.
  - `get_database()` raises a clear error if called before `connect()`
    has run, instead of a confusing `NoneType has no attribute` error
    somewhere deep in a repository.
  - Every repository (Module 2+) imports `get_database` from here rather
    than constructing its own client — a single shared connection pool
    for the whole process, which is what Motor is designed for.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class MongoDB:
    """Holds the process-wide Motor client and database handle."""

    client: AsyncIOMotorClient | None = None
    database: AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        """
        Opens the connection to MongoDB Atlas and verifies it's reachable
        via a `ping` command. Called once from the FastAPI lifespan
        startup phase.

        We fail fast here deliberately: if the database is unreachable,
        we want the app to refuse to start (and show up clearly in
        deploy logs) rather than start "successfully" and fail on the
        first incoming request.
        """
        settings = get_settings()
        logger.info("mongodb_connecting", extra={"extra_fields": {"db": settings.mongodb_db_name}})

        self.client = AsyncIOMotorClient(settings.mongodb_uri)
        self.database = self.client[settings.mongodb_db_name]

        # `ping` is the recommended lightweight way to verify connectivity
        # and auth without touching any real collection.
        await self.client.admin.command("ping")

        logger.info("mongodb_connected", extra={"extra_fields": {"db": settings.mongodb_db_name}})

    async def close(self) -> None:
        """Closes the Motor client cleanly. Called from lifespan shutdown."""
        if self.client is not None:
            self.client.close()
            logger.info("mongodb_connection_closed")

    def get_database(self) -> AsyncIOMotorDatabase:
        """
        Returns the active database handle. Repositories call this
        (indirectly, via the `get_db` FastAPI dependency defined below)
        rather than importing `mongo_db.database` directly, so the
        access pattern stays consistent and mockable in tests.
        """
        if self.database is None:
            raise RuntimeError(
                "MongoDB database accessed before connection was established. "
                "Ensure MongoDB.connect() has run (it runs in the app lifespan startup)."
            )
        return self.database


# Process-wide singleton. FastAPI's lifespan handler calls `.connect()`
# and `.close()` on this exact instance; everything else only ever reads
# from it via `get_database()`.
mongo_db = MongoDB()


def get_database() -> AsyncIOMotorDatabase:
    """
    FastAPI-dependency-friendly accessor, e.g.:

        async def some_route(db: AsyncIOMotorDatabase = Depends(get_database)):
            ...

    Kept as a module-level function (rather than a method reference)
    so it has a clean import path for use in `Depends(...)` throughout
    the routes layer starting Module 2.
    """
    return mongo_db.get_database()
