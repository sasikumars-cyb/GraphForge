"""Connect GitHub, list/select repositories, and connection status.

This is the "repo access" use case — a locally-authenticated user (see
app.services.auth_service) linking a GitHub account so we can read their
repos, not a way to log in. See ADR 0006.
"""

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import AppError, NotFoundError, UnauthorizedError
from app.core.security import create_access_token, decode_access_token
from app.integrations.github import (
    GitHubApiError,
    GitHubOAuthProvider,
    fetch_token_scopes,
    fetch_user_profile,
    list_repositories,
)
from app.models.github_connection import GitHubConnection
from app.models.repository import Repository
from app.models.user import User
from app.schemas.github import AvailableRepository, RepositorySelectionRequest
from app.services.oauth_app_config_service import get_credential as get_oauth_app_credential

_STATE_EXPIRY = timedelta(minutes=10)
_OAUTH_STATE_PURPOSE = "github_oauth_state"


class GitHubNotConfiguredError(AppError):
    """Raised when GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET aren't set."""

    status_code = 503
    error_code = "github_not_configured"


class InvalidGitHubTokenError(AppError):
    """Raised when a pasted personal access token doesn't work - a client
    error (400), unlike GitHubApiError's 502 for an upstream failure once a
    connection already exists."""

    status_code = 400
    error_code = "invalid_github_token"


# Classic PATs need `repo` to do anything this app uses a token for (list
# private repos, clone for indexing, read diffs, write branches/commits/PRs
# via the git-ops agents) - the same scope the OAuth flow already requests
# (see `_SCOPE` in app.integrations.github). Checked only as an advisory
# warning, never a hard gate - see `fetch_token_scopes`'s docstring for why
# a fine-grained PAT can't be checked this way at all.
_REQUIRED_PAT_SCOPE = "repo"


async def _build_provider(db: AsyncSession) -> GitHubOAuthProvider:
    """A stored OAuth App credential (set via Settings -> Integrations by an
    admin, see app.services.oauth_app_config_service) takes precedence over
    GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET - the env vars remain the fallback
    for installs that configure this way instead."""
    client_id, client_secret = await get_oauth_app_credential(db, "github")
    settings = get_settings()
    client_id = client_id or settings.github_client_id
    client_secret = client_secret or settings.github_client_secret
    if not client_id or not client_secret:
        raise GitHubNotConfiguredError(
            "GitHub integration is not configured. Set GITHUB_CLIENT_ID and "
            "GITHUB_CLIENT_SECRET (see docs/setup.md), or configure it from "
            "Settings -> Integrations as an admin."
        )
    return GitHubOAuthProvider(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=settings.github_oauth_redirect_uri,
    )


