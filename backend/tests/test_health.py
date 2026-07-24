"""
Smoke test for Module 1: verifies the app factory produces a working
FastAPI app and the health check route is wired correctly.

Note: this test requires a reachable MONGODB_URI (e.g. a free Atlas
cluster) since `create_app()`'s lifespan connects to the real database
by design — Module 1 has no repository layer yet to mock against.
Later modules will introduce a proper test-database fixture strategy.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.mark.asyncio
async def test_health_check_returns_ok_when_db_reachable() -> None:
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
