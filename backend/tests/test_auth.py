"""
Module 2 integration tests.

Uses `mongomock-motor` — a Motor-API-compatible in-memory MongoDB
substitute — in place of a real MongoDB Atlas connection, since this
environment can't reach Atlas. Production still uses the real Motor
client from `app/db/mongodb.py`; only the connection step is patched
here, so every repository/service/route code path under test is the
actual production code.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.db.mongodb import mongo_db
from app.main import create_app


@pytest.fixture
async def client():
    """
    Yields an AsyncClient wired against a fresh app instance backed by
    an in-memory mock database, with startup/shutdown lifecycle handled
    exactly as it would be in production (indexes get created, etc.).
    """
    mock_client = AsyncMongoMockClient(tz_aware=True)

    async def fake_connect() -> None:
        mongo_db.client = mock_client
        mongo_db.database = mock_client["hiremind_ai_test"]

    with patch.object(mongo_db, "connect", side_effect=fake_connect), patch.object(
        mongo_db, "close", new_callable=AsyncMock
    ):
        app = create_app()
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac


@pytest.mark.asyncio
async def test_signup_returns_token_pair_and_user(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/signup",
        json={"full_name": "Ada Lovelace", "email": "ada@example.com", "password": "supersecret1"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert "hashed_password" not in body["user"]
    assert body["access_token"]
    assert body["refresh_token"]


@pytest.mark.asyncio
async def test_duplicate_signup_returns_409(client: AsyncClient) -> None:
    payload = {"full_name": "Grace Hopper", "email": "grace@example.com", "password": "supersecret1"}
    first = await client.post("/api/v1/auth/signup", json=payload)
    second = await client.post("/api/v1/auth/signup", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


@pytest.mark.asyncio
async def test_login_with_wrong_password_returns_401(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/signup",
        json={"full_name": "Alan Turing", "email": "alan@example.com", "password": "supersecret1"},
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "alan@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_protected_me_route_requires_valid_token(client: AsyncClient) -> None:
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"full_name": "Margaret Hamilton", "email": "margaret@example.com", "password": "supersecret1"},
    )
    access_token = signup.json()["access_token"]

    unauthenticated = await client.get("/api/v1/auth/me")
    assert unauthenticated.status_code == 401

    authenticated = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert authenticated.status_code == 200
    assert authenticated.json()["email"] == "margaret@example.com"


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_old_token_becomes_invalid(client: AsyncClient) -> None:
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"full_name": "Katherine Johnson", "email": "katherine@example.com", "password": "supersecret1"},
    )
    original_refresh_token = signup.json()["refresh_token"]

    refresh_response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original_refresh_token}
    )
    assert refresh_response.status_code == 200
    new_refresh_token = refresh_response.json()["refresh_token"]
    assert new_refresh_token != original_refresh_token

    # New token works for a second rotation.
    second_refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_refresh_token}
    )
    assert second_refresh.status_code == 200


@pytest.mark.asyncio
async def test_reusing_rotated_refresh_token_is_detected_and_revokes_family(
    client: AsyncClient,
) -> None:
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"full_name": "Radia Perlman", "email": "radia@example.com", "password": "supersecret1"},
    )
    original_refresh_token = signup.json()["refresh_token"]

    # Rotate once (legitimate use) — original_refresh_token is now "spent".
    first_refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original_refresh_token}
    )
    assert first_refresh.status_code == 200
    rotated_token = first_refresh.json()["refresh_token"]

    # Replay the ALREADY-SPENT original token — simulates an attacker
    # who stole it before the legitimate rotation happened.
    replay_attempt = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original_refresh_token}
    )
    assert replay_attempt.status_code == 401

    # The entire family (including the token issued by the legitimate
    # rotation) must now be dead too, since we can't tell attacker and
    # victim apart after the fact.
    now_dead_too = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated_token}
    )
    assert now_dead_too.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"full_name": "Barbara Liskov", "email": "barbara@example.com", "password": "supersecret1"},
    )
    refresh_token = signup.json()["refresh_token"]

    logout_response = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
    assert logout_response.status_code == 204

    reuse_after_logout = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert reuse_after_logout.status_code == 401
