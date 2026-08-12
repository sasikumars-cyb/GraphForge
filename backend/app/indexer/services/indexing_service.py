"""Orchestrates the full indexing pipeline: clone -> detect language ->
parse -> build graph -> persist -> (temp clone directory is always cleaned
up by `clone_repository`, success or failure).

KAN-32: also orchestrates the incremental alternative — re-parse only the
files a push actually changed and merge that into the existing graph,
instead of a full clone+parse+replace. `run_indexing` (the DB-aware
entrypoint every real indexing run goes through) decides per-run which
path applies; `index_repository` (the full path) is completely unchanged
by this and remains the fallback for a first index, an unsafe diff, or
any failure determining what changed — see `_attempt_incremental_index`.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.exceptions import AppError
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.extractors.sql_file_extractor import extract_sql_files
from app.indexer.graph.builder import build_graph
from app.indexer.graph.cross_repo_linker import relink_account
from app.indexer.hypotheses.generic_language_runner import run_generic_language_fallback
from app.indexer.hypotheses.repository_evidence import extract_repository_evidence
from app.indexer.hypotheses.shadow_runner import run_shadow_hypothesis_generation
from app.indexer.models.architecture import ArchitectureModel
from app.indexer.parsers.registry import get_parser
from app.indexer.scanner.incremental import (
    ChangedFile,
    compute_changed_files,
    is_safe_for_incremental_update,
    materialize_changed_files,
    resolve_branch_head_sha,
)
from app.indexer.scanner.language_detector import DetectedLanguage, detect_language
from app.indexer.scanner.repository_cloner import clone_repository, resolve_head_commit_sha
from app.knowledge_engine.materializer import materialize_repository_graph
from app.knowledge_engine.shadow_compare import shadow_compare_materialized_graph
from app.models.github_connection import GitHubConnection
from app.models.repository import Repository

logger = logging.getLogger(__name__)

IndexingSummary = dict[str, int]

# The only three values `Settings.graph_authority_mode` is recognized for -
# see that field's own docstring. An unrecognized value degrades to the
# safest, zero-behavior-change default rather than raising: a typo in an
# operator's env var must never turn into an indexing outage.
_VALID_GRAPH_AUTHORITY_MODES = frozenset({"shadow", "shadow_compare", "authoritative"})
_DEFAULT_GRAPH_AUTHORITY_MODE = "shadow_compare"


def _graph_authority_mode() -> str:
    mode = get_settings().graph_authority_mode
    if mode not in _VALID_GRAPH_AUTHORITY_MODES:
        logger.warning(
            "unknown_graph_authority_mode configured=%r falling_back_to=%r",
            mode,
            _DEFAULT_GRAPH_AUTHORITY_MODE,
        )
        return _DEFAULT_GRAPH_AUTHORITY_MODE
    return mode


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
        "sql_files": len(model.sql_files),
        "sql_table_references": len(model.sql_table_references),
    }


async def _attempt_generic_language_fallback(
    *,
    repository_id: str,
    repo_path: Path,
    language: DetectedLanguage,
    commit_sha: str,
    db: AsyncSession | None,
    repository_name: str | None,
) -> IndexingSummary | None:
    """RFC-07 — the "relax the hard 422 gate" activation: when no
    `ILanguageParser` matches (the caller's `get_parser(language)` already
    returned None), try the generic evidence/LLM fallback instead of
    unconditionally failing. Returns `None` (caller then raises
    `UnsupportedRepositoryError` exactly as before) when the fallback
    isn't applicable or didn't produce anything - never partially
    succeeds silently.

    Two preconditions, both required: `Settings.enable_generic_language_fallback`
    (off by default - a real LLM call, opt-in the same way
    `enable_frontier_llm_generator` is) and `db is not None` (there is no
    `ArchitectureModel`/`build_graph()` output for this path to fall back
    to the way "authoritative" mode's `_write_repository_graph` can -
    Engineering Memory persistence is the *only* way anything from this
    path reaches a graph at all, so without a session there is nothing
    useful this function can do).
    """
    if db is None or not get_settings().enable_generic_language_fallback:
        return None

    pack = await run_generic_language_fallback(
        repository_id=repository_id,
        commit_sha=commit_sha,
        repo_root=repo_path,
        language_label=str(language),
        db=db,
        repository_name=repository_name,
    )
    if pack is None:
        return None

    graph_repository = Neo4jGraphRepository(get_driver())
    try:
        materialized = await materialize_repository_graph(db, uuid.UUID(repository_id))
    except Exception:
        logger.exception(
            "generic_language_materialization_failed repository_id=%s", repository_id
        )
        return None

    await graph_repository.replace_repository_graph(repository_id, materialized)

    source_file_count = sum(1 for item in pack.items if item.kind == "source_file")
    return {
        "generic_language_fallback": 1,
        "generic_language_files_discovered": source_file_count,
        "materialized_nodes": len(materialized.nodes),
        "materialized_edges": len(materialized.edges),
    }


async def index_repository(
    repository_id: str,
    html_url: str,
    ref: str,
    access_token: str | None = None,
    db: AsyncSession | None = None,
    on_language_detected: Callable[[DetectedLanguage], None] | None = None,
    on_commit_resolved: Callable[[str], None] | None = None,
    repository_name: str | None = None,
) -> IndexingSummary:
    """The DB-independent core of the pipeline — clone, detect, parse,
    build, persist. Takes plain values rather than ORM objects specifically
    so it's testable without a database at all (see
    tests/integration/test_indexing_pipeline.py).

    `repository_name` (optional, defaults to None — every existing call
    site predating this parameter still works unchanged) becomes the
    Repository graph node's "name" property, the same way `build_graph`
    already sets one for every other node type. Omitting it just means
    that one Repository node renders as its raw id instead of a name
    wherever the frontend falls back to `properties.name ?? id`.

    `db` (added in ADR 0018 RFC-04) is optional and touches nothing except
    the shadow reasoning pipeline's Engineering Memory persistence step —
    every existing call site (including every test predating RFC-04) omits
    it and gets byte-identical behavior to before. See
    `app.indexer.hypotheses.shadow_runner`'s module docstring for the full
    reasoning.

    `on_language_detected`/`on_commit_resolved` (KAN-32) are optional
    callbacks invoked once `detect_language`/`resolve_head_commit_sha`
    resolve, purely so `run_indexing` can persist `Repository.
    last_indexed_language`/`last_indexed_commit_sha` (what a future
    incremental run needs — a parser to use and a commit to diff against
    — without re-cloning just to re-derive them) without this function's
    return type changing — `IndexingSummary` is asserted on by key/shape
    in enough tests (`tests/integration/test_indexing_pipeline.py` and
    others) that widening it was a real, avoidable blast radius for an
    optional, additive need. Every existing call site omits both and is
    unaffected.
    """
    async with clone_repository(html_url, ref, access_token) as repo_path:
        language = detect_language(repo_path)
        if on_language_detected is not None:
            on_language_detected(language)
        if on_commit_resolved is not None:
            commit_sha = await resolve_head_commit_sha(repo_path)
            if commit_sha is not None:
                on_commit_resolved(commit_sha)
        parser = get_parser(language) if language != DetectedLanguage.UNSUPPORTED else None
        if parser is None:
            fallback_summary = await _attempt_generic_language_fallback(
                repository_id=repository_id,
                repo_path=repo_path,
                language=language,
                commit_sha=ref,
                db=db,
                repository_name=repository_name,
            )
            if fallback_summary is not None:
                return fallback_summary
            raise UnsupportedRepositoryError(
                f"Repository language/framework is not supported yet "
                f"(detected: {language}). Java + Spring Boot (Maven) and "
                f"Python are supported in this phase."
            )

        model = parser.parse(repo_path)
        # Repo-wide `.sql` file discovery/lineage - unconditional, not
        # owned by any one `ILanguageParser` (see sql_file_extractor.py's
        # own docstring for why). Must run inside this `with` block too,
        # same as the parse above: the clone doesn't outlive it.
        model.sql_files, model.sql_table_references = extract_sql_files(repo_path)
        # Frontier Hypothesis Generator (ADR 0018) Finding 1: README/
        # manifest/config content isn't recoverable from `ArchitectureModel`
        # and the clone won't exist past this block — read it now, while
        # `repo_path` is still valid, language-agnostic and generator-
        # agnostic (see repository_evidence.py's own docstring).
        repository_evidence_facts = extract_repository_evidence(repo_path)

    graph = build_graph(repository_id, model, repository_name=repository_name)
    graph_repository = Neo4jGraphRepository(get_driver())
    mode = _graph_authority_mode() if db is not None else "shadow"

    # In "authoritative" mode the write is deferred until after shadow
    # hypothesis generation has had a chance to persist evidence this run
    # can materialize from (see `_write_repository_graph`). Every other
    # mode keeps today's exact ordering — write the builder's graph
    # immediately, unchanged from before this activation.
    if mode != "authoritative":
        await graph_repository.replace_repository_graph(repository_id, graph)

    # ADR 0018 RFC-02B/RFC-04: shadow-mode only, after the real graph is
    # already committed (for "shadow"/"shadow_compare") — never raises,
    # never affects `graph` or this function's return value. See
    # `run_shadow_hypothesis_generation`'s own docstring for why `ref`
    # stands in for a commit SHA here, and why `db` is optional.
    await run_shadow_hypothesis_generation(
        repository_id=repository_id,
        commit_sha=ref,
        model=model,
        db=db,
        repository_evidence_facts=repository_evidence_facts,
        repository_name=repository_name,
    )

    # KAN-16 — shadow-compare the Materializer's projection against the
    # builder's graph, on every real indexing run in "shadow_compare" or
    # "authoritative" mode (not just the one fixture the replay test
    # covers). Diagnostic only in "shadow_compare" (logs mismatches, never
    # affects what's written); in "authoritative" mode this same log is
    # what makes a divergence between the two paths visible even after
    # cutover, rather than silently invisible once the builder's payload
    # stops being written. Runs after shadow hypothesis generation has had
    # a chance to persist evidence for this exact commit. `db is None`
    # (every pre-RFC-04 call site and test) skips this the same way it
    # skips shadow persistence itself - there's no Engineering Memory to
    # materialize from without a session.
    if db is not None and mode in ("shadow_compare", "authoritative"):
        await shadow_compare_materialized_graph(db, repository_id, graph)

    if mode == "authoritative":
        await _write_repository_graph(
            db=db,  # type: ignore[arg-type]  # mode is only "authoritative" when db is not None
            graph_repository=graph_repository,
            repository_id=repository_id,
            fallback_graph=graph,
        )

    return _summarize(model)


async def _write_repository_graph(
    *,
    db: AsyncSession,
    graph_repository: IGraphRepository,
    repository_id: str,
    fallback_graph: GraphPayload,
) -> None:
    """The one write for "authoritative" mode: materialize Engineering
    Memory (just persisted by `run_shadow_hypothesis_generation` above)
    into a `GraphPayload` and write *that* — ADR 0018's stated end state,
    "Neo4j becomes a synced, rebuildable projection" of Engineering Memory,
    not the builder's direct output.

    Two failure modes are handled explicitly, both falling back to
    `fallback_graph` (the builder's own payload) rather than leaving the
    repository with no graph at all or a corrupted one:

    - Materialization raises (a Postgres error, a malformed evidence item)
      - caught here, not left to crash the whole indexing run over what
        is, for now, still a migration-safety measure.
    - Materialization succeeds but is suspiciously empty while the
      builder's own payload is not - the one shape a partial/failed
      Engineering Memory write could produce that wouldn't raise at all
      (e.g. `run_shadow_hypothesis_generation` failed to persist for a
      reason it swallows internally, per its own "never raises" contract).
      An authoritative write must never silently wipe a repository's graph
      to empty; a same-content builder write is always safer than that.

    This is the intentional trust boundary: `fallback_graph` is always a
    fully deterministic, already-validated-by-tests payload (the same one
    every non-authoritative mode writes as-is), so falling back to it never
    trades a trustworthy graph for an untrustworthy one - only for a less
    complete one (no confidence/provenance properties this run).
    """
    try:
        materialized = await materialize_repository_graph(db, uuid.UUID(repository_id))
    except Exception:
        logger.exception(
            "graph_materialization_failed repository_id=%s falling_back_to=builder_graph",
            repository_id,
        )
        await graph_repository.replace_repository_graph(repository_id, fallback_graph)
        return

    if not materialized.nodes and fallback_graph.nodes:
        logger.error(
            "graph_materialization_empty repository_id=%s builder_node_count=%d "
            "falling_back_to=builder_graph",
            repository_id,
            len(fallback_graph.nodes),
        )
        await graph_repository.replace_repository_graph(repository_id, fallback_graph)
        return

    await graph_repository.replace_repository_graph(repository_id, materialized)
    logger.info(
        "graph_materialized_authoritative repository_id=%s node_count=%d edge_count=%d",
        repository_id,
        len(materialized.nodes),
        len(materialized.edges),
    )


async def _index_changed_files(
    repository_id: str,
    html_url: str,
    language: DetectedLanguage,
    changed_files: list[ChangedFile],
    head_sha: str,
    access_token: str | None,
    repository_name: str | None = None,
) -> IndexingSummary:
    """KAN-32: re-parse only `changed_files` (fetched via GitHub's API —
    see `materialize_changed_files`, no `git clone`) and merge the result
    into the existing graph via `replace_repository_files_subgraph`
    instead of `index_repository`'s full clone+parse+replace.

    Only ever called after `is_safe_for_incremental_update` has already
    approved `changed_files` — this function does not re-check safety
    itself, matching `Neo4jGraphRepository.replace_repository_files_
    subgraph`'s own "callers own the decision" contract.

    The returned summary's keys are shaped like `_summarize`'s (for the
    same `IndexingSummary = dict[str, int]` consumers), but the *counts*
    describe only what was found among the just-reparsed files, not the
    repository as a whole — `files_reindexed` disambiguates that this was
    a scoped run, not a full one.
    """
    parser = get_parser(language)
    if parser is None:
        raise UnsupportedRepositoryError(f"No parser registered for language {language}.")

    async with materialize_changed_files(
        html_url, changed_files, head_sha, access_token
    ) as work_dir:
        model = parser.parse(work_dir)
        # `work_dir` only ever contains the files in `changed_files` (see
        # `materialize_changed_files`'s own docstring) - so this naturally
        # stays scoped to exactly the `.sql` files this incremental update
        # touched, matching `file_paths`/the deletion scope below. Running
        # the unscoped, repo-wide `extract_sql_files` here would be
        # incorrect only if `work_dir` held more than the changed files;
        # it doesn't.
        model.sql_files, model.sql_table_references = extract_sql_files(work_dir)

    graph = build_graph(repository_id, model, repository_name=repository_name)
    graph_repository = Neo4jGraphRepository(get_driver())
    # A rename touches two paths: the old one (nothing lives there
    # anymore — must be deleted) and the new one (already covered by
    # `graph`'s freshly-parsed nodes). Both go in the deletion scope;
    # only the new path is ever re-written.
    file_paths = [f.path for f in changed_files] + [
        f.previous_path for f in changed_files if f.previous_path
    ]
    await graph_repository.replace_repository_files_subgraph(repository_id, file_paths, graph)

    summary = _summarize(model)
    summary["files_reindexed"] = len(changed_files)
    return summary


async def _attempt_incremental_index(
    repository: Repository, access_token: str | None
) -> tuple[IndexingSummary, str] | None:
    """Decides whether this run can be served incrementally and, if so,
    runs it. Returns `(summary, head_sha)` on success, `None` if the
    caller should fall back to the full `index_repository` path instead.

    Every reason to decline — no prior index, an unresolvable head sha,
    an uncomputable diff, a diff `is_safe_for_incremental_update` rejects,
    or any error actually running the scoped update — is logged and
    treated identically: fall back, never raise. KAN-32's own acceptance
    criterion is that a full re-index always remains available as a
    fallback/repair path; this is what keeps that true by construction
    rather than by every caller remembering to catch something.
    """
    if repository.source != "github":
        # GitHub's Compare/Contents/branches REST endpoints are what this
        # entire module is built on — a `source="local"` repository's
        # `html_url` is a filesystem path (see
        # `app.services.local_repository_service`), not a github.com URL,
        # and was never going to resolve against any of them. The full
        # `index_repository` path already handles both sources correctly
        # (plain `git clone` works against a local path too), so this is
        # simply "incremental doesn't apply here," not a gap.
        return None
    if not repository.last_indexed_commit_sha or not repository.last_indexed_language:
        return None
    try:
        language = DetectedLanguage(repository.last_indexed_language)
    except ValueError:
        return None

    head_sha = await resolve_branch_head_sha(
        repository.html_url, repository.default_branch, access_token
    )
    if head_sha is None:
        return None

    changed_files = await compute_changed_files(
        repository.html_url, repository.last_indexed_commit_sha, head_sha, access_token
    )
    if changed_files is None or not is_safe_for_incremental_update(changed_files):
        return None

    try:
        summary = await _index_changed_files(
            repository_id=str(repository.id),
            html_url=repository.html_url,
            language=language,
            changed_files=changed_files,
            head_sha=head_sha,
            access_token=access_token,
            repository_name=repository.full_name,
        )
    except Exception:
        logger.exception(
            "incremental_indexing_failed_falling_back_to_full repository_id=%s", repository.id
        )
        return None

    logger.info(
        "incremental_indexing_succeeded repository_id=%s files_changed=%d head_sha=%s",
        repository.id,
        len(changed_files),
        head_sha,
    )
    return summary, head_sha


async def run_indexing(db: AsyncSession, repository: Repository) -> IndexingSummary:
    """The DB-aware entrypoint: looks up the repository owner's GitHub
    token (if connected) and runs either an incremental or a full index,
    then (re)computes cross-repository edges the same way regardless of
    which path ran.

    KAN-32: tries `_attempt_incremental_index` first — a prior indexed
    commit, a resolvable diff, and a diff simple enough to trust (see
    that function and `is_safe_for_incremental_update`) are all required
    for it to actually run; any gap falls back to the exact `index_repository`
    full path this function always used before KAN-32, unchanged. Either
    way, `Repository.last_indexed_commit_sha`/`last_indexed_language`/
    `last_indexed_at` are updated on success so the *next* run has
    something to diff against.

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

    head_sha: str | None
    # "authoritative" mode always takes the full `index_repository` path:
    # KAN-32's incremental path (`_index_changed_files`) never runs shadow
    # hypothesis generation and never persists to Engineering Memory (see
    # its own module docstring - only `index_repository` does), so
    # materializing from Engineering Memory after an incremental update
    # would read a stale, pre-change evidence pack and write a stale
    # authoritative graph. A full re-index is slower but never wrong;
    # that's the explicit tradeoff documented on
    # `Settings.graph_authority_mode`.
    incremental = (
        None
        if _graph_authority_mode() == "authoritative"
        else await _attempt_incremental_index(repository, access_token)
    )
    if incremental is not None:
        summary, head_sha = incremental
    else:
        detected_language: DetectedLanguage | None = None
        resolved_commit_sha: str | None = None

        def _capture_language(language: DetectedLanguage) -> None:
            nonlocal detected_language
            detected_language = language

        def _capture_commit(commit_sha: str) -> None:
            nonlocal resolved_commit_sha
            resolved_commit_sha = commit_sha

        summary = await index_repository(
            repository_id=str(repository.id),
            html_url=repository.html_url,
            ref=repository.default_branch,
            access_token=access_token,
            db=db,
            on_language_detected=_capture_language,
            on_commit_resolved=_capture_commit,
            repository_name=repository.full_name,
        )
        # `resolve_head_commit_sha` reads the clone `index_repository` just
        # made (plain `git rev-parse HEAD`) — works identically for
        # `source="github"` and `source="local"` repositories and needs no
        # extra network round-trip, unlike re-asking GitHub's API for the
        # branch head after the fact would.
        head_sha = resolved_commit_sha
        if head_sha is not None and detected_language is not None:
            repository.last_indexed_language = detected_language.value

    if head_sha is not None:
        repository.last_indexed_commit_sha = head_sha
        repository.last_indexed_at = datetime.now(UTC)
        await db.commit()

    graph_repository = Neo4jGraphRepository(get_driver())
    try:
        await relink_account(graph_repository=graph_repository, db=db, user_id=repository.user_id)
    except Exception:
        logger.exception("cross_repo_relink_failed user_id=%s", repository.user_id)

    return summary
