"""Physical Workspace backing — Cap §19's "isolated, bounded environment."

Isolation here is filesystem-level: one directory per Workspace identity
under `settings.workspace_root`, never shared between Workspaces. This is
the exact, honest scope of isolation Phase 4 provides — not
process-level, not container-level, not credential-level beyond "the
access token is never persisted." Cap §19 never specifies a stronger
isolation mechanism, and this module does not claim one.

Reuses `app.indexer.scanner.repository_cloner.run_git_clone` — the same
security-reviewed subprocess/timeout/token-redaction mechanics
`clone_repository()` already uses — rather than a second, independently
maintained copy (Phase 4 design decision; see that module's own
docstring on why `run_git_clone` was extracted).

Deliberately NOT an async context manager, unlike `clone_repository()`:
a Workspace's contract-required lifetime ("MAY survive across reasoning
cycles") is unrelated to any single request's lifetime — creation and
destruction are two independent calls, driven by
`WorkspaceLifecycleService`, never a single `async with` block.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.indexer.scanner.repository_cloner import RepositoryCloneError, run_git_clone

logger = logging.getLogger(__name__)


def physical_location_for(workspace_id: uuid.UUID) -> str:
    """The deterministic directory a Workspace's identity maps to —
    never a credential, never itself secret (see Phase 4 design's
    identity-classification analysis), but not echoed to any end-user-
    facing surface either, matching `local_repository_service.py`'s
    existing precedent for treating resolved filesystem paths as
    operationally sensitive."""
    settings = get_settings()
    return str(Path(settings.workspace_root) / str(workspace_id))


async def create_physical_workspace(
    *,
    workspace_id: uuid.UUID,
    repository_url: str | None,
    ref: str,
    access_token: str | None = None,
) -> str:
    """Creates the isolated directory and, if `repository_url` is given,
    clones into it. Returns the physical location. On ANY failure
    (including a clone failure), the partially-created directory is
    removed before the exception propagates — Phase 4 design's Case B:
    a synchronously-caught creation failure must never leave a
    physically-present-but-durably-unclaimed directory; the caller
    (`WorkspaceLifecycleService`) is responsible for the corresponding
    durable `WorkspaceDestroyed(reason=creation_failed)` record, this
    function only owns the filesystem side of that story.

    Credentials are never written to disk by this function beyond git's
    own already-existing behavior for an authenticated clone URL
    (identical to `clone_repository()`'s existing, already-reviewed
    behavior) and are never returned or logged — `access_token` is used
    only to build the ephemeral clone URL inside `run_git_clone`.
    """
    location = physical_location_for(workspace_id)
    dest = Path(location)
    dest.mkdir(parents=True, exist_ok=False)
    try:
        if repository_url is not None:
            await run_git_clone(repository_url, ref, dest, access_token)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return location


def destroy_physical_workspace(physical_location: str) -> bool:
    """Best-effort, retry-tolerant removal — Phase 4 design's Case D:
    the durable `WorkspaceDestroyed` record is ALWAYS authoritative
    regardless of whether this succeeds; a failure here never raises,
    it is reconciled later by the sweep re-attempting this exact call.
    Returns True if the location no longer exists after this call
    (whether because it was just removed or was already gone —
    idempotent either way), False if it still exists (removal failed).
    """
    try:
        shutil.rmtree(physical_location, ignore_errors=False)
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning(
            "workspace_physical_cleanup_failed location=%s error=%s", physical_location, exc
        )
        return not Path(physical_location).exists()
    return not Path(physical_location).exists()


def physical_workspace_exists(physical_location: str) -> bool:
    return Path(physical_location).exists()


__all__ = [
    "RepositoryCloneError",
    "create_physical_workspace",
    "destroy_physical_workspace",
    "physical_location_for",
    "physical_workspace_exists",
]
