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
