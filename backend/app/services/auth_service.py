"""
Authentication service — orchestrates the user + refresh-token
repositories and the security primitives to implement signup, login,
token refresh (with rotation), and logout.

This is where the reuse-detection security model actually lives:
see `refresh()` below for the core logic.
"""

from datetime import datetime, timezone

from app.core.exceptions import UnauthorizedException
from app.core.logging_config import get_logger
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.auth import TokenPairResponse
from app.models.user import UserInDB, UserPublic
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository

logger = get_logger(__name__)


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
        refresh_token_repository: RefreshTokenRepository,
    ) -> None:
        self._users = user_repository
        self._refresh_tokens = refresh_token_repository

    async def signup(self, *, full_name: str, email: str, password: str) -> TokenPairResponse:
        """
        Creates a new user and immediately logs them in (issues a fresh
        token pair) — signup-then-separately-log-in is unnecessary
        friction for a candidate-facing product.
        """
        hashed_password = hash_password(password)
        user = await self._users.create(
            full_name=full_name, email=email, hashed_password=hashed_password
        )
        logger.info("user_signed_up", extra={"extra_fields": {"user_id": user.id}})
        return await self._issue_new_session(user)

    async def login(self, *, email: str, password: str) -> TokenPairResponse:
        """
        Authenticates credentials and issues a fresh token pair
        (a brand-new token family — a new login is a new session lineage,
        distinct from any rotation chain of a previous session).

        Uses a single generic error message for "no such user" and
        "wrong password" deliberately, to avoid leaking which emails
        are registered (user enumeration).
        """
        user = await self._users.get_by_email(email)
        if user is None or not verify_password(password, user.hashed_password):
            logger.warning("login_failed", extra={"extra_fields": {"email": email}})
            raise UnauthorizedException("Invalid email or password.")

        if not user.is_active:
            raise UnauthorizedException("This account has been deactivated.")

        logger.info("user_logged_in", extra={"extra_fields": {"user_id": user.id}})
        return await self._issue_new_session(user)

    async def refresh(self, *, raw_refresh_token: str) -> TokenPairResponse:
        """
        Rotates a refresh token: validates it, issues a brand-new
        access + refresh token pair in the same family, and invalidates
        the presented token so it can never be used again.

        Reuse detection: if the presented token has already been
        rotated (`replaced_by_id` is set) or was explicitly revoked
        (`revoked_at` is set), that means either (a) a legitimate
        client is retrying a stale token it shouldn't have kept, or
        more seriously (b) an attacker is replaying a stolen token
        after the real user already moved past it. We can't distinguish
        these cases, so we treat it as theft: revoke the ENTIRE family,
        forcing re-authentication, rather than silently ignoring it.
        """
        token_hash = hash_refresh_token(raw_refresh_token)
        record = await self._refresh_tokens.get_by_hash(token_hash)

        if record is None:
            raise UnauthorizedException("Invalid refresh token.")

        if not record.is_active:
            revoked_count = await self._refresh_tokens.revoke_family(record.family_id)
            logger.error(
                "refresh_token_reuse_detected",
                extra={
                    "extra_fields": {
                        "user_id": record.user_id,
                        "family_id": record.family_id,
                        "tokens_revoked": revoked_count,
                    }
                },
            )
            raise UnauthorizedException(
                "This session is no longer valid. Please log in again."
            )

        if record.expires_at < datetime.now(timezone.utc):
            raise UnauthorizedException("Refresh token has expired. Please log in again.")

        user = await self._users.get_by_id(record.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedException("Account no longer available.")

        # Issue the replacement token in the SAME family, then mark the
        # presented token as rotated. Order matters: we create the new
        # token first so that if this step failed we'd never leave the
        # user's only valid token marked as consumed with nothing to
        # replace it.
        new_raw_refresh_token = generate_refresh_token()
        new_record = await self._refresh_tokens.create(
            user_id=user.id,
            token_hash=hash_refresh_token(new_raw_refresh_token),
            family_id=record.family_id,
        )
        await self._refresh_tokens.mark_rotated(token_id=record.id, replaced_by_id=new_record.id)

        access_token, expires_at = create_access_token(user_id=user.id)
        return TokenPairResponse(
            access_token=access_token,
            refresh_token=new_raw_refresh_token,
            expires_at=expires_at,
            user=UserPublic.from_db_model(user),
        )

    async def logout(self, *, raw_refresh_token: str) -> None:
        """
        Revokes the entire token family associated with the presented
        refresh token, ending that session lineage. Idempotent: logging
        out with an already-invalid or unknown token is treated as
        success (the desired end state — "not logged in" — is already
        achieved), not an error.
        """
        token_hash = hash_refresh_token(raw_refresh_token)
        record = await self._refresh_tokens.get_by_hash(token_hash)
        if record is None:
            return

        revoked_count = await self._refresh_tokens.revoke_family(record.family_id)
        logger.info(
            "user_logged_out",
            extra={
                "extra_fields": {
                    "user_id": record.user_id,
                    "tokens_revoked": revoked_count,
                }
            },
        )

    async def _issue_new_session(self, user: UserInDB) -> TokenPairResponse:
        """Shared helper: issues a fresh access token + a brand-new refresh token family."""
        raw_refresh_token = generate_refresh_token()
        await self._refresh_tokens.create(
            user_id=user.id, token_hash=hash_refresh_token(raw_refresh_token)
        )
        access_token, expires_at = create_access_token(user_id=user.id)
        return TokenPairResponse(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            expires_at=expires_at,
            user=UserPublic.from_db_model(user),
        )
