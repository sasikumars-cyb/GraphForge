"""ContextBuilder — merges and compresses tool results into LLM-ready context.

The Planning Agent collects one ToolResult per tool it ran, then calls
ContextBuilder.build(). The builder:
  - Filters failed results (optionally logging their errors)
  - Deduplicates evidence that appears in multiple tools
  - Produces a single markdown-formatted context_text under a token budget
  - Returns a PlanningContext ready to be injected into the LLM prompt
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from app.tools.interfaces import ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TOKEN_BUDGET = 12_000
_CHARS_PER_TOKEN = 4


@dataclass
class PlanningContext:
    """LLM-ready context assembled from all tool results.

    Attributes:
        context_text:    Markdown block ready for insertion into the LLM prompt.
        evidence_items:  Deduplicated list of short strings for the UI audit trail.
        tool_ids_used:   Tool IDs that contributed to this context.
        total_tokens:    Estimated token count of context_text.
        truncated:       True if one or more tool results were dropped to fit budget.
    """

    context_text: str = ""
    evidence_items: list[str] = field(default_factory=list)
    tool_ids_used: list[str] = field(default_factory=list)
    total_tokens: int = 0
    truncated: bool = False


class ContextBuilder:
    """Assembles a PlanningContext from a sequence of ToolResults.

    Usage:
        builder = ContextBuilder(token_budget=12_000)
        ctx = builder.build(results)
        prompt = f"{base_prompt}\n\n{ctx.context_text}"
    """

    def __init__(self, token_budget: int = _DEFAULT_TOKEN_BUDGET) -> None:
        self._budget = token_budget

    def build(self, results: Sequence[ToolResult]) -> PlanningContext:
        """Merge tool results into a single PlanningContext."""
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        for r in failed:
            logger.info(
                "context_builder_skipped_failed tool=%s error=%s", r.tool_id, r.error
            )

        if not successful:
            return PlanningContext(
                context_text="No tool context available.",
                tool_ids_used=[r.tool_id for r in failed],
            )

        sections: list[str] = []
        evidence: list[str] = []
        seen_evidence: set[str] = set()
        tool_ids_used: list[str] = []
        tokens_used = 0
        truncated = False

        for result in successful:
            context_text_piece = result.data.get("context_text", result.summary or "")
            if not context_text_piece:
                continue

            section = f"### {result.tool_name}\n{context_text_piece.strip()}"
            section_tokens = len(section) // _CHARS_PER_TOKEN

            if tokens_used + section_tokens > self._budget:
                truncated = True
                logger.warning(
                    "context_builder_truncated tool=%s budget_remaining=%d section_tokens=%d",
                    result.tool_id,
                    self._budget - tokens_used,
                    section_tokens,
                )
                break

            sections.append(section)
            tokens_used += section_tokens
            tool_ids_used.append(result.tool_id)

            for item in result.evidence_items:
                norm = item.strip()
                if norm and norm not in seen_evidence:
                    seen_evidence.add(norm)
                    evidence.append(norm)

        context_text = "\n\n".join(sections) if sections else "No tool context available."

        return PlanningContext(
            context_text=context_text,
            evidence_items=evidence,
            tool_ids_used=tool_ids_used,
            total_tokens=tokens_used,
            truncated=truncated,
        )
