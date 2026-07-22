"""Selecting/persisting repositories and listing their pull requests."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "ada@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada",
}
USER_B = {
    "email": "grace@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Grace",
}

REPO_ENGINE = {
    "provider_repo_id": "1001",
    "owner": "ada",
    "name": "engine",
    "full_name": "ada/engine",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/engine",
}
REPO_NOTES = {
    "provider_repo_id": "1002",
    "owner": "ada",
    "name": "notes",
    "full_name": "ada/notes",
    "private": True,
    "default_branch": "main",
    "html_url": "https://github.com/ada/notes",
}


async def _register_and_get_token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login_response.json()["access_token"])


async def test_selecting_repositories_persists_metadata(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE, REPO_NOTES]}
    )

    assert response.status_code == 200
    full_names = {repo["full_name"] for repo in response.json()}
    assert full_names == {"ada/engine", "ada/notes"}

    listed = await db_client.get("/api/v1/repositories", headers=headers)
    assert {repo["full_name"] for repo in listed.json()} == {"ada/engine", "ada/notes"}


async def test_resubmitting_a_smaller_selection_untracks_the_rest(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE, REPO_NOTES]}
    )
    response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE]}
    )

    assert response.status_code == 200
    assert [repo["full_name"] for repo in response.json()] == ["ada/engine"]


async def test_repositories_are_scoped_per_user(db_client: AsyncClient) -> None:
    token_a = await _register_and_get_token(db_client, USER_A)
    token_b = await _register_and_get_token(db_client, USER_B)

    await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"repositories": [REPO_ENGINE]},
    )

    response_b = await db_client.get(
        "/api/v1/repositories", headers={"Authorization": f"Bearer {token_b}"}
    )

    assert response_b.json() == []


async def test_pull_requests_for_a_freshly_selected_repo_is_empty(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    select_response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE]}
    )
    repo_id = select_response.json()[0]["id"]

    response = await db_client.get(f"/api/v1/repositories/{repo_id}/pull-requests", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_pull_requests_endpoint_404s_for_another_users_repository(
    db_client: AsyncClient,
) -> None:
    token_a = await _register_and_get_token(db_client, USER_A)
    token_b = await _register_and_get_token(db_client, USER_B)

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"repositories": [REPO_ENGINE]},
    )
    repo_id = select_response.json()[0]["id"]

    response = await db_client.get(
        f"/api/v1/repositories/{repo_id}/pull-requests",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == 404
