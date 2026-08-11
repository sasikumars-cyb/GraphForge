"""ADR 0010 §7 P0 (Theme B) regression tests — retiring `implementation_
candidates` as an independent capability and folding its remaining signal
into `repository`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.context_pipeline.reasoning.capabilities import BY_KEY, CAPABILITIES
from app.context_pipeline.reasoning.engine import discover
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigators import (
    GraphInvestigator,
    RequestParseInvestigator,
)
from app.context_pipeline.reasoning.ledger import Ledger
from app.context_pipeline.reasoning.projection import build_result
from app.tools.interfaces import ToolResult


def _session() -> SessionContext:
    return SessionContext(db=None, user_id=None)  # type: ignore[arg-type]


def _graph_tool_result(repositories: list[str], components: list[tuple[str, str]]) -> ToolResult:
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": n} for n in repositories],
            "components": [{"name": c, "repository": r, "type": "service"} for c, r in components],
            "kafka_topics": [],
            "context_text": "x",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "x",
            "_traverse_summary": "x",
        },
        summary="q",
    )


def test_implementation_candidates_is_no_longer_a_registered_capability() -> None:
    assert "implementation_candidates" not in BY_KEY
    assert "implementation_candidates" not in {c.key for c in CAPABILITIES}


def test_repository_capability_owns_four_signals_including_the_folded_one() -> None:
    repository = BY_KEY["repository"]
    signal_labels = {s.label for s in repository.signals(Ledger())}
    assert "Candidate implementation sites found" in signal_labels
    assert len(signal_labels) == 4


@pytest.mark.asyncio
async def test_two_explicit_repositories_score_full_confidence_with_no_unsatisfied_signal() -> None:
    """The exact scenario the original review found broken: two repositories
    explicitly named together must never show a ✗ signal anywhere, and
    `capability_confidence` must no longer carry an `implementation_
    candidates` key at all."""
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["ingestion-framework", "etl-core"],
                [("SchemaMerger", "ingestion-framework"), ("DeltaWriter", "etl-core")],
            )
        ),
    ):
        state = await discover(
            request="Enable Delta Lake mergeSchema. Repo: ingestion-framework, etl-core",
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    result = build_result(state)
    assert "implementation_candidates" not in result["capability_confidence"]

    repository_assessment = state.assessment_for("repository")
    assert repository_assessment is not None
    assert repository_assessment.score == 1.0
    assert all(
        s.satisfied for s in repository_assessment.signals
    ), repository_assessment.explanation()
