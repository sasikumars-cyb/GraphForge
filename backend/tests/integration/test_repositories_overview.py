"""`GET /repositories/overview` — the single paginated request backing the
Repositories page.

Everything asserted here used to be derived client-side from one PR list
and one indexing job per repository plus one analysis per open PR; these
tests pin the server-side equivalents so that fan-out can't come back.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indexing_job import IndexingJob
from app.models.pull_request import PullRequest
from app.models.pull_request_analysis import PullRequestAnalysis

pytestmark = pytest.mark.asyncio

USER = {
    "email": "overview@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada",
}
OTHER_USER = {
    "email": "other-overview@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Grace",
}


async def _token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login.json()["access_token"])


def _repo_payload(index: int, owner: str = "ada") -> dict[str, object]:
    return {
        "provider_repo_id": str(2000 + index),
        "owner": owner,
        "name": f"service-{index:03d}",
        "full_name": f"{owner}/service-{index:03d}",
        "private": False,
        "default_branch": "main",
        "html_url": f"https://github.com/{owner}/service-{index:03d}",
    }


async def _track(db_client: AsyncClient, headers: dict[str, str], count: int) -> list[str]:
    response = await db_client.post(
        "/api/v1/repositories",
        headers=headers,
        json={"repositories": [_repo_payload(i) for i in range(count)]},
    )
    assert response.status_code == 200
    return [repo["id"] for repo in response.json()]


def _make_pr(repository_id: str, number: int, updated_at: datetime) -> PullRequest:
    return PullRequest(
        repository_id=repository_id,
        github_pr_id=f"gh-{repository_id}-{number}",
        number=number,
        title=f"PR {number}",
        state="open",
        is_draft=False,
        author_login="ada",
        html_url="https://github.com/ada/service/pull/1",
        head_ref="feature",
        head_sha="a" * 40,
        base_ref="main",
        github_created_at=updated_at,
        github_updated_at=updated_at,
    )


async def test_overview_paginates_instead_of_returning_every_repository(
    db_client: AsyncClient,
) -> None:
    headers = {"Authorization": f"Bearer {await _token(db_client, USER)}"}
    await _track(db_client, headers, 30)

    first = await db_client.get(
        "/api/v1/repositories/overview?page=1&page_size=24", headers=headers
    )
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 24
    assert body["total"] == 30
    assert body["has_more"] is True
    # Account-wide, not page-scoped — the headline stat must still say 30.
    assert body["stats"]["repositories_monitored"] == 30

    second = await db_client.get(
        "/api/v1/repositories/overview?page=2&page_size=24", headers=headers
    )
    assert len(second.json()["items"]) == 6
    assert second.json()["has_more"] is False

    # No repository appears on both pages, and between them they cover
    # everything — a paginated list that silently drops or repeats rows is
    # worse than an unpaginated one.
    ids = [item["id"] for item in body["items"]] + [item["id"] for item in second.json()["items"]]
    assert len(set(ids)) == 30


async def test_overview_search_matches_full_name_case_insensitively(
    db_client: AsyncClient,
) -> None:
    headers = {"Authorization": f"Bearer {await _token(db_client, USER)}"}
    await _track(db_client, headers, 5)

    response = await db_client.get("/api/v1/repositories/overview?q=SERVICE-003", headers=headers)

    body = response.json()
    assert [item["full_name"] for item in body["items"]] == ["ada/service-003"]
    assert body["total"] == 1
    # Filtering the list must not rewrite the account-wide stats above it.
    assert body["stats"]["repositories_monitored"] == 5


async def test_overview_derives_health_and_open_pr_counts_from_analyses(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = {"Authorization": f"Bearer {await _token(db_client, USER)}"}
    repo_ids = await _track(db_client, headers, 3)
    recent = datetime.now(UTC) - timedelta(days=1)

    # repo 0: one open PR analyzed HIGH, updated this week -> critical.
    high_pr = _make_pr(repo_ids[0], 1, recent)
    # repo 1: one open PR with no analysis at all -> attention + awaiting.
    unanalyzed_pr = _make_pr(repo_ids[1], 1, recent)
    # repo 2: one open PR analyzed LOW -> healthy.
    low_pr = _make_pr(repo_ids[2], 1, recent)
    db_session.add_all([high_pr, unanalyzed_pr, low_pr])
    await db_session.flush()
    db_session.add_all(
        [
            PullRequestAnalysis(pull_request_id=high_pr.id, risk="HIGH"),
            PullRequestAnalysis(pull_request_id=low_pr.id, risk="LOW"),
        ]
    )
    await db_session.flush()

    body = (await db_client.get("/api/v1/repositories/overview", headers=headers)).json()
    by_name = {item["full_name"]: item for item in body["items"]}

    assert by_name["ada/service-000"]["health"] == "critical"
    assert by_name["ada/service-001"]["health"] == "attention"
    assert by_name["ada/service-002"]["health"] == "healthy"
    assert all(item["open_pull_requests"] == 1 for item in body["items"])

    assert body["stats"]["open_pull_request_count"] == 3
    assert body["stats"]["awaiting_analysis_count"] == 1
    assert body["stats"]["high_risk_this_week_count"] == 1

    # Most urgent first, then alphabetical — the ordering the card grid
    # relies on, and the reason paging through it is meaningful.
    assert [item["full_name"] for item in body["items"]] == [
        "ada/service-000",
        "ada/service-001",
        "ada/service-002",
    ]


async def test_overview_reports_latest_indexing_job_per_repository(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = {"Authorization": f"Bearer {await _token(db_client, USER)}"}
    repo_ids = await _track(db_client, headers, 2)
    now = datetime.now(UTC)

    # An older failure followed by a newer success: only the newest job
    # describes the repository, so this must read as "indexed".
    db_session.add_all(
        [
            IndexingJob(
                repository_id=repo_ids[0],
                status="failed",
                created_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2),
            ),
            IndexingJob(
                repository_id=repo_ids[0],
                status="completed",
                created_at=now - timedelta(minutes=10),
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
            ),
            IndexingJob(repository_id=repo_ids[1], status="running", created_at=now),
        ]
    )
    await db_session.flush()

    body = (await db_client.get("/api/v1/repositories/overview", headers=headers)).json()
    by_name = {item["full_name"]: item for item in body["items"]}

    assert by_name["ada/service-000"]["indexing_status"] == "indexed"
    assert by_name["ada/service-000"]["indexing_in_progress"] is False
    assert by_name["ada/service-000"]["last_indexed_at"] is not None
    # A job that's still running has produced nothing usable yet, so it
    # filters as "not indexed" — but is distinguishable from never-run.
    assert by_name["ada/service-001"]["indexing_status"] == "not_indexed"
    assert by_name["ada/service-001"]["indexing_in_progress"] is True

    assert body["stats"]["avg_indexing_time_ms"] == pytest.approx(60_000, rel=0.05)

    indexed_only = await db_client.get(
        "/api/v1/repositories/overview?indexing=indexed", headers=headers
    )
    assert [item["full_name"] for item in indexed_only.json()["items"]] == ["ada/service-000"]


async def test_overview_never_includes_another_users_repositories(
    db_client: AsyncClient,
) -> None:
    headers_a = {"Authorization": f"Bearer {await _token(db_client, USER)}"}
    headers_b = {"Authorization": f"Bearer {await _token(db_client, OTHER_USER)}"}
    await _track(db_client, headers_a, 3)

    body = (await db_client.get("/api/v1/repositories/overview", headers=headers_b)).json()

    assert body["items"] == []
    assert body["total"] == 0
    assert body["stats"]["repositories_monitored"] == 0
