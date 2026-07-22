"""Orchestrates the full indexing pipeline: clone -> detect language ->
parse -> build graph -> persist -> (temp clone directory is always cleaned
up by `clone_repository`, success or failure).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.exceptions import AppError
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.graph.builder import build_graph
from app.indexer.models.architecture import ArchitectureModel
from app.indexer.parsers.registry import get_parser
from app.indexer.scanner.language_detector import DetectedLanguage, detect_language
from app.indexer.scanner.repository_cloner import clone_repository
from app.models.github_connection import GitHubConnection
from app.models.repository import Repository

IndexingSummary = dict[str, int]


class UnsupportedRepositoryError(AppError):
    status_code = 422
    error_code = "unsupported_repository"


async def _get_access_token(db: AsyncSession, user_id: object) -> str | None:
    result = await db.execute(select(GitHubConnection).where(GitHubConnection.user_id == user_id))
    connection = result.scalar_one_or_none()
    return decrypt_secret(connection.encrypted_access_token) if connection else None


def _summarize(model: ArchitectureModel) -> IndexingSummary:
    return {
        "controllers": len(model.controllers),
        "endpoints": sum(len(c.endpoints) for c in model.controllers),
        "services": len(model.services),
        "feign_clients": len(model.feign_clients),
        "kafka_producers": len(model.kafka_producers),
        "kafka_consumers": len(model.kafka_consumers),
        "maven_dependencies": len(model.maven_dependencies),
    }


async def index_repository(
    repository_id: str,
    html_url: str,
    ref: str,
    access_token: str | None = None,
) -> IndexingSummary:
    """The DB-independent core of the pipeline — clone, detect, parse,
    build, persist. Takes plain values rather than ORM objects specifically
    so it's testable without a database at all (see
    tests/integration/test_indexing_pipeline.py).
    """
    async with clone_repository(html_url, ref, access_token) as repo_path:
        language = detect_language(repo_path)
        parser = get_parser(language) if language != DetectedLanguage.UNSUPPORTED else None
        if parser is None:
            raise UnsupportedRepositoryError(
                f"Repository language/framework is not supported yet "
                f"(detected: {language}). Only Java + Spring Boot (Maven) is "
                f"supported in this phase."
            )

        model = parser.parse(repo_path)

    graph = build_graph(repository_id, model)
    graph_repository = Neo4jGraphRepository(get_driver())
    await graph_repository.replace_repository_graph(repository_id, graph)

    return _summarize(model)


async def run_indexing(db: AsyncSession, repository: Repository) -> IndexingSummary:
    """The DB-aware entrypoint: looks up the repository owner's GitHub
    token (if connected) and runs `index_repository` with it."""
    access_token = await _get_access_token(db, repository.user_id)
    return await index_repository(
        repository_id=str(repository.id),
        html_url=repository.html_url,
        ref=repository.default_branch,
        access_token=access_token,
    )
