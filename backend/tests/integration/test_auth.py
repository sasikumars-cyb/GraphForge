"""Registration, login, /me, and the GitHub OAuth stub routes."""

import uuid

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


async def test_register_never_grants_admin_regardless_of_email(db_client: AsyncClient) -> None:
    """Regression test: app/main.py's lifespan used to run an unconditional
    `UPDATE users SET role='admin' WHERE email='admin@graphforge.dev'` on
    every startup, in every environment. Combined with open self-registration,
    anyone could register that exact email and be auto-promoted to admin on
    the next restart — confirmed this had actually happened against this
    dev database (a real admin@graphforge.dev/role=admin row, since
    remediated). That startup SQL is gone — self-registering with this (or
    any) email must always yield an ordinary 'user' role. A fresh,
    never-registered-before variant of the address, since a stray
    already-admin row from the exploit this test guards against would
    otherwise hit the 409-duplicate-email path instead of actually
    exercising registration.
    """
    response = await _register(db_client, email=f"admin+{uuid.uuid4()}@graphforge.dev")

    assert response.status_code == 201
    assert response.json()["role"] == "user"


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


async def test_login_is_rate_limited_per_email(db_client: AsyncClient) -> None:
    """Regression test: login previously had no rate limiting at all, unlike
    workflows.py's stage-start endpoints — an attacker could make unlimited
    password guesses against one account. A unique email keeps this test's
    hits isolated from every other login test sharing the same rate-limit
    module state."""
    email = f"rate-limit-test-{uuid.uuid4()}@example.com"
    await _register(db_client, email=email)

    last_response = None
    for _ in range(11):
        last_response = await db_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "not-the-right-password"},
        )

    assert last_response is not None
    assert last_response.status_code == 429


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
