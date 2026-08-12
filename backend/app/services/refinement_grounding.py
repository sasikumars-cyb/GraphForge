"""Refinement Planner's grounding — resolves what a requirement actually
is (a real Jira issue, a pasted requirement, or a Confluence reference
GraphForge can't yet fetch standalone) and computes the deterministic
parts of a refinement plan (critical path, parallelizable work, "what if
X is delayed" impact) from the dependency edges already in the plan.

What's real here vs. what stays the LLM's job
------------------------------------------------
Fetching a Jira issue reuses the exact same `ToolExecutor.execute("jira",
...)` call `app.context_pipeline.providers.JiraProvider` already makes —
same transport, same auth, same tenant trust boundary (see that module's
own docstring). Nothing here adds a new way to reach Jira or a new
permission surface.

Confluence is honestly NOT resolvable from a bare URL/reference today:
`app.agents.planning.confluence_context`'s own docstring establishes that
Atlassian's MCP server has no free-text search, only graph traversal
anchored on a known entity (a Jira issue key) — Confluence enrichment
already only ever ran "when a Jira issue was resolved" (see
`ConfluenceProvider.resolve_for_issue`). A user pasting a bare Confluence
URL with no Jira anchor is therefore a real capability gap, not something
to fake — `resolve_requirement_source` returns `"confluence_unsupported"`
for that case, and the caller must say so rather than inventing a
retrieved page.

Everything past "what is the requirement text" (decomposition into
epics/stories/tasks/spikes, dependency edges between them, open
questions) is the LLM's job — this module supplies it real input, never
pretends to compute a plan itself. The one exception is the graph math
below: once edges exist, critical path / parallelizable / "what if this
slips" are pure graph algorithms, computed here so they're `derived`, not
another LLM guess.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.context_pipeline.reference_detection import CONFLUENCE_URL_RE
from app.schemas.refinement import OpenQuestion, RefinementReadiness, WorkItem, WorkItemEdge
from app.services.ask_grounding import resolve_repository
from app.services.engineering_intelligence import dependency_query_service
from app.tools import ToolExecutor, ToolInput
from app.tools.implementations.jira_tool import extract_issue_key
from app.tools.registry import get_tool_registry


#  `CONFLUENCE_URL_RE` (shared with the rest of `reference_detection`)
# only matches an actual Confluence URL. A user who names the page in
# prose instead of pasting its link — "refine the Confluence page
# 'Notification RFC'" — has no URL for that regex to catch, so without
# this the message fell through to the generic freetext path: the LLM
# was handed a sentence with no real requirement content and, doing its
# best with nothing, invented a "capture and parse the doc" spike that
# then read as a normal, confidently-scored plan. Short-word match, not
# anchored to a URL — deliberately broad, because the honest "I can't
# fetch this" response is the safe failure mode and a real pasted
# requirement is exceedingly unlikely to name "Confluence" by product
# name rather than just containing its content.
_CONFLUENCE_MENTION_RE = re.compile(r"\bconfluence\b", re.IGNORECASE)


@dataclass(frozen=True)
class RequirementFetch:
    source: str  # "jira" | "freetext" | "confluence_unsupported"
    text: str
    jira_key: str | None = None
    jira_url: str | None = None
    unresolved_note: str = ""


async def resolve_requirement(db: AsyncSession, message: str) -> RequirementFetch:
    """What the requirement actually is, fetched where GraphForge
    genuinely can fetch it. Never guesses at Jira/Confluence content it
    didn't retrieve."""
    issue_key = extract_issue_key(message)
    if issue_key:
        executor = ToolExecutor(registry=get_tool_registry())
        result = await executor.execute("jira", ToolInput(query=message))
        if result.success:
            return RequirementFetch(
                source="jira",
                text=str(result.data.get("context_text", "")),
                jira_key=issue_key,
                jira_url=str(result.data.get("url", "")) or None,
            )
        return RequirementFetch(
            source="freetext",
            text=message,
            unresolved_note=(
                f"Couldn't fetch Jira issue {issue_key} ({result.error or 'unknown error'}) — "
                "treating the message as the requirement text itself."
            ),
        )

    if CONFLUENCE_URL_RE.search(message) or _CONFLUENCE_MENTION_RE.search(message):
        return RequirementFetch(
            source="confluence_unsupported",
            text="",
            unresolved_note=(
                "I can refine Jira issues and pasted requirements here. This Confluence "
                "reference isn't currently accessible through the connected knowledge "
                "sources — paste the requirement text itself, or reference a Jira issue "
                "that links to this page, and I'll work from that."
            ),
        )

    return RequirementFetch(source="freetext", text=message)


async def ground_engineering_context(
    db: AsyncSession, user_id: uuid.UUID, requirement_text: str
) -> tuple[str | None, str | None, int]:
    """Best-effort: the one existing repository the requirement most
    plausibly touches (by the same token-overlap matching
    `ask_grounding.resolve_repository` already applies elsewhere), plus
    how many tracked relationships it has — real Engineering Memory data,
    not invented "engineering context". Returns
    (repository_id, repository_name, relationship_count); repository_id
    is None when nothing matches confidently, and the caller must not
    claim engineering context was found in that case."""
    repository = await resolve_repository(db, user_id, requirement_text)
    if repository is None:
        return None, None, 0
    result = await dependency_query_service.search(db, [repository.id])
    return str(repository.id), repository.full_name, result.total_matched


