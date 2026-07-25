"""
Refresh token repository — manages the `refresh_tokens` collection
that backs the rotation + reuse-detection scheme described in the
Module 2 architecture notes.
"""

import uuid
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.models.auth import RefreshTokenInDB

logger = get_logger(__name__)

_COLLECTION_NAME = "refresh_tokens"


class RefreshTokenRepository:
    """Async operations against the `refresh_tokens` collection."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._collection = database[_COLLECTION_NAME]

    @staticmethod
    def _to_domain(document: dict) -> RefreshTokenInDB:
        return RefreshTokenInDB(
            id=str(document["_id"]),
            token_hash=document["token_hash"],
            user_id=document["user_id"],
            family_id=document["family_id"],
            created_at=document["created_at"],
            expires_at=document["expires_at"],
            revoked_at=document.get("revoked_at"),
            replaced_by_id=document.get("replaced_by_id"),
        )

    async def create(
        self, *, user_id: str, token_hash: str, family_id: str | None = None
    ) -> RefreshTokenInDB:
        """
        Inserts a new refresh token record.

        `family_id` is provided when this token is a rotation of an
        existing session (so it inherits the same lineage), or omitted
        on a fresh login (so a brand-new family is started).
        """
        settings = get_settings()
        now = datetime.now(timezone.utc)
        document = {
            "token_hash": token_hash,
            "user_id": user_id,
            "family_id": family_id or str(uuid.uuid4()),
            "created_at": now,
            "expires_at": now + timedelta(days=settings.refresh_token_expire_days),
            "revoked_at": None,
            "replaced_by_id": None,
        }
        result = await self._collection.insert_one(document)
        document["_id"] = result.inserted_id
        return self._to_domain(document)

    async def get_by_hash(self, token_hash: str) -> RefreshTokenInDB | None:
        document = await self._collection.find_one({"token_hash": token_hash})
        return self._to_domain(document) if document else None

    async def mark_rotated(self, *, token_id: str, replaced_by_id: str) -> None:
        """
        Marks a refresh token as consumed-by-rotation. It is NOT marked
        `revoked_at` here — `replaced_by_id` alone is what distinguishes
        "normally rotated" from "explicitly revoked" (logout / theft
        response), which matters if we ever want to audit *why* a token
        stopped being valid.
        """
        await self._collection.update_one(
            {"_id": self._object_id(token_id)},
            {"$set": {"replaced_by_id": replaced_by_id}},
        )

    async def revoke_family(self, family_id: str) -> int:
        """
        Revokes every token in a family — used on logout (kill this
        session) and on reuse detection (kill a potentially compromised
        session everywhere). Returns the count of tokens revoked, which
        the service layer logs for audit purposes.
        """
        now = datetime.now(timezone.utc)
        result = await self._collection.update_many(
            {"family_id": family_id, "revoked_at": None},
            {"$set": {"revoked_at": now}},
        )
        return result.modified_count

    @staticmethod
    def _object_id(value: str) -> ObjectId:
        try:
            return ObjectId(value)
        except InvalidId as exc:
            raise ValueError(f"Invalid refresh token id: {value}") from exc
