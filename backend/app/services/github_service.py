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
from app.integrations.github import GitHubOAuthProvider
from app.models.github_connection import GitHubConnection
from app.models.repository import Repository
from app.models.user import User
from app.schemas.github import AvailableRepository, RepositorySelectionRequest

_STATE_EXPIRY = timedelta(minutes=10)


class GitHubNotConfiguredError(AppError):
    """Raised when GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET aren't set."""

    status_code = 503
    error_code = "github_not_configured"


def _build_provider() -> GitHubOAuthProvider:
    settings = get_settings()
    if not settings.github_client_id or not settings.github_client_secret:
        raise GitHubNotConfiguredError(
            "GitHub integration is not configured. Set GITHUB_CLIENT_ID and "
            "GITHUB_CLIENT_SECRET (see docs/setup.md)."
        )
    return GitHubOAuthProvider(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
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


def get_connect_authorization_url(user: User) -> str:
    """Builds the GitHub authorize URL, with a signed, time-limited `state`
    that encodes which user initiated the connect flow — GitHub's redirect
    back to /callback carries no Authorization header, so this is how the
    callback knows whose GitHubConnection to write.
    """
    provider = _build_provider()
    state = create_access_token(subject=str(user.id), expires_delta=_STATE_EXPIRY)
    return provider.get_authorization_url(state)


async def handle_oauth_callback(db: AsyncSession, code: str, state: str) -> User:
    """Verifies `state`, exchanges `code` for a token, and upserts the
    GitHubConnection for the user `state` identifies. Returns that user."""
    payload = decode_access_token(state)
    subject = payload.get("sub")
    if subject is None:
        raise UnauthorizedError("Invalid OAuth state.")

    user = await db.get(User, uuid.UUID(subject))
    if user is None:
        raise NotFoundError("User not found.")

    provider = _build_provider()
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
        )
        db.add(connection)
    else:
        connection.github_user_id = profile.provider_user_id
        connection.github_username = profile.name or profile.provider_user_id
        connection.encrypted_access_token = encrypted_token

    await db.commit()
    return user


async def disconnect(db: AsyncSession, user: User) -> None:
    connection = await get_connection(db, user)
    if connection is not None:
        await db.delete(connection)
        await db.commit()


async def list_available_repositories(db: AsyncSession, user: User) -> list[AvailableRepository]:
    """Live list from GitHub (not persisted), cross-referenced against what
    this user already has tracked in `repositories`."""
    connection = await get_connection(db, user)
    if connection is None:
        raise UnauthorizedError("GitHub is not connected for this user.")

    provider = _build_provider()
    access_token = decrypt_secret(connection.encrypted_access_token)
    repos = await provider.list_repositories(access_token)

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
