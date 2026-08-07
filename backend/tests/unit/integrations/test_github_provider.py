"""Unit tests for `GitHubVersionControlProvider.get_file_content` - mocks
only HTTP, same `httpx.MockTransport` convention used in
`tests/integration/test_openai_provider.py` (no new mocking library)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.github import (
    GitHubApiError,
    GitHubVersionControlProvider,
    PostedComment,
    list_repositories,
)

# `GitHubVersionControlProvider` constructs its own `httpx.AsyncClient` per
# call rather than accepting one, so each test monkeypatches
# `httpx.AsyncClient` itself to inject a `MockTransport` - mirroring how
# `patch.object(GitHubVersionControlProvider, ...)` is used elsewhere in
# this codebase's integration tests, adapted for a unit-level HTTP mock.


@pytest.mark.asyncio
async def test_get_file_content_returns_raw_text_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/order-svc/contents/CODEOWNERS"
        assert request.headers["Accept"] == "application/vnd.github.raw+json"
        return httpx.Response(status_code=200, text="*.py @alice\n")

    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    provider = GitHubVersionControlProvider()
    content = await provider.get_file_content(owner="acme", repo="order-svc", path="CODEOWNERS")

    assert content == "*.py @alice\n"


@pytest.mark.asyncio
async def test_get_file_content_returns_none_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code=404, text="Not Found")
    )

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    provider = GitHubVersionControlProvider()
    content = await provider.get_file_content(owner="acme", repo="order-svc", path="CODEOWNERS")

    assert content is None


@pytest.mark.asyncio
async def test_get_file_content_raises_on_other_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code=500, text="Internal error")
    )

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    provider = GitHubVersionControlProvider()
    with pytest.raises(GitHubApiError):
        await provider.get_file_content(owner="acme", repo="order-svc", path="CODEOWNERS")


@pytest.mark.asyncio
async def test_post_pull_request_comment_returns_posted_comment_on_201(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/repos/acme/order-svc/issues/42/comments"
        assert request.headers["Authorization"] == "Bearer gho_faketoken"
        assert json.loads(request.content) == {"body": "# 🤖 GraphForge AI Review"}
        return httpx.Response(
            status_code=201,
            json={"id": 987654321, "html_url": "https://github.com/acme/order-svc/pull/42#comment"},
        )

    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    provider = GitHubVersionControlProvider()
    posted = await provider.post_pull_request_comment(
        owner="acme",
        repo="order-svc",
        pull_number=42,
        body="# 🤖 GraphForge AI Review",
        access_token="gho_faketoken",
    )

    assert posted == PostedComment(
        id=987654321, html_url="https://github.com/acme/order-svc/pull/42#comment"
    )


@pytest.mark.asyncio
async def test_post_pull_request_comment_omits_auth_header_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(status_code=201, json={"id": 1, "html_url": "https://x"})

    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    provider = GitHubVersionControlProvider()
    posted = await provider.post_pull_request_comment(
        owner="acme", repo="order-svc", pull_number=42, body="body"
    )

    assert posted.id == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 404, 500])
async def test_post_pull_request_comment_raises_on_error_status(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code=status_code, text="error body")
    )

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    provider = GitHubVersionControlProvider()
    with pytest.raises(GitHubApiError):
        await provider.post_pull_request_comment(
            owner="acme", repo="order-svc", pull_number=42, body="body", access_token="tok"
        )


@pytest.mark.asyncio
async def test_list_repositories_includes_organization_member_affiliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: `affiliation` must include `organization_member`
    alongside `owner`/`collaborator`, or every repo a user can only see via
    org membership (the common case for most org repos) is silently
    dropped from the response — no error, no indication of filtering,
    exactly the symptom reported against a real org."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/repos"
        affiliation = httpx.QueryParams(request.url.query).get("affiliation")
        assert affiliation is not None
        assert set(affiliation.split(",")) == {"owner", "collaborator", "organization_member"}
        return httpx.Response(status_code=200, json=[])

    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    result = await list_repositories("tok")
    assert result == []


@pytest.mark.asyncio
async def test_list_repositories_follows_pagination_past_the_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: a single `_github_get` call against `/user/repos`
    silently caps at GitHub's own 100-per-page maximum — an org with more
    than 100 repos would have the rest disappear with no error and no
    indication of truncation. `list_repositories` must follow GitHub's
    `Link: rel="next"` header until it's exhausted."""

    def _repo(n: int) -> dict[str, object]:
        return {
            "id": n,
            "owner": {"login": "acme"},
            "name": f"repo-{n}",
            "full_name": f"acme/repo-{n}",
            "private": False,
            "default_branch": "main",
            "html_url": f"https://github.com/acme/repo-{n}",
        }

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert request.url.path == "/user/repos"
            return httpx.Response(
                status_code=200,
                json=[_repo(1), _repo(2)],
                headers={"Link": '<https://api.github.com/user/repos?page=2>; rel="next"'},
            )
        if call_count == 2:
            # The `next` URL already carries its own query string — no
            # `affiliation`/`per_page` params should be re-appended.
            assert dict(request.url.params) == {"page": "2"}
            return httpx.Response(status_code=200, json=[_repo(3)])
        raise AssertionError("should not request a third page — the second had no Link header")

    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    result = await list_repositories("tok")
    assert [r.full_name for r in result] == ["acme/repo-1", "acme/repo-2", "acme/repo-3"]
    assert call_count == 2


@pytest.mark.asyncio
async def test_paginated_fetch_stops_at_the_page_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pagination bug on GitHub's side (or a truly pathological account)
    that returns an endless `next` link must not turn one call into an
    unbounded request loop — `_MAX_PAGINATED_PAGES` is the ceiling."""
    from app.integrations.github import _MAX_PAGINATED_PAGES, _github_get_all_pages

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=200,
            json=[{"n": call_count}],
            headers={"Link": '<https://api.github.com/user/repos?page=999>; rel="next"'},
        )

    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    result = await _github_get_all_pages("/user/repos", "tok")
    assert call_count == _MAX_PAGINATED_PAGES
    assert len(result) == _MAX_PAGINATED_PAGES
