"""Proves the three registered exception handlers produce the documented
JSON error shape.

No production route raises these yet, so this test attaches temporary
routes directly to a fresh app instance rather than adding test-only
endpoints to `app.api`.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import NotFoundError
from app.main import create_app


@pytest.fixture
async def error_test_client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    @app.get("/api/v1/_test/app-error")
    async def _raise_app_error() -> None:
        raise NotFoundError("Widget not found.")

    @app.get("/api/v1/_test/unhandled-error")
    async def _raise_unhandled_error() -> None:
        raise RuntimeError("Something went wrong internally.")

    @app.get("/api/v1/_test/validated")
    async def _requires_query_param(count: int) -> dict[str, int]:
        return {"count": count}

    # raise_app_exceptions=False: let unhandled exceptions become the HTTP
    # response our handler produced (500 JSON), instead of httpx re-raising
    # them into the test - the same choice a real deployed server makes.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_app_error_returns_its_status_code_and_shape(
    error_test_client: AsyncClient,
) -> None:
    response = await error_test_client.get("/api/v1/_test/app-error")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Widget not found."}}


@pytest.mark.asyncio
async def test_unhandled_exception_is_masked_as_generic_500(
    error_test_client: AsyncClient,
) -> None:
    response = await error_test_client.get("/api/v1/_test/unhandled-error")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    # The internal exception message must never leak to the client.
    assert "Something went wrong internally" not in response.text


@pytest.mark.asyncio
async def test_validation_error_returns_422_with_consistent_shape(
    error_test_client: AsyncClient,
) -> None:
    # Omitting the required `count` query param triggers FastAPI/Pydantic's
    # own RequestValidationError before the route body ever runs.
    response = await error_test_client.get("/api/v1/_test/validated")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed.",
        }
    }
