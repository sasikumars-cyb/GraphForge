"""Orchestrates Phase 7's deterministic impact analysis: read the pull
request's changed files -> map them to indexed graph nodes -> traverse
Neo4j relationships -> classify risk -> persist the result.

No AI/LLM calls anywhere in this package - every step is a deterministic
lookup or traversal over data `app.indexer` already produced.
"""

import uuid
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.graph.interfaces import IImpactGraphReader
from app.analysis.models.impact import ImpactAnalysisResult, impacted_node_from_graph_node
from app.analysis.services.dependency_path_builder import build_dependency_paths
from app.analysis.services.risk_classifier import classify_risk
from app.core.exceptions import AppError, NotFoundError
from app.graph.health import GraphHealthService, GraphHealthStatus
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphNode
from app.integrations.interfaces import IVersionControlProvider
from app.models.pull_request import PullRequest
from app.models.pull_request_analysis import PullRequestAnalysis
from app.models.repository import Repository
from app.services.github_service import get_decrypted_access_token, list_repository_ids_for_user


class RepositoryNotIndexedError(AppError):
    """Raised when impact analysis is requested for a repository that has
    never been successfully indexed - there's no graph to map changed
    files against yet."""

    status_code = 422
    error_code = "repository_not_indexed"


def _is_pom_file(path: str) -> bool:
    return path == "pom.xml" or path.endswith("/pom.xml")


def _dedupe_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    return list({node.id: node for node in nodes}.values())


class ImpactAnalysisEngine:
    def __init__(
        self,
        db: AsyncSession,
        graph_repository: IGraphRepository,
        impact_graph_reader: IImpactGraphReader,
        version_control_provider: IVersionControlProvider,
    ) -> None:
        self._db = db
        self._graph_repository = graph_repository
        self._impact_graph_reader = impact_graph_reader
        self._version_control_provider = version_control_provider
        self._health_service = GraphHealthService(db, graph_repository)

    async def analyze_pull_request(self, pull_request_id: uuid.UUID) -> PullRequestAnalysis:
        pull_request = await self._db.get(PullRequest, pull_request_id)
        if pull_request is None:
            raise NotFoundError("Pull request not found.")

        repository = await self._db.get(Repository, pull_request.repository_id)
        if repository is None:
            raise NotFoundError("Repository not found.")

        repository_id = str(repository.id)
        health = await self._health_service.for_repository(repository)
        if health.status != GraphHealthStatus.HEALTHY:
            raise RepositoryNotIndexedError(
                "This repository has not been indexed yet - run POST "
                "/repositories/{id}/index before analyzing a pull request."
            )

        access_token = await get_decrypted_access_token(self._db, repository.user_id)
        changed_files = await self._version_control_provider.list_changed_files(
            owner=repository.owner,
            repo=repository.name,
            pull_number=pull_request.number,
            access_token=access_token,
        )

        changed_paths = {changed.path for changed in changed_files}
        changed_paths.update(
            changed.previous_path for changed in changed_files if changed.previous_path
        )
        pom_changed = any(_is_pom_file(path) for path in changed_paths)

        direct_nodes = await self._impact_graph_reader.find_nodes_by_file_paths(
            repository_id, changed_paths
        )
        direct_service_nodes = [node for node in direct_nodes if "Component" in node.labels]
        direct_ids = {node.id for node in direct_nodes}

        api_hops = await self._impact_graph_reader.find_downstream_apis(repository_id, direct_ids)
        topic_hops = await self._impact_graph_reader.find_downstream_topics(
            repository_id, direct_ids
        )
        topic_ids = {hop.to_node.id for hop in topic_hops}
        topic_names = {
            str(hop.to_node.properties["name"])
            for hop in topic_hops
            if hop.to_node.properties.get("name")
        }

        same_repo_peer_hops = (
            await self._impact_graph_reader.find_same_repository_topic_peers(
                repository_id, topic_ids, direct_ids
            )
            if topic_ids
            else []
        )
        # KAN-45: scoped to this same user's other tracked repositories -
        # find_cross_repository_topic_peers matches by topic *name* with
        # no tenant attribution of its own, so passing anything wider here
        # (e.g. the old "exclude just this one id" shape) would surface
        # another tenant's component whenever a topic name collides.
        cross_repo_peer_hops = (
            await self._impact_graph_reader.find_cross_repository_topic_peers(
                topic_names,
                (await list_repository_ids_for_user(self._db, repository.user_id))
                - {repository_id},
            )
            if topic_names
            else []
        )

        dependencies = (
            await self._impact_graph_reader.get_dependencies(repository_id) if pom_changed else []
        )

        # Repository-granularity, not component-granularity (see
        # `find_cross_repository_service_callers`'s docstring) - only
        # worth asking when a Component actually changed, same guard
        # `same_repo_peer_hops`/`cross_repo_peer_hops` apply via `topic_ids`.
        service_caller_hops = (
            await self._impact_graph_reader.find_cross_repository_service_callers(repository_id)
            if direct_service_nodes
            else []
        )

        risk = classify_risk(
            direct_service_nodes, pom_changed=pom_changed, topics_touched=bool(topic_hops)
        )

        indirect_nodes = _dedupe_nodes(
            [hop.from_node for hop in same_repo_peer_hops]
            + [hop.from_node for hop in cross_repo_peer_hops]
            + [hop.from_node for hop in service_caller_hops]
        )

        result = ImpactAnalysisResult(
            risk=risk,
            directly_impacted_services=[
                impacted_node_from_graph_node(node) for node in direct_service_nodes
            ],
            indirectly_impacted_services=[
                impacted_node_from_graph_node(node) for node in indirect_nodes
            ],
            impacted_apis=[
                impacted_node_from_graph_node(node)
                for node in _dedupe_nodes([hop.to_node for hop in api_hops])
            ],
            impacted_topics=[
                impacted_node_from_graph_node(node)
                for node in _dedupe_nodes([hop.to_node for hop in topic_hops])
            ],
            impacted_libraries=[impacted_node_from_graph_node(node) for node in dependencies],
            dependency_paths=build_dependency_paths(
                api_hops,
                topic_hops,
                same_repo_peer_hops,
                cross_repo_peer_hops,
                service_caller_hops,
            ),
        )

        return await self._persist(pull_request.id, result)

    async def _persist(
        self, pull_request_id: uuid.UUID, result: ImpactAnalysisResult
    ) -> PullRequestAnalysis:
        existing = await self._db.execute(
            select(PullRequestAnalysis).where(
                PullRequestAnalysis.pull_request_id == pull_request_id
            )
        )
        analysis = existing.scalar_one_or_none()

        fields: dict[str, object] = {
            "risk": result.risk.value,
            "directly_impacted_services": [asdict(n) for n in result.directly_impacted_services],
            "indirectly_impacted_services": [
                asdict(n) for n in result.indirectly_impacted_services
            ],
            "impacted_apis": [asdict(n) for n in result.impacted_apis],
            "impacted_topics": [asdict(n) for n in result.impacted_topics],
            "impacted_libraries": [asdict(n) for n in result.impacted_libraries],
            "dependency_paths": [asdict(p) for p in result.dependency_paths],
        }

        if analysis is None:
            analysis = PullRequestAnalysis(pull_request_id=pull_request_id, **fields)
            self._db.add(analysis)
        else:
            for field_name, value in fields.items():
                setattr(analysis, field_name, value)

        await self._db.commit()
        await self._db.refresh(analysis)
        return analysis
