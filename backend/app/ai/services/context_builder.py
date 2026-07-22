"""Assembles bounded context for AI analysis prompts.

Collects deterministic analysis results, dependency paths, relevant source
file paths, and repository metadata into a single context dict suitable for
prompt rendering.  Never sends the complete repository — only files that
the deterministic engine identified as relevant.

Makes **no** provider/LLM calls.  Does **not** modify the deterministic
analysis, the graph, or the indexer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.analysis.models.impact import (
    DependencyPath,
    ImpactAnalysisResult,
    ImpactedNode,
)

# -- Max context size guard (characters) ------------------------------------
_DEFAULT_MAX_CONTEXT_CHARS = 120_000


@dataclass(frozen=True)
class AIContext:
    """Bounded context payload passed to prompt rendering and ultimately to
    an LLM provider.  Kept as a plain dataclass so it serialises trivially.
    """

    repository_name: str
    repository_owner: str
    default_branch: str
    pull_request_title: str
    pull_request_number: int
    head_ref: str
    base_ref: str
    risk: str
    directly_impacted_services: list[dict[str, str]] = field(default_factory=list)
    indirectly_impacted_services: list[dict[str, str]] = field(default_factory=list)
    impacted_apis: list[dict[str, str]] = field(default_factory=list)
    impacted_topics: list[dict[str, str]] = field(default_factory=list)
    impacted_libraries: list[dict[str, str]] = field(default_factory=list)
    dependency_paths: list[list[dict[str, str]]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    jira_issues: list[dict[str, str]] = field(default_factory=list)

    def to_prompt_variables(self) -> dict[str, str]:
        """Convert this context into the variable dict expected by
        :class:`~app.ai.services.prompt_builder.PromptBuilder`.

        Maps dataclass fields to the ``{{ placeholder }}`` names used in
        the prompt templates.  All values are stringified for direct
        template substitution.
        """
        impacted_components = (
            self.directly_impacted_services
            + self.indirectly_impacted_services
            + self.impacted_apis
            + self.impacted_topics
            + self.impacted_libraries
        )
        deterministic_analysis = {
            "risk": self.risk,
            "directly_impacted_services": self.directly_impacted_services,
            "indirectly_impacted_services": self.indirectly_impacted_services,
            "impacted_apis": self.impacted_apis,
            "impacted_topics": self.impacted_topics,
            "impacted_libraries": self.impacted_libraries,
            "dependency_paths": self.dependency_paths,
        }
        return {
            "repository": f"{self.repository_owner}/{self.repository_name}",
            "pull_request_title": self.pull_request_title,
            "deterministic_analysis": json.dumps(deterministic_analysis, indent=2),
            "changed_files": "\n".join(self.changed_files),
            "impacted_components": json.dumps(impacted_components, indent=2),
            "dependency_paths": json.dumps(self.dependency_paths, indent=2),
        }


def _impacted_node_to_dict(node: ImpactedNode) -> dict[str, str]:
    return {
        "id": node.id,
        "name": node.name,
        "node_type": node.node_type,
    }


def _dependency_path_to_list(path: DependencyPath) -> list[dict[str, str]]:
    return [
        {
            "node_id": step.node_id,
            "node_name": step.node_name,
            "node_type": step.node_type,
            "relationship": step.relationship or "",
        }
        for step in path.steps
    ]


class ContextBuilder:
    """Assembles an :class:`AIContext` from deterministic analysis outputs.

    Consumes data produced by the ``ImpactAnalysisEngine`` and the
    repository/PR models.  Never queries the graph or GitHub directly —
    callers pass pre-fetched data in.

    Usage::

        ctx = (
            ContextBuilder(max_context_chars=100_000)
            .with_repository(name="svc", owner="acme", default_branch="main")
            .with_pull_request(title="Fix order flow", number=42, head="fix/order", base="main")
            .with_analysis(result)
            .with_changed_files(["src/orders.py", "src/payments.py"])
            .build()
        )
    """

    def __init__(self, max_context_chars: int = _DEFAULT_MAX_CONTEXT_CHARS) -> None:
        self._max_context_chars = max_context_chars
        self._repository_name: str = ""
        self._repository_owner: str = ""
        self._default_branch: str = ""
        self._pr_title: str = ""
        self._pr_number: int = 0
        self._head_ref: str = ""
        self._base_ref: str = ""
        self._risk: str = ""
        self._directly_impacted: list[ImpactedNode] = []
        self._indirectly_impacted: list[ImpactedNode] = []
        self._impacted_apis: list[ImpactedNode] = []
        self._impacted_topics: list[ImpactedNode] = []
        self._impacted_libraries: list[ImpactedNode] = []
        self._dependency_paths: list[DependencyPath] = []
        self._changed_files: list[str] = []
        self._jira_issues: list[dict[str, str]] = []
        self._from_persisted: bool = False
        self._persisted_directly: list[dict[str, str]] = []
        self._persisted_indirectly: list[dict[str, str]] = []
        self._persisted_apis: list[dict[str, str]] = []
        self._persisted_topics: list[dict[str, str]] = []
        self._persisted_libraries: list[dict[str, str]] = []
        self._persisted_paths: list[list[dict[str, str]]] = []

    def with_repository(
        self,
        *,
        name: str,
        owner: str,
        default_branch: str,
    ) -> ContextBuilder:
        """Add repository metadata."""
        self._repository_name = name
        self._repository_owner = owner
        self._default_branch = default_branch
        return self

    def with_pull_request(
        self,
        *,
        title: str,
        number: int,
        head_ref: str,
        base_ref: str,
    ) -> ContextBuilder:
        """Add pull request metadata."""
        self._pr_title = title
        self._pr_number = number
        self._head_ref = head_ref
        self._base_ref = base_ref
        return self

    def with_analysis(self, result: ImpactAnalysisResult) -> ContextBuilder:
        """Add deterministic impact analysis results."""
        self._risk = result.risk.value
        self._directly_impacted = list(result.directly_impacted_services)
        self._indirectly_impacted = list(result.indirectly_impacted_services)
        self._impacted_apis = list(result.impacted_apis)
        self._impacted_topics = list(result.impacted_topics)
        self._impacted_libraries = list(result.impacted_libraries)
        self._dependency_paths = list(result.dependency_paths)
        return self

    def with_analysis_from_persisted(self, analysis: object) -> ContextBuilder:
        """Add analysis data from a persisted ``PullRequestAnalysis`` row.

        Accepts any object with the same attribute names as the SQLAlchemy
        model (risk, directly_impacted_services, etc.) where the values are
        already serialised as dicts/lists (JSON columns).
        """
        self._risk = getattr(analysis, "risk", "")
        self._persisted_directly = getattr(analysis, "directly_impacted_services", [])
        self._persisted_indirectly = getattr(analysis, "indirectly_impacted_services", [])
        self._persisted_apis = getattr(analysis, "impacted_apis", [])
        self._persisted_topics = getattr(analysis, "impacted_topics", [])
        self._persisted_libraries = getattr(analysis, "impacted_libraries", [])
        self._persisted_paths = getattr(analysis, "dependency_paths", [])
        self._from_persisted = True
        return self

    def with_changed_files(self, file_paths: list[str]) -> ContextBuilder:
        """Add the list of changed file paths (relevant files only)."""
        self._changed_files = list(file_paths)
        return self

    def with_jira_issues(self, issues: list[dict[str, str]]) -> ContextBuilder:
        """Add linked Jira issues (placeholder for future integration)."""
        self._jira_issues = list(issues)
        return self

    def build(self) -> AIContext:
        """Assemble the final bounded context, truncating if needed."""
        changed = self._truncate_file_list(self._changed_files)

        if self._from_persisted:
            return AIContext(
                repository_name=self._repository_name,
                repository_owner=self._repository_owner,
                default_branch=self._default_branch,
                pull_request_title=self._pr_title,
                pull_request_number=self._pr_number,
                head_ref=self._head_ref,
                base_ref=self._base_ref,
                risk=self._risk,
                directly_impacted_services=self._persisted_directly,
                indirectly_impacted_services=self._persisted_indirectly,
                impacted_apis=self._persisted_apis,
                impacted_topics=self._persisted_topics,
                impacted_libraries=self._persisted_libraries,
                dependency_paths=self._persisted_paths,
                changed_files=changed,
                jira_issues=self._jira_issues,
            )

        return AIContext(
            repository_name=self._repository_name,
            repository_owner=self._repository_owner,
            default_branch=self._default_branch,
            pull_request_title=self._pr_title,
            pull_request_number=self._pr_number,
            head_ref=self._head_ref,
            base_ref=self._base_ref,
            risk=self._risk,
            directly_impacted_services=[_impacted_node_to_dict(n) for n in self._directly_impacted],
            indirectly_impacted_services=[
                _impacted_node_to_dict(n) for n in self._indirectly_impacted
            ],
            impacted_apis=[_impacted_node_to_dict(n) for n in self._impacted_apis],
            impacted_topics=[_impacted_node_to_dict(n) for n in self._impacted_topics],
            impacted_libraries=[_impacted_node_to_dict(n) for n in self._impacted_libraries],
            dependency_paths=[_dependency_path_to_list(p) for p in self._dependency_paths],
            changed_files=changed,
            jira_issues=self._jira_issues,
        )

    def _truncate_file_list(self, files: list[str]) -> list[str]:
        """Keep only as many file paths as fit within the char budget."""
        budget = self._max_context_chars
        result: list[str] = []
        used = 0
        for path in files:
            cost = len(path) + 2  # account for separator overhead
            if used + cost > budget:
                break
            result.append(path)
            used += cost
        return result
