"""Smoke test proving the FastAPI app boots and serves a request end to end."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "environment" in body
