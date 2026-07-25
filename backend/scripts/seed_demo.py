"""Seeds the local demo environment (see demo/DEMO_GUIDE.md).

Registers a dedicated demo user, tracks the four local demo repositories,
indexes each one, creates a `PullRequest` row for each of the four demo
scenarios (as branches on the already-indexed repos - there's no "create
pull request" API since PRs normally arrive via GitHub webhook), and runs
both deterministic and AI analysis on each so the UI has real results to
show immediately.

Run from `backend/` against the containerized dev stack started via
`scripts/demo-up.sh` (which mounts demo/repositories/ into the backend
container at /demo/repositories and sets VCS_PROVIDER=local_git):

    uv run python scripts/seed_demo.py

This script talks to the API over HTTP (http://localhost:8000 by default)
and, for the one thing with no API (creating a PullRequest row), connects
directly to the database - the same two techniques this project's own
integration tests already use.
"""

import asyncio
import os
from datetime import UTC, datetime
from typing import TypedDict

import httpx
from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.pull_request import PullRequest

# Never referenced by name below, but required: PullRequest.repository_id's
# ForeignKey only resolves against tables SQLAlchemy has actually seen a
# model class for in this process, and nothing else in this script imports
# app.models.repository.
from app.models.repository import Repository  # noqa: F401


class Scenario(TypedDict):
    repo_name: str
    number: int
    title: str
    head_ref: str


API_BASE_URL = os.environ.get("DEMO_API_BASE_URL", "http://localhost:8000/api/v1")
DEMO_EMAIL = "demo@graphforge.example.com"
DEMO_PASSWORD = "correct-horse-battery-staple"  # noqa: S105
DEMO_REPOSITORIES_CONTAINER_ROOT = "/demo/repositories"

REPOSITORIES = [
    {"provider_repo_id": "local-order-service", "name": "order-service"},
    {"provider_repo_id": "local-payment-service", "name": "payment-service"},
    {"provider_repo_id": "local-inventory-service", "name": "inventory-service"},
    {"provider_repo_id": "local-notification-service", "name": "notification-service"},
]

SCENARIOS: list[Scenario] = [
    {
        "repo_name": "order-service",
        "number": 1,
        "title": "Rename OrderCreatedEvent.total to totalCents",
        "head_ref": "pr-1",
    },
    {
        "repo_name": "order-service",
        "number": 2,
        "title": "Add refund() to PaymentClient and a currency field to the charge contract",
        "head_ref": "pr-2",
    },
    {
        "repo_name": "inventory-service",
        "number": 3,
        "title": "Add order.shipped consumer ahead of the shipping producer",
        "head_ref": "pr-3",
    },
    {
        "repo_name": "order-service",
        "number": 4,
        "title": "Retire order.cancelled event",
        "head_ref": "pr-4",
    },
]


async def get_or_create_token(client: httpx.AsyncClient) -> str:
    register_response = await client.post(
        "/auth/register",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD, "full_name": "Demo User"},
    )
    if register_response.status_code not in (200, 201, 409):
        register_response.raise_for_status()

    login_response = await client.post(
        "/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
    )
    login_response.raise_for_status()
    return str(login_response.json()["access_token"])


