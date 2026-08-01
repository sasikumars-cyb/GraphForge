"""GitHub webhook signature verification and `pull_request` event handling.

Metadata only: no diff fetching, no risk scoring, no AI analysis — that's a
later feature. This just keeps `pull_requests` in sync with what GitHub says
is currently true about a PR.
"""

import hashlib
import hmac
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.pull_request import PullRequest
from app.models.repository import Repository


def _require(obj: Any, *keys: str) -> Any:
    """Nested dict lookup that raises a clean 400 AppError naming the
    missing key, instead of a bare KeyError several layers deep surfacing
    as an unhandled 500. The webhook signature already verified this
    payload actually came from GitHub — an unexpected/missing field means a
    GitHub event shape this endpoint doesn't handle yet, not tampering —
    but GitHub treats any non-2xx response as a delivery failure and
    retries, so a payload shape this can't handle must still be ack'd with
    a clean 4xx (logged once) rather than crash and retry forever.
    """
    current = obj
    for i, key in enumerate(keys):
        if not isinstance(current, dict) or key not in current:
            raise AppError(
                f"Malformed pull_request webhook payload: missing '{'.'.join(keys[: i + 1])}'.",
                status_code=400,
                error_code="invalid_webhook_payload",
            )
        current = current[key]
    return current


def verify_signature(payload_body: bytes, signature_header: str | None, secret: str) -> bool:
    """Verifies GitHub's `X-Hub-Signature-256` header against the raw
    (unparsed) request body — the signature is computed over the exact
    bytes GitHub sent, so this must run before any JSON parsing.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = (
        "sha256=" + hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


def _pull_request_state(pr_payload: dict[str, Any]) -> str:
    if pr_payload.get("merged"):
        return "merged"
    return str(_require(pr_payload, "state"))


def pull_request_fields_from_api_payload(pr_payload: dict[str, Any]) -> dict[str, Any]:
    """Map a GitHub `pull_request` object (identical shape whether it comes
    from a webhook event's `payload["pull_request"]` or a direct
    `GET /repos/{owner}/{repo}/pulls/{number}` call) to `PullRequest`
    column values. Shared so the standalone AI Workspace PR-URL resolver
    (`app.api.v1.routers.agent_runs`) doesn't reimplement this mapping.
    """
    return {
        "number": _require(pr_payload, "number"),
        "title": _require(pr_payload, "title"),
        "state": _pull_request_state(pr_payload),
        "is_draft": pr_payload.get("draft", False),
        "author_login": _require(pr_payload, "user", "login"),
        "html_url": _require(pr_payload, "html_url"),
        "head_ref": _require(pr_payload, "head", "ref"),
        "head_sha": _require(pr_payload, "head", "sha"),
        "base_ref": _require(pr_payload, "base", "ref"),
        "github_created_at": datetime.fromisoformat(
            _require(pr_payload, "created_at").replace("Z", "+00:00")
        ),
        "github_updated_at": datetime.fromisoformat(
            _require(pr_payload, "updated_at").replace("Z", "+00:00")
        ),
    }


async def handle_pull_request_event(db: AsyncSession, payload: dict[str, Any]) -> list[PullRequest]:
    """Upserts a PullRequest row for every locally-tracked Repository that
    matches the webhook's repository — there's one Repository row per user
    who tracks it, so one GitHub event can fan out to several rows.
    Repositories nobody tracks are silently ignored (still ack'd 200 to GitHub).
    """
    github_repo_id = str(_require(payload, "repository", "id"))
    pr_payload = _require(payload, "pull_request")
    github_pr_id = str(_require(pr_payload, "id"))

    repositories_result = await db.execute(
        select(Repository).where(Repository.github_repo_id == github_repo_id)
    )
    repositories = repositories_result.scalars().all()

    updated: list[PullRequest] = []
    for repository in repositories:
        existing_result = await db.execute(
            select(PullRequest).where(
                PullRequest.repository_id == repository.id,
                PullRequest.github_pr_id == github_pr_id,
            )
        )
        pull_request = existing_result.scalar_one_or_none()

        fields = pull_request_fields_from_api_payload(pr_payload)

        if pull_request is None:
            pull_request = PullRequest(
                repository_id=repository.id, github_pr_id=github_pr_id, **fields
            )
            db.add(pull_request)
        else:
            for field, value in fields.items():
                setattr(pull_request, field, value)

        updated.append(pull_request)

    await db.commit()
    return updated
