"""GitHub-API-based change detection and minimal-tree materialization —
KAN-32 incremental indexing.

Avoids a full `git clone` for a re-index entirely: fetches only the list
of changed files (GitHub's Compare API) and only those files' current
content (GitHub's Contents API) — the same two REST endpoints
`app.tools.implementations.github_tool.GitHubTool` already calls for
single-file lookups, called directly here rather than through the Tool
Registry (indexing already resolves its own `access_token` per run — see
`indexing_service._get_access_token` — independent of any per-user Tool
Registry instance).

`app.indexer.services.indexing_service` owns the actual decision of
whether to use this module for a given run versus falling back to
`app.indexer.scanner.repository_cloner.clone_repository`'s full shallow
clone; every function here either returns a clear "I can't tell, don't
trust me" signal (`None`/`False`) or does exactly what it says, so that
decision stays in one place.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

_API_URL = "https://api.github.com"
_COMPARE_TIMEOUT_SECONDS = 30
_FILE_FETCH_TIMEOUT_SECONDS = 15
_FILE_FETCH_CONCURRENCY = 8

# A changed file with this basename, anywhere in the repo, makes a scoped
# update unsafe to trust on its own — see is_safe_for_incremental_update.
_MANIFEST_FILENAMES = frozenset(
    {"pom.xml", "requirements.txt", "pyproject.toml", "setup.py", "Pipfile"}
)

# Above this many changed files, the per-file GitHub API round-trips this
# module makes cost more than a single shallow clone would — the point of
# KAN-32 is making small changes cheap, not making large ones artificially
# more expensive by insisting on per-file fetches past where that stops
# being a win. A diff this size falls back to a full index instead.
_MAX_FILES_FOR_INCREMENTAL = 50


class GitHubCompareError(AppError):
    status_code = 502
    error_code = "github_compare_failed"


@dataclass(frozen=True)
class ChangedFile:
    path: str
    # GitHub's own vocabulary: "added" | "modified" | "removed" |
    # "renamed" | "copied" | "changed" | "unchanged".
    status: str
    previous_path: str | None = None


def _owner_repo_from_html_url(html_url: str) -> tuple[str, str]:
    parsed = urlparse(html_url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise GitHubCompareError(f"Cannot parse owner/repo from {html_url!r}.")
    return parts[0], parts[1].removesuffix(".git")


async def resolve_branch_head_sha(
    html_url: str, branch: str, access_token: str | None
) -> str | None:
    """The commit sha `branch` currently points to. `None` on any failure
    — same "can't tell, don't trust me" contract as the rest of this
    module; callers fall back to a full index rather than guessing.
    """
    owner, repo = _owner_repo_from_html_url(html_url)
    headers = {"Accept": "application/vnd.github+json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    try:
        async with httpx.AsyncClient(timeout=_COMPARE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{_API_URL}/repos/{owner}/{repo}/branches/{branch}", headers=headers
            )
        if response.status_code != 200:
            logger.warning(
                "github_resolve_branch_head_failed owner=%s repo=%s branch=%s status=%d",
                owner,
                repo,
                branch,
                response.status_code,
            )
            return None
        payload = response.json()
        sha = payload.get("commit", {}).get("sha")
        return str(sha) if sha else None
    except httpx.HTTPError as exc:
        logger.warning(
            "github_resolve_branch_head_http_error owner=%s repo=%s branch=%s error=%s",
            owner,
            repo,
            branch,
            str(exc),
        )
        return None


async def compute_changed_files(
    html_url: str, base_sha: str, head_sha: str, access_token: str | None
) -> list[ChangedFile] | None:
    """GitHub's Compare API. Returns `None` (never raises) on any failure
    or inconclusive result, so callers treat "couldn't determine the
    diff" identically to "diff looked unsafe": fall back to a full index
    rather than trusting a partial or ambiguous answer.
    """
    if base_sha == head_sha:
        return []
    owner, repo = _owner_repo_from_html_url(html_url)
    headers = {"Accept": "application/vnd.github+json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    try:
        async with httpx.AsyncClient(timeout=_COMPARE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{_API_URL}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}",
                headers=headers,
            )
        if response.status_code != 200:
            logger.warning(
                "github_compare_failed owner=%s repo=%s base=%s head=%s status=%d",
                owner,
                repo,
                base_sha,
                head_sha,
                response.status_code,
            )
            return None
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("github_compare_http_error owner=%s repo=%s error=%s", owner, repo, str(exc))
        return None

    if payload.get("status") not in ("ahead", "identical"):
        # "diverged"/"behind" — base_sha isn't actually an ancestor of
        # head_sha (a force-push, a rebase, a rewritten branch history).
        # The file-level diff GitHub returns in that case doesn't mean
        # what this module needs it to mean; only a full index is
        # trustworthy here.
        logger.info(
            "github_compare_non_ancestor owner=%s repo=%s status=%s",
            owner,
            repo,
            payload.get("status"),
        )
        return None

    files = payload.get("files")
    if files is None:
        return None
    if payload.get("truncated") or len(files) >= 300:
        # GitHub's Compare API caps the `files` array at 300 and sets
        # `truncated: true` past that — an incomplete file list is exactly
        # the "can't trust it" case above.
        logger.info("github_compare_truncated owner=%s repo=%s", owner, repo)
        return None

    return [
        ChangedFile(
            path=f["filename"],
            status=f.get("status", "modified"),
            previous_path=f.get("previous_filename"),
        )
        for f in files
    ]


def is_safe_for_incremental_update(changed_files: list[ChangedFile]) -> bool:
    """Whether `changed_files` is small and simple enough to trust a
    scoped re-parse+merge for, rather than falling back to a full index.

    Deliberately conservative: a false "not safe" costs one full index —
    today's existing, always-correct behavior, unchanged. A false "safe"
    would mean silently wrong graph data, which is the failure mode this
    whole module exists to avoid.
    """
    if not changed_files:
        return True
    if len(changed_files) > _MAX_FILES_FOR_INCREMENTAL:
        return False
    for changed in changed_files:
        filename = changed.path.rsplit("/", 1)[-1]
        if filename in _MANIFEST_FILENAMES:
            # A changed manifest can add/remove/version-bump project-level
            # dependency facts (MavenDependency/PythonDependency) that
            # aren't scoped to one file's node set the way Controllers/
            # Services/PythonModules are (see app.indexer.models.
            # architecture — dependencies carry no SourceLocation at all).
            # Safer to re-derive those from a full parse than to special-
            # case manifest merging here.
            return False
    return True


@asynccontextmanager
async def materialize_changed_files(
    html_url: str,
    changed_files: list[ChangedFile],
    head_sha: str,
    access_token: str | None,
) -> AsyncGenerator[Path, None]:
    """Fetches the current content of every added/modified/renamed file in
    `changed_files` (nothing for `removed` — there is nothing to parse)
    via GitHub's Contents API and writes it into a fresh temp directory at
    its real relative path, so the *existing, unmodified* `ILanguageParser`
    implementations — which just walk a directory (see
    `app.indexer.parsers.python.python_parser`'s/`...java.spring_boot_
    parser`'s own `_iter_*_files`) — can parse it exactly as if it were a
    real (partial) checkout. No parser changes needed for this to work.

    A non-source file among `changed_files` (a changed `README.md`
    alongside a changed `.py` file, say) gets fetched and written too —
    harmless, not filtered out: the parser's own directory walk only ever
    looks for its own file extension, and a Neo4j delete scoped to that
    path later matches no node (nothing was ever indexed from a README).
    Slightly wasteful, not incorrect — not worth the extra branch to avoid.

    Always removes the temp directory on exit — same contract as
    `app.indexer.scanner.repository_cloner.clone_repository`.
    """
    settings = get_settings()
    Path(settings.indexer_clone_root).mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="repo-incremental-", dir=settings.indexer_clone_root))

    owner, repo = _owner_repo_from_html_url(html_url)
    headers = {"Accept": "application/vnd.github.raw+json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    to_fetch = [f for f in changed_files if f.status != "removed"]
    semaphore = asyncio.Semaphore(_FILE_FETCH_CONCURRENCY)

    async def _fetch_one(client: httpx.AsyncClient, changed: ChangedFile) -> None:
        async with semaphore:
            try:
                response = await client.get(
                    f"{_API_URL}/repos/{owner}/{repo}/contents/{changed.path}",
                    headers=headers,
                    params={"ref": head_sha},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                # One file's fetch failing must not silently omit it from
                # the re-parsed set — that would leave the graph merge
                # believing the file has no content (deleting its old
                # nodes without writing replacements for it). Fail the
                # whole incremental attempt; the caller falls back to a
                # full index rather than persisting a partial result.
                raise GitHubCompareError(
                    f"Failed to fetch {changed.path} at {head_sha}: {exc}"
                ) from exc
            target = work_dir / changed.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)

    try:
        async with httpx.AsyncClient(timeout=_FILE_FETCH_TIMEOUT_SECONDS) as client:
            await asyncio.gather(*(_fetch_one(client, f) for f in to_fetch))
        yield work_dir
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
