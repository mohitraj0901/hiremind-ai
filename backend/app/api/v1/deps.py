"""
Dependency-injection wiring for the v1 API.

Each `get_*` function is a thin FastAPI dependency that constructs the
next layer up (repository -> service) from the layer below it, using
FastAPI's `Depends` to chain them. This keeps route handlers free of
any object-construction logic — they simply declare what they need
and FastAPI resolves the chain per-request.
"""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import UnauthorizedException
from app.core.security import decode_access_token
from app.db.mongodb import get_database
from app.models.user import UserInDB
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService

# `auto_error=False` so we can raise our own `UnauthorizedException`
# (translated to our standard error envelope) instead of FastAPI's
# default HTTPBearer error shape, keeping every auth failure in this
# API consistent regardless of *why* auth failed.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_user_repository(
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> UserRepository:
    return UserRepository(database)


def get_refresh_token_repository(
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> RefreshTokenRepository:
    return RefreshTokenRepository(database)


def get_auth_service(
    user_repository: UserRepository = Depends(get_user_repository),
    refresh_token_repository: RefreshTokenRepository = Depends(get_refresh_token_repository),
) -> AuthService:
    return AuthService(user_repository, refresh_token_repository)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    user_repository: UserRepository = Depends(get_user_repository),
) -> UserInDB:
    """
    Protects a route: extracts the `Authorization: Bearer <token>`
    header, validates the access-token JWT, and loads the corresponding
    user. Every downstream module's protected routes depend on this
    single function, so auth behavior (e.g. "what happens if the
    account was deactivated after the token was issued") only needs to
    be correct in one place.
    """
    if credentials is None:
        raise UnauthorizedException("Missing authentication credentials.")

    try:
        payload = decode_access_token(credentials.credentials)
    except JWTError as exc:
        raise UnauthorizedException("Invalid or expired access token.") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedException("Invalid access token payload.")

    user = await user_repository.get_by_id(user_id)
    if user is None or not user.is_active:
        raise UnauthorizedException("User not found or inactive.")

    return user
