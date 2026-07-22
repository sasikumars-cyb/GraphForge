"""Registration, login, /me, and the GitHub OAuth stub routes."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

REGISTER_PAYLOAD = {
    "email": "ada@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada Lovelace",
}


async def _register(db_client: AsyncClient, **overrides: str) -> dict:
    payload = {**REGISTER_PAYLOAD, **overrides}
    response = await db_client.post("/api/v1/auth/register", json=payload)
    return response


async def test_register_creates_user(db_client: AsyncClient) -> None:
    response = await _register(db_client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == REGISTER_PAYLOAD["email"]
    assert body["full_name"] == REGISTER_PAYLOAD["full_name"]
    assert body["auth_provider"] == "local"
    assert "id" in body
    assert "created_at" in body
    assert "password" not in body
    assert "hashed_password" not in body


async def test_register_duplicate_email_returns_409(db_client: AsyncClient) -> None:
    await _register(db_client)
    response = await _register(db_client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_register_short_password_returns_422(db_client: AsyncClient) -> None:
    response = await _register(db_client, password="short")

    assert response.status_code == 422


async def test_login_with_correct_credentials_returns_token(db_client: AsyncClient) -> None:
    await _register(db_client)

    response = await db_client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


async def test_login_with_wrong_password_returns_401(db_client: AsyncClient) -> None:
    await _register(db_client)

    response = await db_client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": "not-the-right-password"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_login_with_unknown_email_returns_401(db_client: AsyncClient) -> None:
    response = await db_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever-password"},
    )

    assert response.status_code == 401


async def test_me_without_token_returns_401(db_client: AsyncClient) -> None:
    response = await db_client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_me_with_invalid_token_returns_401(db_client: AsyncClient) -> None:
    response = await db_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )

    assert response.status_code == 401


async def test_me_with_valid_token_returns_current_user(db_client: AsyncClient) -> None:
    await _register(db_client)
    login_response = await db_client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    token = login_response.json()["access_token"]

    response = await db_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["email"] == REGISTER_PAYLOAD["email"]


async def test_github_oauth_login_is_not_configured(db_client: AsyncClient) -> None:
    response = await db_client.get("/api/v1/auth/github/login")

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"


async def test_github_oauth_callback_is_not_configured(db_client: AsyncClient) -> None:
    response = await db_client.get("/api/v1/auth/github/callback?code=abc&state=xyz")

    assert response.status_code == 501
