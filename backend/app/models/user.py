"""
User domain models.

Three distinct Pydantic models exist deliberately, rather than one
"User" model reused everywhere:

  - `UserSignupRequest` / `UserLoginRequest`: shape of incoming
    request bodies. Validation lives here (e.g. password min length).
  - `UserInDB`: the full internal representation, including the
    `hashed_password` field — this NEVER leaves the service layer.
  - `UserPublic`: the safe, client-facing representation returned by
    the API. Excludes `hashed_password` entirely, so it's structurally
    impossible to accidentally leak a password hash in an API response
    (as opposed to remembering to strip a field before every response).
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints


class UserRole(StrEnum):
    """
    Roles gate access to admin-only routes (Module 10). Declared now
    so the `users` collection schema is stable from day one instead of
    requiring a migration later to add a `role` field.
    """

    CANDIDATE = "candidate"
    ADMIN = "admin"


class UserSignupRequest(BaseModel):
    """Request body for POST /api/v1/auth/signup."""

    full_name: Annotated[str, StringConstraints(min_length=2, max_length=100)]
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]


class UserLoginRequest(BaseModel):
    """Request body for POST /api/v1/auth/login."""

    email: EmailStr
    password: str


class UserInDB(BaseModel):
    """
    Internal representation matching the `users` MongoDB collection.
    Includes the bcrypt hash — only ever constructed/read by the
    repository and service layers, never returned directly from a route.
    """

    id: str
    full_name: str
    email: EmailStr
    hashed_password: str
    role: UserRole = UserRole.CANDIDATE
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    @classmethod
    def new(cls, *, full_name: str, email: str, hashed_password: str) -> "UserInDB":
        """
        Factory for creating a brand-new user record with server-assigned
        timestamps. Keeping this on the model (rather than scattering
        `datetime.now(timezone.utc)` calls across the service layer)
        ensures `created_at`/`updated_at` are always set consistently.
        """
        now = datetime.now(timezone.utc)
        return cls(
            id="",  # populated by the repository after Mongo assigns an _id
            full_name=full_name,
            email=email,
            hashed_password=hashed_password,
            created_at=now,
            updated_at=now,
        )


class UserPublic(BaseModel):
    """
    Safe, client-facing user representation. Returned by
    signup/login/me endpoints. Deliberately has no `hashed_password`
    field at all — not just omitted at serialization time, but absent
    from the type — so leaking it is a type error, not a runtime bug.
    """

    id: str
    full_name: str
    email: EmailStr
    role: UserRole
    created_at: datetime

    @classmethod
    def from_db_model(cls, user: UserInDB) -> "UserPublic":
        return cls(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=user.role,
            created_at=user.created_at,
        )
