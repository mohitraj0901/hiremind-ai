"""
Authentication routes.

Deliberately thin: every handler does request validation (handled
automatically by the Pydantic request models), delegates to
`AuthService`, and returns a response model. No business logic lives
here — that's what makes the service layer unit-testable independent
of HTTP.
"""

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import get_auth_service, get_current_user
from app.models.auth import LogoutRequest, RefreshRequest, TokenPairResponse
from app.models.user import UserInDB, UserLoginRequest, UserPublic, UserSignupRequest
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/signup",
    response_model=TokenPairResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new candidate account",
)
async def signup(
    payload: UserSignupRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenPairResponse:
    return await auth_service.signup(
        full_name=payload.full_name, email=payload.email, password=payload.password
    )


@router.post(
    "/login",
    response_model=TokenPairResponse,
    summary="Authenticate and receive a new token pair",
)
async def login(
    payload: UserLoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenPairResponse:
    return await auth_service.login(email=payload.email, password=payload.password)


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    summary="Rotate a refresh token for a new access + refresh token pair",
)
async def refresh(
    payload: RefreshRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenPairResponse:
    return await auth_service.refresh(raw_refresh_token=payload.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current session's refresh token family",
)
async def logout(
    payload: LogoutRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> None:
    await auth_service.logout(raw_refresh_token=payload.refresh_token)


@router.get(
    "/me",
    response_model=UserPublic,
    summary="Return the currently authenticated user",
)
async def get_me(current_user: UserInDB = Depends(get_current_user)) -> UserPublic:
    """
    Exists in Module 2 primarily as a reference implementation of a
    protected route — every future module's protected endpoints follow
    this exact `Depends(get_current_user)` pattern.
    """
    return UserPublic.from_db_model(current_user)
