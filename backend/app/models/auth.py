"""
Auth-related models: API response schemas and the internal
`refresh_tokens` collection representation.
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.user import UserPublic


class TokenPairResponse(BaseModel):
    """
    Returned by /signup, /login, and /refresh. The refresh token is
    included in the body (not just a cookie) so this API works cleanly
    for both a browser SPA and non-browser clients (mobile, CLI, tests)
    without assuming a particular client-side storage strategy — the
    frontend team decides whether to keep it in memory, an httpOnly
    cookie proxy, etc.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserPublic


class RefreshRequest(BaseModel):
    """Request body for POST /api/v1/auth/refresh."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Request body for POST /api/v1/auth/logout."""

    refresh_token: str


class RefreshTokenInDB(BaseModel):
    """
    Internal representation matching the `refresh_tokens` collection.

    `family_id` links every token descended from a single original
    login via rotation — logout or reuse-detection revokes by
    `family_id`, killing every token in that lineage at once.

    `replaced_by_id` is set the moment a token is rotated, and is what
    lets us distinguish "this is the current valid token" from "this
    token was already used once" — the latter, if presented again, is
    the reuse-detection trigger.
    """

    id: str
    token_hash: str
    user_id: str
    family_id: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    replaced_by_id: str | None = None

    @property
    def is_active(self) -> bool:
        """A token is usable only if it hasn't been revoked or rotated away."""
        return self.revoked_at is None and self.replaced_by_id is None