async def track_repositories(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    """Returns {repo_name: repository_id}."""
    response = await client.post(
        "/repositories",
        headers=headers,
        json={
            "repositories": [
                {
                    "provider_repo_id": repo["provider_repo_id"],
                    "owner": "local",
                    "name": repo["name"],
                    "full_name": f"local/{repo['name']}",
                    "private": False,
                    "default_branch": "main",
                    "html_url": f"{DEMO_REPOSITORIES_CONTAINER_ROOT}/{repo['name']}",
                }
                for repo in REPOSITORIES
            ]
        },
    )
    response.raise_for_status()
    return {item["name"]: item["id"] for item in response.json()}


async def index_and_wait(
    client: httpx.AsyncClient, headers: dict[str, str], repository_id: str, name: str
) -> None:
    trigger = await client.post(f"/repositories/{repository_id}/index", headers=headers)
    if trigger.status_code == 409:
        print(f"  {name}: indexing already in progress, waiting for it instead")
    else:
        trigger.raise_for_status()

    for _ in range(60):
        status_response = await client.get(f"/repositories/{repository_id}/index", headers=headers)
        status_response.raise_for_status()
        job = status_response.json()
        if job["status"] == "completed":
            summary = job.get("result_summary") or {}
            print(f"  {name}: indexed ({summary})")
            return
        if job["status"] == "failed":
            raise RuntimeError(f"Indexing failed for {name}: {job.get('error_message')}")
        await asyncio.sleep(1)

    raise RuntimeError(f"Indexing did not complete for {name} within 60s")


async def get_or_create_pull_request(repository_id: str, scenario: Scenario) -> str:
    async with AsyncSessionLocal() as session:
        github_pr_id = str(scenario["number"])
        existing = await session.execute(
            select(PullRequest).where(
                PullRequest.repository_id == repository_id,
                PullRequest.github_pr_id == github_pr_id,
            )
        )
        pull_request = existing.scalar_one_or_none()
        if pull_request is not None:
            return str(pull_request.id)

        pull_request = PullRequest(
            repository_id=repository_id,
            github_pr_id=github_pr_id,
            number=scenario["number"],
            title=scenario["title"],
            state="open",
            is_draft=False,
            author_login="demo",
            html_url=f"{DEMO_REPOSITORIES_CONTAINER_ROOT}/{scenario['repo_name']}/tree/{scenario['head_ref']}",
            head_ref=scenario["head_ref"],
            head_sha="0" * 40,
            base_ref="main",
            github_created_at=datetime.now(UTC),
            github_updated_at=datetime.now(UTC),
        )
        session.add(pull_request)
        await session.commit()
        await session.refresh(pull_request)
        return str(pull_request.id)


async def analyze(
    client: httpx.AsyncClient, headers: dict[str, str], pull_request_id: str
) -> tuple[str, str | None]:
    """Returns (risk, ai_executive_summary). The AI summary is None if
    OPENAI_API_KEY isn't configured - deterministic analysis still runs and
    is still worth seeding even when AI analysis can't."""
    deterministic = await client.post(f"/pull-requests/{pull_request_id}/analyze", headers=headers)
    deterministic.raise_for_status()

    ai = await client.post(f"/pull-requests/{pull_request_id}/ai-analysis", headers=headers)
    if ai.status_code == 503:
        return str(deterministic.json()["risk"]), None
    ai.raise_for_status()
    return str(deterministic.json()["risk"]), str(ai.json()["executive_summary"])


async def main() -> None:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        token = await get_or_create_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        print("Tracking repositories...")
        repo_ids = await track_repositories(client, headers)

        print("Indexing repositories...")
        for repo in REPOSITORIES:
            await index_and_wait(client, headers, repo_ids[repo["name"]], repo["name"])

        print("Creating and analyzing scenario pull requests...")
        results: list[tuple[Scenario, str]] = []
        for scenario in SCENARIOS:
            repository_id = repo_ids[scenario["repo_name"]]
            pull_request_id = await get_or_create_pull_request(repository_id, scenario)
            risk, executive_summary = await analyze(client, headers, pull_request_id)
            results.append((scenario, pull_request_id))
            summary_note = (
                f"ai_summary={executive_summary[:80]!r}"
                if executive_summary is not None
                else "ai_summary=<skipped - OPENAI_API_KEY not configured>"
            )
            print(f"  #{scenario['number']} {scenario['title']} -> risk={risk}, {summary_note}")

        print("\nDone. Sign in to the UI as:")
        print(f"  email:    {DEMO_EMAIL}")
        print(f"  password: {DEMO_PASSWORD}")
        print("\nRepositories:")
        for repo in REPOSITORIES:
            print(f"  {repo['name']}: /repositories/{repo_ids[repo['name']]}")
        print("\nScenario pull requests:")
        for scenario, pull_request_id in results:
            print(f"  #{scenario['number']} {scenario['title']}: /pull-requests/{pull_request_id}")


if __name__ == "__main__":
    asyncio.run(main())
