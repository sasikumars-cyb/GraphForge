"""app.indexer.scanner.incremental — KAN-32's GitHub-API change detection
and minimal-tree materialization. HTTP mocked throughout (httpx.MockTransport,
same pattern tests/integration/test_openai_provider.py uses) — no real
network calls, no real GitHub API.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.indexer.scanner.incremental import (
    ChangedFile,
    compute_changed_files,
    is_safe_for_incremental_update,
    materialize_changed_files,
    resolve_branch_head_sha,
)


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Redirects every httpx.AsyncClient this module constructs through a
    mock transport — the module always builds its own short-lived client
    per call (no injected client param, unlike OpenAIProvider), so
    patching the class constructor is the seam."""
    real_client_cls = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)


# -- is_safe_for_incremental_update ------------------------------------------


def test_empty_diff_is_safe():
    assert is_safe_for_incremental_update([]) is True


def test_small_source_only_diff_is_safe():
    changed = [
        ChangedFile(path="app/services/foo.py", status="modified"),
        ChangedFile(path="app/services/bar.py", status="added"),
    ]
    assert is_safe_for_incremental_update(changed) is True


def test_too_many_files_is_unsafe():
    changed = [ChangedFile(path=f"app/f{i}.py", status="modified") for i in range(51)]
    assert is_safe_for_incremental_update(changed) is False


@pytest.mark.parametrize(
    "manifest", ["pom.xml", "requirements.txt", "pyproject.toml", "setup.py", "Pipfile"]
)
def test_manifest_change_is_unsafe(manifest: str):
    changed = [
        ChangedFile(path="app/services/foo.py", status="modified"),
        ChangedFile(path=manifest, status="modified"),
    ]
    assert is_safe_for_incremental_update(changed) is False


def test_nested_manifest_change_is_still_unsafe():
    """A manifest doesn't have to be at repo root to signal a project-
    level dependency change."""
    changed = [ChangedFile(path="submodule/requirements.txt", status="modified")]
    assert is_safe_for_incremental_update(changed) is False


def test_removed_and_renamed_files_alone_are_safe():
    changed = [
        ChangedFile(path="app/old.py", status="removed"),
        ChangedFile(path="app/new.py", status="renamed", previous_path="app/moved_from.py"),
    ]
    assert is_safe_for_incremental_update(changed) is True


# -- resolve_branch_head_sha --------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_branch_head_sha_returns_the_sha(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widgets/branches/main"
        return httpx.Response(200, json={"commit": {"sha": "abc123def456"}})

    _patch_client(monkeypatch, handler)
    sha = await resolve_branch_head_sha("https://github.com/acme/widgets", "main", "tok")
    assert sha == "abc123def456"


@pytest.mark.asyncio
async def test_resolve_branch_head_sha_returns_none_on_404(monkeypatch: pytest.MonkeyPatch):
    _patch_client(monkeypatch, lambda request: httpx.Response(404, json={"message": "Not Found"}))
    sha = await resolve_branch_head_sha("https://github.com/acme/widgets", "no-such-branch", None)
    assert sha is None


@pytest.mark.asyncio
async def test_resolve_branch_head_sha_returns_none_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _patch_client(monkeypatch, handler)
    sha = await resolve_branch_head_sha("https://github.com/acme/widgets", "main", None)
    assert sha is None


# -- compute_changed_files -----------------------------------------------------


@pytest.mark.asyncio
async def test_compute_changed_files_returns_empty_list_for_identical_shas():
    # No HTTP call at all when base == head — nothing to compare.
    result = await compute_changed_files("https://github.com/acme/widgets", "same", "same", None)
    assert result == []


