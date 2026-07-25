"""
User repository — the only layer that talks directly to the `users`
MongoDB collection. Services depend on this class, never on Motor
directly, so the persistence mechanism could be swapped without
touching business logic, and so repository methods are trivially
mockable in service-layer unit tests.
"""

from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import ConflictException
from app.core.logging_config import get_logger
from app.models.user import UserInDB

logger = get_logger(__name__)

_COLLECTION_NAME = "users"


class UserRepository:
    """Async CRUD operations against the `users` collection."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._collection = database[_COLLECTION_NAME]

    @staticmethod
    def _to_domain(document: dict) -> UserInDB:
        """Converts a raw MongoDB document into a typed `UserInDB` model."""
        return UserInDB(
            id=str(document["_id"]),
            full_name=document["full_name"],
            email=document["email"],
            hashed_password=document["hashed_password"],
            role=document.get("role", "candidate"),
            is_active=document.get("is_active", True),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
        )

    async def create(self, *, full_name: str, email: str, hashed_password: str) -> UserInDB:
        """
        Inserts a new user document. Relies on the unique index on
        `email` (created in `app/db/indexes.py`) as the source of truth
        for uniqueness — we still catch `DuplicateKeyError` here rather
        than doing a separate "check if exists" query first, which
        would be a classic TOCTOU (time-of-check-to-time-of-use) race
        condition under concurrent signups for the same email.
        """
        now = datetime.now(timezone.utc)
        document = {
            "full_name": full_name,
            "email": email.lower(),
            "hashed_password": hashed_password,
            "role": "candidate",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self._collection.insert_one(document)
        except DuplicateKeyError as exc:
            logger.warning("signup_duplicate_email", extra={"extra_fields": {"email": email}})
            raise ConflictException("An account with this email already exists.") from exc

        document["_id"] = result.inserted_id
        return self._to_domain(document)

    async def get_by_email(self, email: str) -> UserInDB | None:
        document = await self._collection.find_one({"email": email.lower()})
        return self._to_domain(document) if document else None

    async def get_by_id(self, user_id: str) -> UserInDB | None:
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            object_id = ObjectId(user_id)
        except InvalidId:
            return None

        document = await self._collection.find_one({"_id": object_id})
        return self._to_domain(document) if document else None
