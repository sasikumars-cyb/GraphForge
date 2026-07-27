"""GitHub webhook receiver — real, hand-crafted GitHub-shaped payloads with
real HMAC-SHA256 signatures. No mocking: this is exactly what GitHub itself
sends and how it signs it, so this proves the endpoint works against the
real wire format without needing a live GitHub account.
"""

import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio

USER = {"email": "ada@example.com", "password": "correct-horse-battery-staple", "full_name": "Ada"}
REPO = {
    "provider_repo_id": "555000111",
    "owner": "ada",
    "name": "engine",
    "full_name": "ada/engine",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/engine",
}

WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture
def webhook_secret_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _pull_request_payload(
    *,
    action: str = "opened",
    pr_id: int = 42_000_001,
    number: int = 7,
    title: str = "Rename OrderPlaced.total to OrderPlaced.totalCents",
    state: str = "open",
    merged: bool = False,
    draft: bool = False,
    repo_id: str = REPO["provider_repo_id"],
) -> dict:
    """Shaped exactly like a real GitHub `pull_request` webhook delivery -
    only trimmed to the fields our handler actually reads."""
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "id": pr_id,
            "number": number,
            "title": title,
            "state": state,
            "merged": merged,
            "draft": draft,
            "user": {"login": "j.moreau"},
            "html_url": f"https://github.com/ada/engine/pull/{number}",
            "head": {"ref": "rename-total-field", "sha": "abc123def456"},
            "base": {"ref": "main"},
            "created_at": "2026-07-20T09:00:00Z",
            "updated_at": "2026-07-21T09:12:00Z",
        },
        "repository": {"id": int(repo_id), "full_name": "ada/engine"},
    }


async def _register_and_select_repo(db_client: AsyncClient) -> str:
    await db_client.post("/api/v1/auth/register", json=USER)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": USER["email"], "password": USER["password"]}
    )
    token = login_response.json()["access_token"]
    select_response = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token}"},
        json={"repositories": [REPO]},
    )
    return str(select_response.json()[0]["id"])


async def test_ping_event_is_acknowledged(
    db_client: AsyncClient, webhook_secret_configured: None
) -> None:
    body = json.dumps({"zen": "Keep it logically awesome."}).encode()

    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(body),
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pong"


async def test_invalid_signature_is_rejected(
    db_client: AsyncClient, webhook_secret_configured: None
) -> None:
    body = json.dumps(_pull_request_payload()).encode()

    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )

    assert response.status_code == 401


async def test_missing_signature_is_rejected(
    db_client: AsyncClient, webhook_secret_configured: None
) -> None:
    body = json.dumps(_pull_request_payload()).encode()

    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"Content-Type": "application/json", "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 401


async def test_webhook_rejected_when_secret_not_configured(db_client: AsyncClient) -> None:
    body = json.dumps(_pull_request_payload()).encode()

    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )

    assert response.status_code == 503


async def test_pull_request_opened_is_persisted(
    db_client: AsyncClient, webhook_secret_configured: None
) -> None:
    repo_id = await _register_and_select_repo(db_client)
    body = json.dumps(_pull_request_payload()).encode()

    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200
    assert response.json()["pull_requests_updated"] == "1"

    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": USER["email"], "password": USER["password"]}
    )
    token = login_response.json()["access_token"]
    prs_response = await db_client.get(
        f"/api/v1/repositories/{repo_id}/pull-requests",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert prs_response.status_code == 200
    prs = prs_response.json()
    assert len(prs) == 1
    assert prs[0]["number"] == 7
    assert prs[0]["state"] == "open"
    assert prs[0]["title"] == "Rename OrderPlaced.total to OrderPlaced.totalCents"


async def test_pull_request_merged_updates_the_same_row_not_a_duplicate(
    db_client: AsyncClient, webhook_secret_configured: None
) -> None:
    repo_id = await _register_and_select_repo(db_client)

    opened_body = json.dumps(_pull_request_payload(action="opened", state="open")).encode()
    await db_client.post(
        "/api/v1/webhooks/github",
        content=opened_body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(opened_body),
        },
    )

    merged_body = json.dumps(
        _pull_request_payload(action="closed", state="closed", merged=True)
    ).encode()
    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=merged_body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(merged_body),
        },
    )
    assert response.status_code == 200

    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": USER["email"], "password": USER["password"]}
    )
    token = login_response.json()["access_token"]
    prs_response = await db_client.get(
        f"/api/v1/repositories/{repo_id}/pull-requests",
        headers={"Authorization": f"Bearer {token}"},
    )

    prs = prs_response.json()
    assert len(prs) == 1  # updated in place, not duplicated
    assert prs[0]["state"] == "merged"


async def test_pull_request_for_untracked_repo_is_ignored_not_errored(
    db_client: AsyncClient, webhook_secret_configured: None
) -> None:
    body = json.dumps(_pull_request_payload(repo_id="999999999")).encode()

    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )

    assert response.status_code == 200
    assert response.json()["pull_requests_updated"] == "0"


async def test_malformed_json_body_returns_400_not_500(
    db_client: AsyncClient, webhook_secret_configured: None
) -> None:
    """Regression test: the signature-verified-but-not-valid-JSON case used
    to raise an unhandled JSONDecodeError (a 500). GitHub treats any non-2xx
    response as a delivery failure and retries — an unhandled 500 here
    would retry the same unparseable payload forever."""
    body = b"{not actually json"

    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_webhook_payload"


async def test_pull_request_payload_missing_required_field_returns_400_not_500(
    db_client: AsyncClient, webhook_secret_configured: None
) -> None:
    """Regression test: a `pull_request` payload missing a field this
    handler reads (e.g. a GitHub event shape variant not anticipated) used
    to raise a bare KeyError deep in handle_pull_request_event — again an
    unhandled 500 GitHub would retry indefinitely, instead of a clean 400
    logged once."""
    # The missing field is only ever read inside the per-tracked-repository
    # loop — a repo must actually be tracked/selected for this payload's
    # `repository.id` or that loop body (and thus the validation) never runs.
    await _register_and_select_repo(db_client)
    payload = _pull_request_payload()
    del payload["pull_request"]["head"]  # a field the handler requires
    body = json.dumps(payload).encode()

    response = await db_client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_webhook_payload"