async def get_connection(db: AsyncSession, user: User) -> GitHubConnection | None:
    result = await db.execute(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    return result.scalar_one_or_none()


async def get_decrypted_access_token(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    """Resolve a user's GitHub access token for provider calls, decrypted -
    or `None` if they haven't connected GitHub. Takes a bare `user_id`
    (rather than `get_connection`'s `User`) since every call site already
    has one handy (e.g. `repository.user_id`) without a separate `User`
    row loaded. Was previously duplicated verbatim as a private
    `_get_access_token` in both `app.ai.agent.investigation_agent` and
    `app.analysis.engine.impact_analysis_engine` - both now call this."""
    result = await db.execute(select(GitHubConnection).where(GitHubConnection.user_id == user_id))
    connection = result.scalar_one_or_none()
    return decrypt_secret(connection.encrypted_access_token) if connection else None


async def get_connect_authorization_url(db: AsyncSession, user: User) -> str:
    """Builds the GitHub authorize URL, with a signed, time-limited `state`
    that encodes which user initiated the connect flow — GitHub's redirect
    back to /callback carries no Authorization header, so this is how the
    callback knows whose GitHubConnection to write.
    """
    provider = await _build_provider(db)
    state = create_access_token(
        subject=str(user.id), expires_delta=_STATE_EXPIRY, purpose=_OAUTH_STATE_PURPOSE
    )
    return provider.get_authorization_url(state)


async def handle_oauth_callback(db: AsyncSession, code: str, state: str) -> User:
    """Verifies `state`, exchanges `code` for a token, and upserts the
    GitHubConnection for the user `state` identifies. Returns that user."""
    payload = decode_access_token(state)
    if payload.get("purpose") != _OAUTH_STATE_PURPOSE:
        # Rejects a general login access token presented as `state` too —
        # this callback should only ever accept a token minted specifically
        # for this flow (see get_connect_authorization_url).
        raise UnauthorizedError("Invalid OAuth state.")
    subject = payload.get("sub")
    if subject is None:
        raise UnauthorizedError("Invalid OAuth state.")

    user = await db.get(User, uuid.UUID(subject))
    if user is None:
        raise NotFoundError("User not found.")

    provider = await _build_provider(db)
    access_token = await provider.exchange_code_for_token(code)
    profile = await provider.fetch_user_profile(access_token)

    connection = await get_connection(db, user)
    encrypted_token = encrypt_secret(access_token)

    if connection is None:
        connection = GitHubConnection(
            user_id=user.id,
            github_user_id=profile.provider_user_id,
            github_username=profile.name or profile.provider_user_id,
            encrypted_access_token=encrypted_token,
            auth_method="oauth",
        )
        db.add(connection)
    else:
        connection.github_user_id = profile.provider_user_id
        connection.github_username = profile.name or profile.provider_user_id
        connection.encrypted_access_token = encrypted_token
        # Reconnecting via OAuth after a prior PAT connection (or vice
        # versa in connect_with_pat below) must flip this, not leave it
        # stuck on whichever flow was used first - it describes the
        # connection as it exists now, not its history.
        connection.auth_method = "oauth"

    await db.commit()
    return user


async def connect_with_pat(
    db: AsyncSession, user: User, token: str
) -> tuple[GitHubConnection, str | None]:
    """The PAT equivalent of `handle_oauth_callback`: no authorize/exchange
    step (there's no code to exchange - the user handed us the token
    directly), so this validates it directly against GitHub and upserts the
    same `GitHubConnection` row `auth_method="pat"` instead of `"oauth"`.

    Every downstream reader of that row (repository listing/selection,
    the indexer's clone step, deterministic impact analysis, the Planning
    Agent's/Context Discovery's GitHub enrichment, and the git-ops write
    agents) already resolves its token via `get_decrypted_access_token`, so
    none of them need to know or care which flow produced this row.

    Returns `(connection, scope_warning)` - `scope_warning` is a human-readable
    string when the token is a classic PAT missing the `repo` scope, else
    `None` (either it has `repo`, or it's a fine-grained PAT/GitHub App
    token whose scopes can't be checked this way - see
    `app.integrations.github.fetch_token_scopes`). The connection is made
    either way; this is advisory, not a gate.
    """
    token = token.strip()
    if not token:
        raise InvalidGitHubTokenError("A GitHub personal access token is required.")

    try:
        profile = await fetch_user_profile(token)
    except GitHubApiError as exc:
        raise InvalidGitHubTokenError(f"GitHub rejected this token: {exc}") from exc

    scope_warning: str | None = None
    try:
        scopes = await fetch_token_scopes(token)
    except GitHubApiError:
        # Already proven valid via fetch_user_profile above - a transient
        # failure on this second, purely advisory call must not block the
        # connection it would otherwise have completed.
        scopes = None
    if scopes is not None and _REQUIRED_PAT_SCOPE not in scopes:
        scope_warning = (
            f"This token doesn't have the '{_REQUIRED_PAT_SCOPE}' scope, so private "
            "repositories, indexing, and write actions (branches, commits, pull "
            "requests) may fail. Reconnect with a token that includes it."
        )

    connection = await get_connection(db, user)
    encrypted_token = encrypt_secret(token)

    if connection is None:
        connection = GitHubConnection(
            user_id=user.id,
            github_user_id=profile.provider_user_id,
            github_username=profile.name or profile.provider_user_id,
            encrypted_access_token=encrypted_token,
            auth_method="pat",
        )
        db.add(connection)
    else:
        connection.github_user_id = profile.provider_user_id
        connection.github_username = profile.name or profile.provider_user_id
        connection.encrypted_access_token = encrypted_token
        connection.auth_method = "pat"

    await db.commit()
    # The router needs connection fields (username/created_at/auth_method)
    # for its response; commit() expires them by default, and — unlike
    # handle_oauth_callback, whose caller only needs `user` back — a plain
    # post-commit attribute access here would trigger an implicit lazy
    # refresh that async SQLAlchemy can't do without an explicit await.
    await db.refresh(connection)
    return connection, scope_warning


async def disconnect(db: AsyncSession, user: User) -> None:
    connection = await get_connection(db, user)
    if connection is not None:
        await db.delete(connection)
        await db.commit()


async def list_available_repositories(db: AsyncSession, user: User) -> list[AvailableRepository]:
    """Live list from GitHub (not persisted), cross-referenced against what
    this user already has tracked in `repositories`. Calls the module-level
    `list_repositories()` directly rather than going through
    `_build_provider()` - listing repos only needs the access token, never
    the OAuth App's client_id/secret, so a PAT-only connection (which never
    has those configured, by design) must not be gated on them here."""
    connection = await get_connection(db, user)
    if connection is None:
        raise UnauthorizedError("GitHub is not connected for this user.")

    access_token = decrypt_secret(connection.encrypted_access_token)
    repos = await list_repositories(access_token)

    tracked_result = await db.execute(
        select(Repository.github_repo_id).where(Repository.user_id == user.id)
    )
    tracked_ids = {row[0] for row in tracked_result.all()}

    return [
        AvailableRepository(
            provider_repo_id=repo.provider_repo_id,
            owner=repo.owner,
            name=repo.name,
            full_name=repo.full_name,
            private=repo.private,
            default_branch=repo.default_branch,
            html_url=repo.html_url,
            is_selected=repo.provider_repo_id in tracked_ids,
        )
        for repo in repos
    ]


async def list_tracked_repositories(db: AsyncSession, user: User) -> list[Repository]:
    result = await db.execute(
        select(Repository).where(Repository.user_id == user.id).order_by(Repository.full_name)
    )
    return list(result.scalars().all())


async def set_selected_repositories(
    db: AsyncSession, user: User, selection: RepositorySelectionRequest
) -> list[Repository]:
    """Replaces the user's tracked repositories with exactly the given set:
    inserts new ones, updates metadata for ones already tracked, and
    removes ones no longer selected."""
    existing_result = await db.execute(select(Repository).where(Repository.user_id == user.id))
    existing_by_github_id = {repo.github_repo_id: repo for repo in existing_result.scalars().all()}

    selected_ids = {item.provider_repo_id for item in selection.repositories}

    for github_repo_id, existing_repo in existing_by_github_id.items():
        if github_repo_id not in selected_ids:
            await db.delete(existing_repo)

    for item in selection.repositories:
        matching_repo = existing_by_github_id.get(item.provider_repo_id)
        if matching_repo is None:
            db.add(
                Repository(
                    user_id=user.id,
                    github_repo_id=item.provider_repo_id,
                    owner=item.owner,
                    name=item.name,
                    full_name=item.full_name,
                    private=item.private,
                    default_branch=item.default_branch,
                    html_url=item.html_url,
                )
            )
        else:
            matching_repo.owner = item.owner
            matching_repo.name = item.name
            matching_repo.full_name = item.full_name
            matching_repo.private = item.private
            matching_repo.default_branch = item.default_branch
            matching_repo.html_url = item.html_url

    await db.commit()
    return await list_tracked_repositories(db, user)
