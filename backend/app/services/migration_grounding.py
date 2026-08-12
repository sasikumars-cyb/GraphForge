"""Migration Assistant's dependency-aware grounding.

Parses a migration question into (source technology, target technology),
then answers "what will be affected" from the real Knowledge Graph rather
than asking an LLM to guess:

1. **Direct** — repositories whose Engineering Memory relationships
   actually mention the source technology, found via the same
   token-overlap matching `ask_grounding.resolve_repository` already uses
   for repository names, applied here to relationship entity text.
2. **Indirect** — everything reachable from a direct repository within a
   bounded blast radius (`compute_blast_radius`, the same deterministic
   service the Impact lens and `ask_grounding.ground_impact` already
   call), minus what's already direct.

No new graph-query code: this module is pure composition of
`dependency_query_service.search` and `compute_blast_radius`. Both are
called with exactly the repository ids the caller already scoped to the
requesting user — see `ConversationService`'s own call site — so Migration
Assistant carries no wider a permission surface than Ask GraphForge's own
impact/dependency grounding already has.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.text_relevance import relevance, term_weights, tokenize
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.repository import Repository
from app.schemas.migration import MigrationRisk, MigrationScope
from app.services.ask_grounding import display_names, pretty_entity_name
from app.services.engineering_intelligence import dependency_query_service
from app.services.engineering_intelligence.contracts import EntityReference
from app.services.engineering_intelligence.impact_analysis_service import compute_blast_radius

_MAX_HOPS = 2
# A direct repository whose OWN blast radius touches at least this many
# other repositories is flagged as a "highly connected" migration risk —
# real fan-out from the graph, never an LLM guessing a component "sounds"
# risky. Matches the same bucket boundary `ask_grounding._severity` uses
# for its own medium/high split, for the same reason: consistency in what
# "several" means across GraphForge's risk language.
_HIGH_FANOUT_THRESHOLD = 3
_UUID_PREFIX_RE = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}):"
)

# Ordered so the most specific phrasing wins: "from X to Y" is checked
# before the bare "migrate/move/upgrade ... to ..." pattern, so "migrate
# the customer ingestion database from PostgreSQL to BigQuery" extracts
# the technology pair (PostgreSQL, BigQuery), not the whole clause
# preceding "from".
# A trailing "." only terminates the target when it's a real sentence
# end (followed by whitespace or end-of-string) — otherwise a version
# number's decimal point ("Python 3.12") would truncate the match.
_END = r"(?:[?!]|\.(?=\s|$)|$)"

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        rf"from\s+(?P<source>[\w][\w .+/-]*?)\s+to\s+(?P<target>[\w][\w .+/-]*?){_END}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"replace\s+(?:the\s+|our\s+)?(?P<source>[\w][\w .+/-]*?)\s+with\s+"
        rf"(?P<target>[\w][\w .+/-]*?){_END}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:migrat\w*|mov\w*|upgrad\w*)\w*\s+(?:our\s+|the\s+)?"
        rf"(?P<source>[\w][\w .+/-]*?)\s+to\s+(?P<target>[\w][\w .+/-]*?){_END}",
        re.IGNORECASE,
    ),
]


def parse_migration_intent(text: str) -> tuple[str, str] | None:
    """(source, target) technology names, or None if the question doesn't
    name both — callers must ask a clarifying question rather than guess
    when this returns None, never fabricate a migration scope."""
    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            source = match.group("source").strip(" .,")
            target = match.group("target").strip(" .,")
            if source and target:
                return source, target
    return None


async def _user_repositories(db: AsyncSession, user_id: uuid.UUID) -> list[Repository]:
    result = await db.execute(select(Repository).where(Repository.user_id == user_id))
    return list(result.scalars().all())


def _repo_id_from_entity(entity: str) -> uuid.UUID | None:
    match = _UUID_PREFIX_RE.match(entity)
    if not match:
        return None
    try:
        return uuid.UUID(match.group(1))
    except ValueError:
        return None


async def _direct_repository_ids(
    db: AsyncSession, repositories: list[Repository], technology: str
) -> set[uuid.UUID]:
    """Every repository id whose Engineering Memory relationships mention
    `technology`. Two matchers, either is sufficient:

    - a case-insensitive substring match on the technology name as
      written ("postgresql" in "...:database:postgresql" — the common
      case, since dependency/entity names are conventionally lowercased
      single tokens already);
    - token overlap (`ask_grounding.resolve_repository`'s own matching),
      which catches a multi-word technology description ("Spark
      workloads") a bare substring check would never find verbatim.

    Substring-only would miss "Spark workloads" entirely; token-overlap
    alone would miss "PostgreSQL" — `tokenize`'s camelCase splitter reads
    it as two words ("postgre", "sql"), neither of which equals the
    single lowercase token "postgresql" real entity names use. Combining
    both is what makes the demo's actual phrasing work, not a
    hypothetical one shaped to fit a single matcher."""
    if not repositories:
        return set()

    technology_lower = technology.strip().lower()
    terms = list(tokenize(technology))

    result = await dependency_query_service.search(db, [r.id for r in repositories])
    texts = [f"{i.source_entity} {i.target_entity}" for i in result.relationships]
    weights = term_weights(terms, texts) if terms else {}

    direct: set[uuid.UUID] = set()
    for insight, text in zip(result.relationships, texts, strict=True):
        substring_hit = bool(technology_lower) and technology_lower in text.lower()
        token_hit = bool(terms) and relevance(text, terms, weights) > 0
        if not (substring_hit or token_hit):
            continue
        for entity in (insight.source_entity, insight.target_entity):
            repo_id = _repo_id_from_entity(entity)
            if repo_id is not None:
                direct.add(repo_id)
    return direct


async def ground_migration(
    db: AsyncSession, user_id: uuid.UUID, source_technology: str, target_technology: str
) -> MigrationScope | None:
    """The real migration scope for this user's indexed repositories, or
    `None` if nothing in the graph mentions the source technology at all
    — callers must say so honestly rather than presenting an empty
    `MigrationScope` as "nothing is affected."."""
    repositories = await _user_repositories(db, user_id)
    repositories_by_id = {r.id: r for r in repositories}

    direct_ids = await _direct_repository_ids(db, repositories, source_technology)
    if not direct_ids:
        return None

    graph_repository = Neo4jGraphRepository(get_driver())
    all_names: dict[str, str] = {}
    indirect_ids: set[uuid.UUID] = set()
    fanout_by_repo: dict[uuid.UUID, int] = {}

    for repo_id in direct_ids:
        blast_radius = await compute_blast_radius(
            db,
            graph_repository,
            EntityReference(repository_id=str(repo_id), node_id=f"{repo_id}:repository"),
            max_hops=_MAX_HOPS,
        )
        all_names.update(await display_names(db, blast_radius))
        fanout_by_repo[repo_id] = len(blast_radius.impacted_repositories)
        for node_id in blast_radius.impacted_repositories:
            impacted_id = _repo_id_from_entity(node_id)
            if impacted_id is not None and impacted_id not in direct_ids:
                indirect_ids.add(impacted_id)

    def display(repo_id: uuid.UUID) -> str:
        owned = repositories_by_id.get(repo_id)
        if owned is not None:
            return owned.full_name
        return pretty_entity_name(f"{repo_id}:repository", all_names)

    risks: list[MigrationRisk] = []
    for repo_id, fanout in sorted(fanout_by_repo.items(), key=lambda item: -item[1]):
        if fanout >= _HIGH_FANOUT_THRESHOLD:
            risks.append(
                MigrationRisk(
                    label=f"{display(repo_id)} is highly connected",
                    reason=(
                        f"{fanout} downstream relationship(s) detected within a "
                        f"{_MAX_HOPS}-hop blast radius."
                    ),
                    provenance="derived",
                )
            )
    if len(indirect_ids) >= _HIGH_FANOUT_THRESHOLD:
        risks.append(
            MigrationRisk(
                label=f"{len(indirect_ids)} indirect systems in scope",
                reason=(
                    f"{len(indirect_ids)} additional repositories are reachable within "
                    f"{_MAX_HOPS} hops of the direct migration scope."
                ),
                provenance="derived",
            )
        )

    direct_ids_sorted = sorted(direct_ids)
    return MigrationScope(
        source_technology=source_technology,
        target_technology=target_technology,
        direct=sorted({display(i) for i in direct_ids}),
        indirect=sorted({display(i) for i in indirect_ids}),
        risks=risks,
        primary_repository_id=str(direct_ids_sorted[0]) if direct_ids_sorted else None,
    )
