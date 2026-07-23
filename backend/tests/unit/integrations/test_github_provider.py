"""Unit tests for `GitHubVersionControlProvider.get_file_content` - mocks
only HTTP, same `httpx.MockTransport` convention used in
`tests/integration/test_openai_provider.py` (no new mocking library)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.github import GitHubApiError, GitHubVersionControlProvider, PostedComment

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
        assert json.loads(request.content) == {"body": "# 🤖 ChangeGuard AI Review"}
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
        body="# 🤖 ChangeGuard AI Review",
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
