"""
Database index creation.

Indexes are created idempotently at application startup (safe to run
on every boot — `create_index` is a no-op if the index already exists
with the same spec) rather than via a separate manual migration step,
so a fresh MongoDB Atlas cluster is always correctly indexed the
moment the app first connects to it.
"""

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.logging_config import get_logger

logger = get_logger(__name__)


async def create_indexes(database: AsyncIOMotorDatabase) -> None:
    """Creates all required indexes across collections. Called from app lifespan startup."""

    # Enforce email uniqueness at the database level — the authoritative
    # guard against duplicate accounts, with UserRepository.create()
    # catching the resulting DuplicateKeyError (see that file's comments
    # on why we don't rely on a check-then-insert pattern instead).
    await database["users"].create_index("email", unique=True, name="uniq_email")

    # Refresh tokens are looked up by their hash on every refresh/logout
    # call, so this needs an index for that to stay O(log n) at scale.
    await database["refresh_tokens"].create_index("token_hash", unique=True, name="uniq_token_hash")

    # Revocation operates per-family (see RefreshTokenRepository.revoke_family),
    # so family lookups need their own index too.
    await database["refresh_tokens"].create_index("family_id", name="idx_family_id")

    # TTL index: MongoDB automatically deletes documents once `expires_at`
    # is in the past (expireAfterSeconds=0 means "delete at the exact
    # value of the date field, not N seconds after it"). This keeps the
    # refresh_tokens collection from growing unboundedly with dead
    # tokens, with zero application-level cleanup code required.
    await database["refresh_tokens"].create_index(
        "expires_at", expireAfterSeconds=0, name="ttl_expires_at"
    )

    logger.info("database_indexes_ensured")
