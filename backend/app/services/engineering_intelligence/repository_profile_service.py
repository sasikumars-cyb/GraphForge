"""`RepositoryProfileService` — the graph-shaped, evidence-shaped summary
of one repository. Reuses `IGraphRepository.get_full_graph` for structure
(`Endpoint`/`DataTable`/`KafkaTopic`/`FeignClient`/`MavenDependency`/
`PythonDependency` node labels — `app.indexer.graph.builder` is the only
writer of that vocabulary, confirmed by audit) and `evidence_curation
.curate_for_prompt` for narrative evidence. No LLM calls here — narrative
synthesis stays in the calling agent (approved design's explicit
constraint).

Note on scope: the approved design named "feature flags" and "cloud
services" as profile categories. The graph's actual node-label vocabulary
(`app.indexer.graph.builder.build_graph`) has no such labels — there is
nothing to populate those fields from without fabricating data. This
service therefore reports only the categories the graph actually models
(apis, databases, queues, integrations, dependencies); it does not carry
permanently-empty placeholder fields.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.knowledge_engine.evidence_curation import curate_for_prompt
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.services.engineering_intelligence.contracts import RepositoryProfile

_API_LABEL = "Endpoint"
_DATABASE_LABEL = "DataTable"
_QUEUE_LABEL = "KafkaTopic"
_INTEGRATION_LABEL = "FeignClient"
_DEPENDENCY_LABELS = ("MavenDependency", "PythonDependency")


def _endpoint_summary(properties: dict[str, object]) -> str:
    method = properties.get("http_method", "")
    path = properties.get("path", "")
    return f"{method} {path}".strip()


def _dependency_summary(properties: dict[str, object]) -> str:
    if "artifact_id" in properties:
        return f"{properties.get('group_id', '')}:{properties['artifact_id']}"
    return str(properties.get("name", ""))


async def get_profile(
    db: AsyncSession, graph_repository: IGraphRepository, repository_id: uuid.UUID
) -> RepositoryProfile:
    """Empty categories (rather than an error) when the repository has
    never been indexed — same "nothing to report yet" contract
    `get_full_graph` and `get_current_relationships` already use."""
    payload = await graph_repository.get_full_graph(str(repository_id))

    apis: list[str] = []
    databases: list[str] = []
    queues: list[str] = []
    integrations: list[str] = []
    dependencies: list[str] = []

    for node in payload.nodes:
        labels = set(node.labels)
        if _API_LABEL in labels:
            apis.append(_endpoint_summary(node.properties))
        if _DATABASE_LABEL in labels:
            databases.append(str(node.properties.get("name", node.id)))
        if _QUEUE_LABEL in labels:
            queues.append(str(node.properties.get("name", node.id)))
        if _INTEGRATION_LABEL in labels:
            integrations.append(str(node.properties.get("name", node.id)))
        if labels.intersection(_DEPENDENCY_LABELS):
            dependencies.append(_dependency_summary(node.properties))

    memory = EngineeringMemoryService(db)
    packs = await memory.list_evidence_packs(repository_id, limit=1)
    narrative_lines: list[str] = []
    if packs:
        pack = await memory.retrieve_evidence_pack(packs[0].pack_id)
        if pack is not None:
            curated = curate_for_prompt(pack)
            narrative_lines = [
                item.raw_value for item in curated.items if item.kind.startswith("repository_")
            ]

    architecture_summary = (
        f"{len(apis)} API(s), {len(databases)} database table(s), {len(queues)} queue(s), "
        f"{len(integrations)} outbound integration(s), {len(dependencies)} dependency(ies)."
    )
    if narrative_lines:
        architecture_summary = " ".join(narrative_lines[:3]) + " " + architecture_summary

    return RepositoryProfile(
        repository_id=str(repository_id),
        apis=tuple(sorted(set(apis))),
        databases=tuple(sorted(set(databases))),
        queues=tuple(sorted(set(queues))),
        integrations=tuple(sorted(set(integrations))),
        dependencies=tuple(sorted(set(dependencies))),
        architecture_summary=architecture_summary,
    )