@pytest.mark.asyncio
async def test_compute_changed_files_parses_the_compare_response(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widgets/compare/base...head"
        return httpx.Response(
            200,
            json={
                "status": "ahead",
                "files": [
                    {"filename": "app/foo.py", "status": "modified"},
                    {
                        "filename": "app/bar.py",
                        "status": "renamed",
                        "previous_filename": "app/old_bar.py",
                    },
                ],
            },
        )

    _patch_client(monkeypatch, handler)
    changed = await compute_changed_files("https://github.com/acme/widgets", "base", "head", "tok")

    assert changed == [
        ChangedFile(path="app/foo.py", status="modified", previous_path=None),
        ChangedFile(path="app/bar.py", status="renamed", previous_path="app/old_bar.py"),
    ]


@pytest.mark.asyncio
async def test_compute_changed_files_none_when_base_is_not_an_ancestor(
    monkeypatch: pytest.MonkeyPatch,
):
    """status="diverged"/"behind" — a force-push or rewritten history.
    The file list GitHub returns for that doesn't mean "diff since last
    index" anymore; must not be trusted."""
    _patch_client(
        monkeypatch,
        lambda request: httpx.Response(200, json={"status": "diverged", "files": []}),
    )
    changed = await compute_changed_files("https://github.com/acme/widgets", "base", "head", None)
    assert changed is None


@pytest.mark.asyncio
async def test_compute_changed_files_none_when_truncated(monkeypatch: pytest.MonkeyPatch):
    _patch_client(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"status": "ahead", "files": [], "truncated": True}
        ),
    )
    changed = await compute_changed_files("https://github.com/acme/widgets", "base", "head", None)
    assert changed is None


@pytest.mark.asyncio
async def test_compute_changed_files_none_on_non_200(monkeypatch: pytest.MonkeyPatch):
    _patch_client(monkeypatch, lambda request: httpx.Response(500))
    changed = await compute_changed_files("https://github.com/acme/widgets", "base", "head", None)
    assert changed is None


# -- materialize_changed_files -------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_changed_files_writes_fetched_content_at_relative_paths(
    monkeypatch: pytest.MonkeyPatch,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/widgets/contents/app/foo.py":
            return httpx.Response(200, content=b"print('hello')\n")
        if request.url.path == "/repos/acme/widgets/contents/app/pkg/bar.py":
            return httpx.Response(200, content=b"x = 1\n")
        raise AssertionError(f"unexpected request: {request.url}")

    _patch_client(monkeypatch, handler)
    changed = [
        ChangedFile(path="app/foo.py", status="modified"),
        ChangedFile(path="app/pkg/bar.py", status="added"),
    ]

    async with materialize_changed_files(
        "https://github.com/acme/widgets", changed, "head-sha", "tok"
    ) as work_dir:
        assert (work_dir / "app/foo.py").read_bytes() == b"print('hello')\n"
        assert (work_dir / "app/pkg/bar.py").read_bytes() == b"x = 1\n"
        captured_dir: Path = work_dir

    # Cleaned up on exit, same contract as clone_repository.
    assert not captured_dir.exists()


@pytest.mark.asyncio
async def test_materialize_changed_files_skips_removed_files(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=b"content\n")

    _patch_client(monkeypatch, handler)
    changed = [ChangedFile(path="app/deleted.py", status="removed")]

    async with materialize_changed_files(
        "https://github.com/acme/widgets", changed, "head-sha", None
    ) as work_dir:
        assert not (work_dir / "app/deleted.py").exists()

    assert calls == []  # no fetch attempted for a removed file


@pytest.mark.asyncio
async def test_materialize_changed_files_raises_on_fetch_failure(monkeypatch: pytest.MonkeyPatch):
    """One file's fetch failing must fail the whole attempt — the caller
    (indexing_service._attempt_incremental_index) catches this and falls
    back to a full index rather than merging a partial result."""
    from app.indexer.scanner.incremental import GitHubCompareError

    _patch_client(monkeypatch, lambda request: httpx.Response(404))
    changed = [ChangedFile(path="app/foo.py", status="modified")]

    with pytest.raises(GitHubCompareError):
        async with materialize_changed_files(
            "https://github.com/acme/widgets", changed, "head-sha", None
        ):
            pass
