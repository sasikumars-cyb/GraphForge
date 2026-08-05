"""Orchestrates the full indexing pipeline: clone -> detect language ->
parse -> build graph -> persist -> (temp clone directory is always cleaned
up by `clone_repository`, success or failure).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.exceptions import AppError
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.graph.builder import build_graph
from app.indexer.graph.cross_repo_linker import relink_account
from app.indexer.hypotheses.repository_evidence import extract_repository_evidence
from app.indexer.hypotheses.shadow_runner import run_shadow_hypothesis_generation
from app.indexer.models.architecture import ArchitectureModel
from app.indexer.parsers.registry import get_parser
from app.indexer.scanner.language_detector import DetectedLanguage, detect_language
from app.indexer.scanner.repository_cloner import clone_repository
from app.knowledge_engine.shadow_compare import shadow_compare_materialized_graph
from app.models.github_connection import GitHubConnection
from app.models.repository import Repository

logger = logging.getLogger(__name__)

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
        "python_modules": len(model.python_modules),
        "python_classes": sum(len(m.classes) for m in model.python_modules),
        "python_functions": sum(
            len(m.functions) + sum(len(c.methods) for c in m.classes) for m in model.python_modules
        ),
        "python_dependencies": len(model.python_dependencies),
    }


async def index_repository(
    repository_id: str,
    html_url: str,
    ref: str,
    access_token: str | None = None,
    db: AsyncSession | None = None,
) -> IndexingSummary:
    """The DB-independent core of the pipeline — clone, detect, parse,
    build, persist. Takes plain values rather than ORM objects specifically
    so it's testable without a database at all (see
    tests/integration/test_indexing_pipeline.py).

    `db` (added in ADR 0018 RFC-04) is optional and touches nothing except
    the shadow reasoning pipeline's Engineering Memory persistence step —
    every existing call site (including every test predating RFC-04) omits
    it and gets byte-identical behavior to before. See
    `app.indexer.hypotheses.shadow_runner`'s module docstring for the full
    reasoning.
    """
    async with clone_repository(html_url, ref, access_token) as repo_path:
        language = detect_language(repo_path)
        parser = get_parser(language) if language != DetectedLanguage.UNSUPPORTED else None
        if parser is None:
            raise UnsupportedRepositoryError(
                f"Repository language/framework is not supported yet "
                f"(detected: {language}). Java + Spring Boot (Maven) and "
                f"Python are supported in this phase."
            )

        model = parser.parse(repo_path)
        # Frontier Hypothesis Generator (ADR 0018) Finding 1: README/
        # manifest/config content isn't recoverable from `ArchitectureModel`
        # and the clone won't exist past this block — read it now, while
        # `repo_path` is still valid, language-agnostic and generator-
        # agnostic (see repository_evidence.py's own docstring).
        repository_evidence_facts = extract_repository_evidence(repo_path)

    graph = build_graph(repository_id, model)
    graph_repository = Neo4jGraphRepository(get_driver())
    await graph_repository.replace_repository_graph(repository_id, graph)

    # ADR 0018 RFC-02B/RFC-04: shadow-mode only, after the real graph is
    # already committed — never raises, never affects `graph` or this
    # function's return value. See `run_shadow_hypothesis_generation`'s own
    # docstring for why `ref` stands in for a commit SHA here, and why `db`
    # is optional.
    await run_shadow_hypothesis_generation(
        repository_id=repository_id,
        commit_sha=ref,
        model=model,
        db=db,
        repository_evidence_facts=repository_evidence_facts,
    )

    # KAN-16 — shadow-compare the Materializer's projection against the
    # graph just written, on every real indexing run (not just the one
    # fixture the replay test covers). Diagnostic only: runs after shadow
    # hypothesis generation has had a chance to persist evidence for this
    # exact commit, never raises, never affects `graph` or this function's
    # return value. `db is None` (every pre-RFC-04 call site and test)
    # skips this the same way it skips shadow persistence itself - there's
    # no Engineering Memory to materialize from without a session.
    if db is not None:
        await shadow_compare_materialized_graph(db, repository_id, graph)

    return _summarize(model)


async def run_indexing(db: AsyncSession, repository: Repository) -> IndexingSummary:
    """The DB-aware entrypoint: looks up the repository owner's GitHub
    token (if connected) and runs `index_repository` with it.

    Also (re)computes the account's entire cross-repository graph edge set
    (see `app.indexer.graph.cross_repo_linker.relink_account`) — not just
    edges touching the repository just indexed, because a repository
    indexed earlier may reference *this* one (a Feign client, a shared
    topic) and had nothing to link against until now. `relink_account` is
    the single entry point for this (ADR 0010, invariant I7); it batch-
    fetches every repository's relationship-relevant nodes once (`O(N)`
    Neo4j round-trips, not `O(N)` per repository) and serializes concurrent
    relinks for the same account with a blocking advisory lock (waits
    rather than skipping, so a concurrently-indexing repository is never
    dropped from the account's cross-repository graph). A relink failure is
    logged and swallowed rather than failing the indexing job —
    the repository's own graph is already committed and usable on its own.
    """
    access_token = await _get_access_token(db, repository.user_id)
    summary = await index_repository(
        repository_id=str(repository.id),
        html_url=repository.html_url,
        ref=repository.default_branch,
        access_token=access_token,
        db=db,
    )

    graph_repository = Neo4jGraphRepository(get_driver())
    try:
        await relink_account(graph_repository=graph_repository, db=db, user_id=repository.user_id)
    except Exception:
        logger.exception("cross_repo_relink_failed user_id=%s", repository.user_id)

    return summary
