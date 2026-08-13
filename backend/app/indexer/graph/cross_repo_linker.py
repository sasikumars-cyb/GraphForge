"""Cross-repository graph edges — the one place two different repositories'
subgraphs get connected.

Every edge `app.indexer.graph.builder` writes is strictly single-repository
(node ids are namespaced `f"{repository_id}:..."`, and `replace_repository_
graph` deletes/rewrites one repository at a time) — Neo4j has never had an
edge connecting two different `repository_id`s before this module.

The signals used here already exist per repository, in
`app.indexer.models.architecture.ArchitectureModel` and the `Component`/
`KafkaTopic`/`MavenDependency`/`PythonDependency` nodes `graph/builder.py`
already writes from it — a Feign client's `target_name`, a Kafka topic's
literal name, a Maven/Python dependency's coordinates. This module is what
compares those, for one repository, against every *other* repository the
same user has indexed, and records the deterministic matches as real graph
edges rather than leaving the relationship implicit in two unconnected
per-repository subgraphs.

`CROSS_REPO_LINK_RULES` is a registry, not an if-chain — same shape as
`app.context_pipeline.reasoning.capabilities.CAPABILITIES` — so a new
relationship type (a shared Terraform module, a shared DB schema) is one
more entry here, not a rewrite of this module.

ADR 0010 (Theme C) shape — batch-fetch, then evaluate in memory: every
repository's relationship-relevant Neo4j nodes are fetched exactly *once*
per `relink_account` call (`load_all_repo_nodes`, O(N) round-trips), then
every pairwise comparison runs against that already-fetched data
(`compute_edges`, pure, no I/O, O(N²) in-memory work — cheap next to a
network round-trip). `relink_account` is the *only* entry point anything
outside this module should call (ADR 0010, invariant I7 and §6) — it is
also the sole place cross-repository linking is guarded against two
concurrent indexing runs for the same account racing each other
(`pg_advisory_xact_lock` — blocking, not "try and skip": every relink for
an account eventually runs to completion, none are silently dropped under
contention).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode
from app.models.indexing_job import IndexingJob
from app.models.repository import Repository

logger = logging.getLogger(__name__)

# Common service-naming suffixes stripped before comparing a Feign client's
# `target_name` (or a dependency's coordinate) against another repository's
# own name — "etl-core-service" and "etl-core" naming the same repository is
# a convention this codebase's own indexed repos already follow, not a
# guess. Deliberately excludes generic words like "core" that legitimately
# appear inside a repository's own name (stripping it from "etl-core" would
# turn it into "etl", which is not the same repository as "etl-core-utils").
# Equality after stripping is still required — never a substring match — so
# "etl-core-utils" is never mistaken for "etl-core".
_SUFFIX_RE = re.compile(r"[-_](service|client|api)$", re.IGNORECASE)

# A trailing language/runtime tag on a repository's own name — this
# fleet's polyglot naming convention is `<domain>-service-<language>`
# (`inventory-service-python`, `payment-service-java`), so a Feign
# client's target name (`inventory-service`, no language tag: a Feign
# client names the *service*, not the repository housing its Java/Python
# implementation) never matches the repository name literally or after
# only the service/client/api strip above. Treating this as a same-repo
# naming alias — never a fuzzy/substring match, still full equality after
# stripping both suffixes — closes that gap without weakening matching:
# "inventory-service-python" and "inventory-service-java" still normalize
# to different names ("inventory") only when *both* actually reduce to
# the same domain; two distinct domains are never conflated because the
# domain portion itself is never touched.
_LANGUAGE_SUFFIX_RE = re.compile(
    r"[-_](python|java|go|golang|node|nodejs|javascript|typescript|js|ts)$", re.IGNORECASE
)


def _normalize(name: str) -> str:
    stripped = _LANGUAGE_SUFFIX_RE.sub("", name.strip())
    stripped = _SUFFIX_RE.sub("", stripped)
    return stripped.lower()


def _identifier_match(a: str, b: str) -> bool:
    """Whether `a` and `b` name the same repository — exact match, or exact
    match once each side's naming-convention suffix is stripped. Never a
    substring match: that would let "etl-core-utils" match "etl-core"."""
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower() or _normalize(a) == _normalize(b)


@dataclass(frozen=True)
class RepoNodes:
    """One repository's relationship-relevant nodes, read once (see
    `load_all_repo_nodes`) and reused across every pairwise rule evaluation
    — never re-fetched per pair."""

    repository_id: str
    name: str
    feign_clients: list[GraphNode]
    maven_dependencies: list[GraphNode]
    python_dependencies: list[GraphNode]
    produces_topic_names: frozenset[str]
    consumes_topic_names: frozenset[str]
    # ADR 0010 §4 — the repository's own graph version (its most recent
    # completed `IndexingJob.finished_at`, ISO-formatted) at the moment
    # these nodes were fetched. Stamped onto every edge this repository is
    # the source of, so an edge's freshness is a pure comparison against
    # whatever `graph_version` is current for the repository right now —
    # never a separately tracked "is this stale" fact. Defaults to `None`
    # (honestly "unknown version") rather than requiring every caller,
    # including hand-built test fixtures with no `IndexingJob` row at all,
    # to supply one.
    graph_version: str | None = None
    # RFC-0012 — every `PythonImport` node this repository's own source
    # produced (see `graph/builder.py`'s `_build_python_graph`): an import
    # that never resolved to one of this repository's own modules, so it
    # names something external — usually a third-party package, sometimes
    # another indexed repository's own published package used but never
    # declared in a manifest. Matched against other repositories by
    # `_source_level_import` below, exactly like `python_dependencies` is
    # matched by `_shared_dependency_name`, just from a different evidence
    # source (source code, not a manifest).
    python_imports: list[GraphNode] = field(default_factory=list)
    # RFC-0012 — this repository's own self-declared package/distribution
    # name (PEP 621/Poetry), when it has one — see `ArchitectureModel.
    # package_name`'s docstring for why this is commonly *different* from
    # `name` (the git repository name) and why a source import can only
    # ever be matched against this, not `name` alone.
    package_name: str | None = None


def _repository_node_id(repository_id: str) -> str:
    return f"{repository_id}:repository"


def _feign_service_calls(source: RepoNodes, other: RepoNodes) -> list[GraphEdge]:
    """A Spring `@FeignClient(name="...")` in `source` naming `other`."""
    vias: list[str] = []
    targets: list[str] = []
    for feign in source.feign_clients:
        target_name = str(feign.properties.get("target_name", ""))
        if target_name and _identifier_match(target_name, other.name):
            vias.append(str(feign.properties.get("name", "")))
            targets.append(target_name)
    if not vias:
        return []
    return [
        GraphEdge(
            source_id=_repository_node_id(source.repository_id),
            target_id=_repository_node_id(other.repository_id),
            type="CALLS_SERVICE",
            # "structural", not "heuristic" (ADR 0010, Theme E): a literal
            # `@FeignClient` target name is as certain as this codebase's
            # matching ever gets — the same confidence-vocabulary precedent
            # `reference_detection.py` already uses for a structural match.
            properties={"via": vias, "target_name": targets, "confidence": "structural"},
        )
    ]


def _kafka_topic_overlap(source: RepoNodes, other: RepoNodes) -> list[GraphEdge]:
    """`source` and `other` producing/consuming at least one of the same
    literal Kafka topic name — a real producer/consumer relationship, not a
    guess, since both sides come from `@KafkaListener`/`KafkaTemplate.send`
    literals (see `ArchitectureModel.kafka_producers`/`kafka_consumers`)."""
    source_topics = source.produces_topic_names | source.consumes_topic_names
    other_topics = other.produces_topic_names | other.consumes_topic_names
    shared = sorted(source_topics & other_topics)
    if not shared:
        return []
    return [
        GraphEdge(
            source_id=_repository_node_id(source.repository_id),
            target_id=_repository_node_id(other.repository_id),
            type="SHARES_TOPIC",
            # "structural" (ADR 0010, Theme E): both sides come from a
            # `@KafkaListener`/`KafkaTemplate.send` literal, not a guess.
            properties={"topics": shared, "confidence": "structural"},
        )
    ]


def _shared_dependency_name(source: RepoNodes, other: RepoNodes) -> list[GraphEdge]:
    """`source` declaring a Maven/Python dependency whose coordinate matches
    another indexed repository's own name — a shared-internal-library
    signal. Deliberately lower confidence than the other two rules: a
    dependency coordinate is external-facing (a published artifact name),
    so a name match is a real but heuristic signal, not a structural one
    like a Feign target or a Kafka topic literal."""
    matched: list[str] = []
    for dep in source.maven_dependencies:
        artifact_id = str(dep.properties.get("artifact_id", ""))
        if artifact_id and _identifier_match(artifact_id, other.name):
            matched.append(artifact_id)
    for dep in source.python_dependencies:
        dep_name = str(dep.properties.get("name", ""))
        if dep_name and _identifier_match(dep_name, other.name):
            matched.append(dep_name)
    if not matched:
        return []
    return [
        GraphEdge(
            source_id=_repository_node_id(source.repository_id),
            target_id=_repository_node_id(other.repository_id),
            type="DEPENDS_ON_REPOSITORY",
            properties={"confidence": "heuristic", "dependencies": sorted(set(matched))},
        )
    ]


def _source_level_import(source: RepoNodes, other: RepoNodes) -> list[GraphEdge]:
    """`source`'s own code importing a module whose name matches another
    indexed repository's identity — its git repository name *or* its
    self-declared package name (`other.package_name`; see `RepoNodes.
    package_name`'s docstring for why both are checked: a repository's
    published package name is frequently not its repository name).

    RFC-0012 — the generic source-level counterpart to
    `_shared_dependency_name` above: that rule only sees a dependency the
    manifest actually *declares*; a repository can genuinely depend on
    another one's package purely through an `import` statement — module-
    level or deferred inside a function body, `extract_imports` (see
    `graph/builder.py`) doesn't distinguish — with no manifest entry at
    all (an environment-provided/runtime-installed package, most
    commonly). This rule is what makes that dependency visible instead of
    invisible to `cross_repo_linker` entirely.

    Deliberately the *only* rule of the four whose edges get a further,
    cross-pair pass after this function returns (see `compute_edges`'s
    ambiguity handling below) — an import is looked up against one `other`
    repository at a time here, exactly like every other rule, but unlike a
    Feign target or a manifest coordinate, an unresolved import name is
    genuinely more likely to coincidentally match more than one indexed
    repository (a short, common package name is far more likely to arise
    from an *unresolved* import than from a deliberately-declared
    dependency) — `compute_edges` is where that global view exists, this
    function's job is only to report every match it finds, honestly,
    without trying to guess ambiguity from a single pair.

    Reuses `_identifier_match` unchanged, per the same exact-or-suffix-
    stripped-normalization, never-substring rule every other rule here
    already relies on — deliberately not hardened further for imports
    specifically (e.g. no minimum-length/generic-word denylist): that kind
    of blacklist is exactly what a shared/common-name heuristic would look
    like, and this module's job is to report matches, not decide which
    ones are trustworthy enough to keep — that judgment belongs entirely
    to the specificity/degree weighting downstream (RFC-0012 Problem A,
    `capabilities._corroboration_evidence`), which sees every repository's
    matches at once and can tell "rare" from "common," something no
    single pairwise rule here ever can.
    """
    matched: list[str] = []
    for imp in source.python_imports:
        module = str(imp.properties.get("module", ""))
        if not module:
            continue
        if _identifier_match(module, other.name) or (
            other.package_name and _identifier_match(module, other.package_name)
        ):
            matched.append(module)
    if not matched:
        return []
    return [
        GraphEdge(
            source_id=_repository_node_id(source.repository_id),
            target_id=_repository_node_id(other.repository_id),
            type="IMPORTS_REPOSITORY",
            properties={"confidence": "heuristic", "imports": sorted(set(matched))},
        )
    ]


@dataclass(frozen=True)
class CrossRepoLinkRule:
    name: str
    rel_type: str
    # Pure function: this repository's nodes + one other repository's nodes
    # -> edges *from* this repository *to* that other one. Never the reverse
    # direction — each repository's own linking pass only ever computes and
    # writes its own outgoing edges (see
    # `IGraphRepository.replace_cross_repository_edges`'s scoped delete).
    find_edges: Callable[[RepoNodes, RepoNodes], list[GraphEdge]]


CROSS_REPO_LINK_RULES: tuple[CrossRepoLinkRule, ...] = (
    CrossRepoLinkRule(
        name="feign_service_calls", rel_type="CALLS_SERVICE", find_edges=_feign_service_calls
    ),
    CrossRepoLinkRule(
        name="kafka_topic_overlap", rel_type="SHARES_TOPIC", find_edges=_kafka_topic_overlap
    ),
    CrossRepoLinkRule(
        name="shared_dependency_name",
        rel_type="DEPENDS_ON_REPOSITORY",
        find_edges=_shared_dependency_name,
    ),
    CrossRepoLinkRule(
        name="source_level_import",
        rel_type="IMPORTS_REPOSITORY",
        find_edges=_source_level_import,
    ),
)


async def _latest_graph_versions(
    db: AsyncSession, repository_ids: list[str]
) -> dict[str, str | None]:
    """Each repository's `graph_version` (ADR 0010 §4) — its most recent
    *completed* `IndexingJob.finished_at`, ISO-formatted. One query for the
    whole batch, reduced to "latest per repository" in Python, rather than
    one query per repository."""
    if not repository_ids:
        return {}
    result = await db.execute(
        select(IndexingJob.repository_id, IndexingJob.finished_at)
        .where(
            IndexingJob.repository_id.in_(repository_ids),
            IndexingJob.status == "completed",
            IndexingJob.finished_at.is_not(None),
        )
        .order_by(IndexingJob.finished_at.asc())
    )
    # Ordered ascending, so the last row seen per repository is the latest —
    # no separate max() pass needed.
    latest: dict[str, datetime] = {}
    for repository_id, finished_at in result.all():
        latest[str(repository_id)] = finished_at
    return {
        repo_id: latest[repo_id].isoformat() if repo_id in latest else None
        for repo_id in repository_ids
    }


async def _load_repo_nodes(
    graph_repository: IGraphRepository, repository_id: str, name: str, graph_version: str | None
) -> RepoNodes:
    feign_clients = await graph_repository.get_nodes_by_label(repository_id, "FeignClient")
    kafka_topics = await graph_repository.get_nodes_by_label(repository_id, "KafkaTopic")
    maven_dependencies = await graph_repository.get_nodes_by_label(repository_id, "MavenDependency")
    python_dependencies = await graph_repository.get_nodes_by_label(
        repository_id, "PythonDependency"
    )
    python_imports = await graph_repository.get_nodes_by_label(repository_id, "PythonImport")
    # The repository's own self-declared package name lives as a property
    # on its own `Repository` node (see `graph/builder.py`'s `build_graph`)
    # — reusing `get_nodes_by_label` for it rather than adding a new
    # `IGraphRepository` method, since exactly one `Repository` node ever
    # exists per `repository_id` and this already filters on it.
    repository_nodes = await graph_repository.get_nodes_by_label(repository_id, "Repository")
    package_name = (
        str(repository_nodes[0].properties.get("package_name") or "") or None
        if repository_nodes
        else None
    )

    # Producer/consumer direction lives on the PRODUCES_TO/CONSUMES_FROM
    # edge from whichever component owns it, not on the KafkaTopic node
    # itself. `get_kafka_topic_edges` reads only those two relationship
    # types targeting this repository's own KafkaTopic nodes — a targeted
    # query, not the full per-repository graph (ADR 0010 review, Weakness
    # #2: `get_full_graph` here previously scaled with total repository
    # graph size, not just Kafka topic count).
    topic_edges = await graph_repository.get_kafka_topic_edges(repository_id)
    topic_name_by_id = {n.id: str(n.properties.get("name", "")) for n in kafka_topics}
    produces: set[str] = set()
    consumes: set[str] = set()
    for edge in topic_edges:
        topic_name = topic_name_by_id.get(edge.target_id)
        if topic_name is None:
            continue
        if edge.type == "PRODUCES_TO":
            produces.add(topic_name)
        elif edge.type == "CONSUMES_FROM":
            consumes.add(topic_name)

    return RepoNodes(
        repository_id=repository_id,
        name=name,
        feign_clients=feign_clients,
        maven_dependencies=maven_dependencies,
        python_dependencies=python_dependencies,
        produces_topic_names=frozenset(produces),
        consumes_topic_names=frozenset(consumes),
        graph_version=graph_version,
        python_imports=python_imports,
        package_name=package_name,
    )


async def load_all_repo_nodes(
    graph_repository: IGraphRepository, db: AsyncSession, repositories: list[Repository]
) -> dict[str, RepoNodes]:
    """Fetch every repository's relationship-relevant Neo4j nodes exactly
    once — `O(N)` round-trips total for `N` repositories, not `O(N²)`. The
    entire point of splitting this from `compute_edges`: every pairwise
    comparison below reads from this already-fetched dict, never issuing a
    second Neo4j read for the same repository."""
    graph_versions = await _latest_graph_versions(db, [str(r.id) for r in repositories])
    return {
        str(repo.id): await _load_repo_nodes(
            graph_repository, str(repo.id), repo.name, graph_versions.get(str(repo.id))
        )
        for repo in repositories
    }


def compute_edges(nodes_by_repo: dict[str, RepoNodes]) -> dict[str, list[GraphEdge]]:
    """Pure, no I/O: every repository's outgoing cross-repository edges,
    evaluated against the already-fetched `nodes_by_repo` (ADR 0010, Theme
    C) — this is the `O(N²)` pairwise work, done entirely in memory.

    Every edge is stamped with `computed_at` and both sides'
    `graph_version` (ADR 0010 §4), so an edge's freshness is answerable
    later as a pure comparison against whatever each repository's current
    `graph_version` is, without a separate staleness-tracking mechanism.
    """
    now = datetime.now(tz=None).isoformat()
    edges_by_repo: dict[str, list[GraphEdge]] = {}
    for repo_id, source in nodes_by_repo.items():
        edges: list[GraphEdge] = []
        for other_id, other in nodes_by_repo.items():
            if other_id == repo_id:
                continue
            for rule in CROSS_REPO_LINK_RULES:
                for edge in rule.find_edges(source, other):
                    edges.append(
                        GraphEdge(
                            source_id=edge.source_id,
                            target_id=edge.target_id,
                            type=edge.type,
                            properties={
                                **edge.properties,
                                "computed_at": now,
                                "source_graph_version": source.graph_version,
                                "target_graph_version": other.graph_version,
                            },
                        )
                    )
        edges_by_repo[repo_id] = _downgrade_ambiguous_imports(edges)
    return edges_by_repo


def _downgrade_ambiguous_imports(edges: list[GraphEdge]) -> list[GraphEdge]:
    """RFC-0012 — one source repository's `IMPORTS_REPOSITORY` edges (from
    `_source_level_import`) only, downgraded when the *same* imported
    module name matched more than one other indexed repository.

    This is deliberately a pass over one source repository's *already-
    computed* edges, not something `_source_level_import` decides pairwise
    — a single `(source, other)` pair has no way to know whether the same
    import also matched some *other* repository; only this wider view
    (every edge `compute_edges` produced for `source`, across every
    `other`) can. Every other rule (`_feign_service_calls`,
    `_kafka_topic_overlap`, `_shared_dependency_name`) is untouched here —
    a Feign target or a manifest coordinate is already exact-matched
    against one specific name by construction and doesn't need this;
    imports are the one signal source generic enough to plausibly collide.

    Ambiguous matches are downgraded, never dropped — "X matches multiple
    repositories -> ambiguous / weaker evidence," not "-> no evidence":
    the import genuinely happened and is still worth showing, it just
    should never single-handedly identify one specific repository over
    another it matched equally well. `confidence: "ambiguous"` is a new,
    deliberately-named-lower tier alongside the existing "structural"/
    "heuristic" vocabulary (see `capabilities._corroboration_evidence`,
    which excludes it from corroboration entirely).
    """
    match_counts: dict[str, int] = {}
    for edge in edges:
        if edge.type != "IMPORTS_REPOSITORY":
            continue
        for module in edge.properties.get("imports", []):
            match_counts[module] = match_counts.get(module, 0) + 1

    downgraded: list[GraphEdge] = []
    for edge in edges:
        if edge.type == "IMPORTS_REPOSITORY" and any(
            match_counts.get(module, 0) > 1 for module in edge.properties.get("imports", [])
        ):
            downgraded.append(
                GraphEdge(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    type=edge.type,
                    properties={**edge.properties, "confidence": "ambiguous"},
                )
            )
        else:
            downgraded.append(edge)
    return downgraded


async def relink_account(
    *, graph_repository: IGraphRepository, db: AsyncSession, user_id: object
) -> None:
    """The single entry point for recomputing an account's entire
    cross-repository edge set (ADR 0010, invariant I7 — the only code
    outside this module allowed to trigger relinking is `app.indexer.
    services.indexing_service.run_indexing`, and it must call this, never
    the lower-level helpers directly).

    User-scoped by construction: only `Repository` rows owned by `user_id`
    are ever read — the same scoping `GetIndexedRepositoriesTool` already
    enforces for the single-repository path. An edge must never connect two
    different users' repositories.

    Guarded by a transaction-scoped Postgres advisory lock keyed on
    `user_id` (`pg_advisory_xact_lock` — auto-released at the enclosing
    transaction's commit/rollback, safe under SQLAlchemy's pooled
    connections). Deliberately *blocking*, not "try and skip": every caller
    commits its own repository's graph to Neo4j before ever reaching this
    call (see `run_indexing`), so whichever concurrent caller acquires the
    lock *last* is guaranteed to observe every repository committed by any
    other caller already waiting on (or holding) the same lock — the
    account's cross-repository edges always converge to a complete, correct
    state from the existing commit-then-lock ordering alone, with no retry,
    queue, or scheduler needed. A "try, don't block" guard was used here
    originally but could drop a concurrent caller's repository entirely
    when the lock was contended (see the regression test in
    `tests/integration/test_finding3_concurrent_relink_repro.py`).
    """
    lock_key = str(user_id)
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(lock_key))))

    result = await db.execute(select(Repository).where(Repository.user_id == user_id))
    all_repos = list(result.scalars().all())

    indexed_repos: list[Repository] = []
    for repo in all_repos:
        if await graph_repository.has_graph(str(repo.id)):
            indexed_repos.append(repo)

    if not indexed_repos:
        return

    nodes_by_repo = await load_all_repo_nodes(graph_repository, db, indexed_repos)
    edges_by_repo = compute_edges(nodes_by_repo)

    for repo_id, edges in edges_by_repo.items():
        await graph_repository.replace_cross_repository_edges(repo_id, edges)

    # ADR 0018 RFC-05: also persist every cross-repository relationship
    # into Engineering Memory, via the existing, parity-tested
    # Hypothesis -> Validator -> ConfidenceEngine pipeline. Deliberately
    # after the Neo4j write loop above (which this leaves byte-for-byte
    # unchanged) and on its own independent session — never `db`, which is
    # still holding this function's advisory lock — see
    # `cross_repo_memory`'s module docstring for why. Imported here, not at
    # module load time, to avoid a cycle: `cross_repo_memory` imports
    # `app.knowledge_engine.validators.cross_repo`, which itself imports
    # this module at runtime.
    from app.indexer.graph.cross_repo_memory import persist_cross_repo_relationships

    await persist_cross_repo_relationships(nodes_by_repo)

    logger.info(
        "cross_repo_relink_computed user_id=%s repositories=%d edges=%d",
        user_id,
        len(indexed_repos),
        sum(len(e) for e in edges_by_repo.values()),
    )
