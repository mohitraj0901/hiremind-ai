"""
Smoke test verifying the app factory produces a working FastAPI app
and the health check route is wired correctly.

Uses the same in-memory `mongomock-motor` substitution as
`test_auth.py` rather than requiring a live MongoDB Atlas connection —
a CI pipeline shouldn't need production database credentials just to
run the test suite. Production behavior (real Motor client, tz_aware
UTC datetimes, real `ping` command) is untouched; only the connection
target is swapped.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.db.mongodb import mongo_db
from app.main import create_app


@pytest.mark.asyncio
async def test_health_check_returns_ok_when_db_reachable() -> None:
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
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert "X-Request-ID" in response.headers
