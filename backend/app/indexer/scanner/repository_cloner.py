"""Shallow-clones a repository to a temporary directory for scanning.

Uses `asyncio.create_subprocess_exec` (not `subprocess.run`) specifically so
a clone doesn't block the event loop while it runs inside a background
task — other requests keep being served while git works.
"""

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT_SECONDS = 120


class RepositoryCloneError(AppError):
    status_code = 502
    error_code = "repository_clone_failed"


def _authenticated_url(html_url: str, access_token: str | None) -> str:
    """Injects a GitHub token into an HTTPS clone URL for private repos.
    Never returned to a caller that might log it — see `redact_token`."""
    if not access_token or not html_url.startswith("https://"):
        return html_url
    return html_url.replace("https://", f"https://x-access-token:{access_token}@", 1)


def redact_token(text: str, access_token: str | None) -> str:
    return text.replace(access_token, "***") if access_token else text


@asynccontextmanager
async def clone_repository(
    html_url: str,
    ref: str,
    access_token: str | None = None,
) -> AsyncGenerator[Path, None]:
    """Shallow-clones `html_url` at `ref` into a fresh temp directory,
    yields its path, and always removes it on exit — success or failure.
    """
    settings = get_settings()
    Path(settings.indexer_clone_root).mkdir(parents=True, exist_ok=True)
    clone_dir = Path(tempfile.mkdtemp(prefix="repo-", dir=settings.indexer_clone_root))

    clone_url = _authenticated_url(html_url, access_token)

    try:
        logger.info("Cloning %s (ref=%s) to %s", html_url, ref, clone_dir)
        process = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            ref,
            "--single-branch",
            clone_url,
            str(clone_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_CLONE_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RepositoryCloneError(f"git clone of {html_url} timed out") from exc

        if process.returncode != 0:
            raise RepositoryCloneError(
                f"git clone of {html_url} failed: "
                f"{redact_token(stderr.decode(errors='replace'), access_token)}"
            )

        yield clone_dir
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


async def resolve_head_commit_sha(repo_path: Path) -> str | None:
    """`git rev-parse HEAD` inside an already-cloned repository (KAN-32) —
    what a full index actually indexed, for `Repository.
    last_indexed_commit_sha` (what a *future* incremental run needs to
    diff against). Reads the local clone `index_repository` already made
    rather than a second GitHub API round-trip: works identically for
    `source="github"` and `source="local"` repositories (a local clone has
    no GitHub API to ask), and needs no network access of its own at all.

    `None` on any failure — a shallow `--depth 1` clone always has HEAD
    resolvable in practice, but this must never turn "couldn't get the
    sha" into a failed indexing run over what KAN-32 treats everywhere
    else as an optional optimization.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        if process.returncode != 0:
            logger.warning(
                "resolve_head_commit_sha_failed repo_path=%s error=%s",
                repo_path,
                stderr.decode(errors="replace"),
            )
            return None
        return stdout.decode().strip() or None
    except (TimeoutError, OSError) as exc:
        logger.warning("resolve_head_commit_sha_error repo_path=%s error=%s", repo_path, str(exc))
        return None