def _precedes_adjacency(
    edges: list[WorkItemEdge],
) -> dict[str, set[str]]:
    """u -> v means "u must happen before v" — `blocks(source, target)`
    already reads that way; `depends_on(source, target)` (source needs
    target's output) reads the other direction, so it's flipped here.
    Only `_PRECEDENCE_RELATIONSHIPS` contribute — `enables`/`related`/
    `parent_child` carry no ordering constraint."""
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if edge.relationship == "blocks":
            before, after = edge.source_id, edge.target_id
        elif edge.relationship == "depends_on":
            before, after = edge.target_id, edge.source_id
        else:
            continue
        adjacency.setdefault(before, set()).add(after)
        adjacency.setdefault(after, set())
    return adjacency


def compute_downstream_impact(edges: list[WorkItemEdge], item_id: str) -> list[str]:
    """Every work item that becomes at-risk if `item_id` slips — a BFS
    over the precedence graph, not an LLM guess. Empty when `item_id`
    blocks/precedes nothing."""
    adjacency = _precedes_adjacency(edges)
    seen: set[str] = set()
    queue = list(adjacency.get(item_id, set()))
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(adjacency.get(current, set()) - seen)
    return sorted(seen)


_MAX_CRITICAL_PATHS = 5  # defensive cap — refinement plans are small (a handful of items)


def compute_critical_paths(
    work_items: list[WorkItem], edges: list[WorkItemEdge]
) -> list[list[str]]:
    """The longest chain(s) of precedence through the plan — standard DAG
    longest-path DP, computed once per turn. Returns every maximal chain
    when more than one ties for longest, including ties introduced by a
    single node fanning out to more than one equally-long continuation
    (the brief's "say *paths*, not a single critical path, when several
    are equally long" case); empty when there's no precedence chain at
    all (nothing blocks/depends on anything — every item is independent)."""
    adjacency = _precedes_adjacency(edges)
    all_ids = {item.id for item in work_items}
    for node in list(adjacency):
        all_ids.add(node)
    if not adjacency or all(not targets for targets in adjacency.values()):
        return []

    memo: dict[str, list[list[str]]] = {}

    def longest_from(node: str, visiting: frozenset[str]) -> list[list[str]]:
        """Every path starting at `node` tied for longest from here on."""
        if node in memo:
            return memo[node]
        if node in visiting:
            return [[node]]  # a cycle in LLM-proposed edges — stop, don't loop forever
        best: list[list[str]] = [[node]]
        best_len = 1
        for nxt in adjacency.get(node, ()):
            for continuation in longest_from(nxt, visiting | {node}):
                candidate = [node, *continuation]
                if len(candidate) > best_len:
                    best, best_len = [candidate], len(candidate)
                elif len(candidate) == best_len:
                    best.append(candidate)
        memo[node] = best
        return best

    all_candidates = [path for node in sorted(all_ids) for path in longest_from(node, frozenset())]
    max_len = max((len(p) for p in all_candidates), default=0)
    if max_len <= 1:
        return []
    # De-duplicated (a path can be reached as both "the longest from its
    # own start" and a suffix of a longer traversal that turned out tied),
    # order-preserving, capped defensively.
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for path in all_candidates:
        if len(path) != max_len:
            continue
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
        if len(result) >= _MAX_CRITICAL_PATHS:
            break
    return result


def compute_parallelizable(work_items: list[WorkItem], edges: list[WorkItemEdge]) -> list[str]:
    """Items with no precedence relationship at all — no incoming or
    outgoing `blocks`/`depends_on` edge. Matches the brief's own
    definition verbatim; genuinely independent work, safe to start in
    parallel."""
    adjacency = _precedes_adjacency(edges)
    involved: set[str] = set()
    for before, afters in adjacency.items():
        if afters:
            involved.add(before)
        involved.update(afters)
    return sorted(item.id for item in work_items if item.id not in involved)


def compute_readiness(
    *,
    objective: str,
    work_items: list[WorkItem],
    engineering_context_grounded: bool,
    open_questions: list[OpenQuestion],
) -> RefinementReadiness:
    """A deterministic 0-100 score from four equally-weighted, genuinely
    checkable completeness criteria — never an LLM-invented percentage
    (the brief's own "do not invent arbitrary AI percentages" rule). Each
    criterion is binary/countable, not a judgment call:

    - objective stated (25)
    - at least one work item proposed (25)
    - engineering context actually grounded — a repository resolved or
      relationships found, not just claimed (25)
    - unresolved "unknown" open questions: full 25 at zero, half at 1-2,
      zero beyond that — the only criterion that's a count rather than a
      boolean, because one open unknown is a normal, expected part of
      refinement and three or more genuinely isn't "mostly ready"."""
    score = 0
    ready_signals: list[str] = []
    needs_clarification: list[str] = []
    investigation_required: list[str] = []

    if objective.strip():
        score += 25
        ready_signals.append("Objective identified")
    else:
        needs_clarification.append("Objective / problem statement")

    if work_items:
        score += 25
        ready_signals.append(f"{len(work_items)} work item(s) proposed")
    else:
        needs_clarification.append("Work breakdown")

    if engineering_context_grounded:
        score += 25
        ready_signals.append("Engineering context grounded in the dependency graph")
    else:
        needs_clarification.append("Engineering context (no matching repository found)")

    unknowns = [q for q in open_questions if q.category == "unknown"]
    if not unknowns:
        score += 25
    elif len(unknowns) <= 2:
        score += 12
        investigation_required.extend(q.question for q in unknowns)
    else:
        investigation_required.extend(q.question for q in unknowns)

    if score >= 80:
        level = "ready"
    elif score >= 50:
        level = "mostly_ready"
    elif score >= 25:
        level = "needs_clarification"
    else:
        level = "not_ready"

    return RefinementReadiness(
        level=level,
        score=score,
        ready_signals=ready_signals,
        needs_clarification=needs_clarification,
        investigation_required=investigation_required,
    )
