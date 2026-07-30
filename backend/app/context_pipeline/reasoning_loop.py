"""The Context Discovery reasoning loop.

Replaces "retrieve everything, then hand it to Planning" with a loop that
gathers evidence through the *existing* providers (Jira/Confluence/GitHub/
Graph — unchanged, still anchor-driven off detected references, not a
free-text search tool), then reasons about what it actually knows:

    gather evidence (ContextResolutionPipeline, unchanged)
        -> update WorkingContext.knowledge
        -> detect BlockingIssues (deterministic: repo not found, repo tie,
           Jira reference unresolved, documentation unavailable — the
           concrete failure modes named in the Context Discovery /
           Context Explorer architecture review, all represented through
           one generic BlockingIssue shape rather than special-cased)
        -> LLM assessment pass (assumptions, any further non-blocking
           issues it perceives)
        -> score capability-specific confidence
        -> evaluate readiness via policy checks (required vs. recommended
           capabilities), not a bare confidence threshold
        -> if BLOCKED: pick the single highest-value blocking issue and
           pause
        -> else: done

Resuming after a user answers a blocking issue's question performs one more
real gather step when the answer identifies a specific repository (a
targeted, re-ranked graph query — genuine additional evidence, not just a
re-assessment of the same data), then re-runs the reasoning pass. This is
the "gather more evidence, reason again, still blocked? ask one more
question" loop, bounded at MAX_CLARIFICATION_ROUNDS.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.agents.llm import invoke_llm_json
from app.agents.planning.tools import rank_repositories
from app.agents.prompt_utils import parse_json_response
from app.context_pipeline.models import EnrichedPlanningRequest, Reference
from app.context_pipeline.pipeline import ContextResolutionPipeline
from app.context_pipeline.providers import GraphProvider
from app.context_pipeline.working_context import (
    BlockingIssue,
    CapabilityCheck,
    CapabilityConfidence,
    ClarificationQuestion,
    Compatibility,
    ContextMetadata,
    DiscoverySummary,
    DiscoverySummaryItem,
    GraphKnowledge,
    Knowledge,
    Reasoning,
    WorkingContext,
)
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

MAX_CLARIFICATION_ROUNDS = 2
# A repository is only flagged "ambiguous" against the leader when both have
# a non-zero score (an all-zero tie means no search terms mattered at all,
# not a genuine ambiguity) and the runner-up is within 10% of the leader.
_REPO_TIE_RATIO = 0.9


class ContextAssessmentLLMError(AppError):
    status_code = 502
    error_code = "context_assessment_llm_error"


_ASSESS_SYSTEM_PROMPT = (
    "You help a Context Discovery stage decide whether it understands an "
    "engineering request well enough to hand off to a Planning stage. You "
    "are given the request and everything retrieved so far (detected "
    "references, resolved Jira/Confluence/GitHub content, indexed "
    "repositories, graph components/topics). Respond ONLY with valid JSON: "
    '{"confidence": number between 0 and 1, "assumptions": [string, ...], '
    '"unresolved_questions": [{"question": string, "why": string, '
    '"options": [string, ...], "blocking": boolean}]}. `confidence` is your '
    "own read on architectural fit — how well the graph's components/topics "
    "actually match this request. `assumptions` are things you inferred "
    "with reasonable confidence and are proceeding on. `unresolved_questions` "
    "are genuine ambiguities that would change how this should be planned — "
    "do not invent one when the request is already concrete and "
    "self-contained; return an empty list in that case. Set `blocking` true "
    "only when the request cannot be reasonably planned without an answer; "
    "otherwise false (still worth surfacing, not worth stopping for)."
)


def _reference_to_dict(ref: Reference) -> dict[str, Any]:
    data = asdict(ref)
    data["type"] = ref.type.value
    return data


def _seed_working_context(enriched: EnrichedPlanningRequest) -> WorkingContext:
    return WorkingContext(
        metadata=ContextMetadata(goal=enriched.original_request, iteration=1),
        knowledge=Knowledge(
            entities=[_reference_to_dict(r) for r in enriched.resolved_references],
            resolved_sources=[
                {
                    "provider": a.provider,
                    "capability": a.capability.value,
                    "title": a.title,
                }
                for a in enriched.artifacts
            ],
            repositories=enriched.indexed_repositories,
            architecture={
                "components": enriched.graph_components,
                "topics": enriched.graph_topics,
            },
            implementation_candidates=enriched.ranked_repository_names,
            graph=GraphKnowledge(
                available=enriched.graph_available,
                has_data=enriched.graph_has_data,
                context_text=enriched.graph_context_text,
            ),
        ),
        reasoning=Reasoning(),
        compatibility=Compatibility(
            original_request=enriched.original_request,
            enriched_text=enriched.enriched_text,
            planning_metadata=enriched.planning_metadata,
        ),
    )


def _detect_blocking_issues(
    wc: WorkingContext, enriched: EnrichedPlanningRequest
) -> list[BlockingIssue]:
    """Deterministic ambiguity checks — no LLM call needed. These are the
    concrete failure modes the architecture review calls out by name,
    every one represented through the same generic `BlockingIssue` shape:
    repository ownership can't be determined, multiple repositories are
    equally likely, a referenced Jira issue can't be resolved, requested
    documentation isn't available."""
    issues: list[BlockingIssue] = []
    entities = wc.knowledge.entities
    repositories = wc.knowledge.repositories
    candidates = wc.knowledge.implementation_candidates

    has_repo_entity = any(e["type"] in ("local_repository", "github_repository") for e in entities)
    if not repositories and has_repo_entity:
        issues.append(
            BlockingIssue(
                issue_id="repo_not_found",
                type="repository_not_found",
                severity="blocking",
                message="No indexed repository matched the request's entities.",
                reason=(
                    "A repository reference was detected in the request, but no "
                    "indexed repository matched it."
                ),
                recommended_action=["Connect repository", "Index repository", "Retry discovery"],
                clarification_question=ClarificationQuestion(
                    question_id="repo_not_found",
                    question=(
                        "I couldn't determine which repository this request refers to. "
                        "How would you like to continue?"
                    ),
                    why=(
                        "A repository reference was detected in the request, but no "
                        "indexed repository matched it."
                    ),
                    options=[
                        "Select a repository",
                        "Connect another repository",
                        "Continue with best-effort planning",
                    ],
                ),
            )
        )

    if len(candidates) >= 2:
        # rank_repositories returns (score, name) pairs, best first — invert
        # to name -> score for the leader/runner-up lookup below.
        scored = {
            name: score
            for score, name in rank_repositories(
                enriched.indexed_repositories,
                enriched.graph_components,
                enriched.profile.search_terms,
            )
        }
        top_score = scored.get(candidates[0], 0.0)
        runner_up_score = scored.get(candidates[1], 0.0)
        if top_score > 0 and runner_up_score >= top_score * _REPO_TIE_RATIO:
            issues.append(
                BlockingIssue(
                    issue_id="repo_ambiguous",
                    type="repository_ambiguous",
                    severity="blocking",
                    message=f"'{candidates[0]}' and '{candidates[1]}' are equally relevant.",
                    reason="Their relevance scores are too close to pick one automatically.",
                    recommended_action=["Select a repository"],
                    clarification_question=ClarificationQuestion(
                        question_id="repo_ambiguous",
                        question=(
                            f"Two repositories appear equally relevant: {candidates[0]} and "
                            f"{candidates[1]}. Which repository should I use?"
                        ),
                        why="Their relevance scores are too close to pick one automatically.",
                        options=[candidates[0], candidates[1]],
                    ),
                )
            )

    jira_ref = next((e for e in entities if e["type"] == "jira_issue"), None)
    jira_resolved = any(s["provider"] == "jira" for s in wc.knowledge.resolved_sources)
    if jira_ref is not None and not jira_resolved:
        issues.append(
            BlockingIssue(
                issue_id="jira_unresolved",
                type="jira_unresolved",
                severity="blocking",
                message=f"Jira reference {jira_ref['normalized_value']} could not be resolved.",
                reason=(
                    "A Jira reference was detected but the ticket couldn't be "
                    "fetched (not found, or Jira isn't connected)."
                ),
                recommended_action=["Connect Jira"],
                clarification_question=ClarificationQuestion(
                    question_id="jira_unresolved",
                    question=(
                        f"I couldn't retrieve {jira_ref['normalized_value']} from Jira. "
                        "How would you like to continue?"
                    ),
                    why=(
                        "A Jira reference was detected but the ticket couldn't be "
                        "fetched (not found, or Jira isn't connected)."
                    ),
                    options=["Connect Jira", "Continue with best-effort planning"],
                ),
            )
        )

    # Documentation is only "expected" when there's a work item to document
    # against — a bare freeform request with no ticket has nothing this
    # check should complain is missing.
    confluence_resolved = any(s["provider"] == "confluence" for s in wc.knowledge.resolved_sources)
    if jira_ref is not None and jira_resolved and not confluence_resolved:
        issues.append(
            BlockingIssue(
                issue_id="documentation_unavailable",
                type="documentation_unavailable",
                severity="warning",
                message="No linked Confluence documentation was found for this ticket.",
                reason="The Jira issue resolved, but no Confluence page was linked or reachable.",
                recommended_action=["Connect Confluence"],
            )
        )

    return issues


async def _assess_context(
    wc: WorkingContext, *, model: str | None, stage: str
) -> tuple[float | None, list[str], list[BlockingIssue]]:
    """LLM reasoning pass — best-effort. Returns (architecture_confidence_
    hint, assumptions, additional_issues). On any failure, returns (None,
    [], []) rather than blocking discovery on the assessment call itself
    (same policy as the Phase 6 discovery.py `recommend_additional_context`
    this extends) — capability scoring falls back to deterministic graph
    state alone in that case."""
    k = wc.knowledge
    try:
        raw = await invoke_llm_json(
            system_prompt=_ASSESS_SYSTEM_PROMPT,
            user_prompt=(
                f"Request: {wc.compatibility.original_request}\n\n"
                f"Entities detected: {k.entities or 'none'}\n"
                f"Resolved sources: {k.resolved_sources or 'none'}\n"
                f"Indexed repositories: {[r.get('name') for r in k.repositories] or 'none'}\n"
                f"Ranked candidates: {k.implementation_candidates or 'none'}\n"
                f"Components found: {len(k.architecture.get('components', []))}\n"
                f"Topics found: {len(k.architecture.get('topics', []))}"
            ),
            stage=stage,
            model=model,
            error_cls=ContextAssessmentLLMError,
        )
        data = parse_json_response(raw, ContextAssessmentLLMError)
    except ContextAssessmentLLMError:
        logger.info("context_discovery_assessment_skipped reason=llm_unavailable")
        return None, [], []

    confidence = data.get("confidence")
    confidence = max(0.0, min(1.0, float(confidence))) if confidence is not None else None
    assumptions = [str(a) for a in (data.get("assumptions") or []) if str(a).strip()]

    issues: list[BlockingIssue] = []
    for q in data.get("unresolved_questions") or []:
        question_text = str(q.get("question", "")).strip()
        if not question_text:
            continue
        is_blocking = bool(q.get("blocking", False))
        question_id = f"llm_{uuid.uuid4().hex[:8]}"
        issues.append(
            BlockingIssue(
                issue_id=question_id,
                type="llm_raised",
                severity="blocking" if is_blocking else "warning",
                message=question_text,
                reason=str(q.get("why", "")).strip(),
                clarification_question=(
                    ClarificationQuestion(
                        question_id=question_id,
                        question=question_text,
                        why=str(q.get("why", "")).strip(),
                        options=[str(o) for o in (q.get("options") or [])],
                    )
                    if is_blocking
                    else None
                ),
            )
        )

    return confidence, assumptions, issues


def _score_capabilities(
    wc: WorkingContext, architecture_hint: float | None
) -> CapabilityConfidence:
    """Deterministic, per-capability confidence — the source of truth
    `_confidence_for` (agent.py) derives its single reported score from,
    rather than the other way around. Each capability reads directly off
    `WorkingContext` state; nothing here is a guess."""
    issue_types = {i.type for i in wc.reasoning.blocking_issues if not i.resolved}
    k = wc.knowledge

    work_item = 0.3 if "jira_unresolved" in issue_types else 1.0

    if "repository_not_found" in issue_types:
        repository = 0.0
    elif "repository_ambiguous" in issue_types:
        repository = 0.35
    elif k.repositories:
        repository = 0.9
    else:
        # No repository signal either way — not applicable, not penalized.
        repository = 0.6

    if architecture_hint is not None:
        graph_score = 0.85 if k.graph.has_data else (0.4 if k.graph.available else 0.2)
        architecture = (graph_score + architecture_hint) / 2
    elif k.graph.has_data:
        architecture = 0.85
    elif k.graph.available:
        architecture = 0.4
    else:
        architecture = 0.2

    if "repository_ambiguous" in issue_types or "repository_not_found" in issue_types:
        implementation_candidates = 0.3
    elif k.implementation_candidates:
        implementation_candidates = min(1.0, 0.5 + 0.1 * len(k.implementation_candidates))
    else:
        implementation_candidates = 0.2

    documentation = 0.3 if "documentation_unavailable" in issue_types else 0.85

    return CapabilityConfidence(
        work_item=work_item,
        repository=repository,
        architecture=architecture,
        implementation_candidates=implementation_candidates,
        documentation=documentation,
    )


def evaluate_readiness_checks(wc: WorkingContext) -> list[CapabilityCheck]:
    """Policy checks readiness is evaluated against — required capabilities
    must all be satisfied for READY; recommended ones are informational
    warnings that never by themselves block READY (see the Discovery
    Summary example: candidates found + Confluence unavailable is still
    READY)."""
    issue_types = {i.type for i in wc.reasoning.blocking_issues if not i.resolved}
    k = wc.knowledge

    return [
        CapabilityCheck(
            capability="work_item",
            label="Work item resolved",
            satisfied="jira_unresolved" not in issue_types,
            severity="required",
        ),
        CapabilityCheck(
            capability="repository",
            label="Repository resolved",
            satisfied=not ({"repository_not_found", "repository_ambiguous"} & issue_types),
            severity="required",
        ),
        CapabilityCheck(
            capability="graph",
            label="Graph available",
            satisfied=k.graph.available,
            severity="required",
        ),
        CapabilityCheck(
            capability="implementation_candidates",
            label="Implementation candidates identified",
            satisfied=bool(k.implementation_candidates),
            severity="recommended",
        ),
        CapabilityCheck(
            capability="documentation",
            label="Documentation available",
            satisfied="documentation_unavailable" not in issue_types,
            severity="recommended",
        ),
    ]


def evaluate_readiness(wc: WorkingContext) -> str:
    """READY/PARTIAL/BLOCKED, derived from policy checks rather than a bare
    confidence threshold. BLOCKED wins outright — any unresolved blocking
    issue means a human decision is needed regardless of what else is
    satisfied. Otherwise: all required checks satisfied -> READY (a failed
    recommended check is a warning, not a downgrade); anything else ->
    PARTIAL."""
    wc.reasoning.checks = evaluate_readiness_checks(wc)
    if wc.reasoning.next_blocking_issue() is not None:
        return "BLOCKED"
    required_failed = [c for c in wc.reasoning.checks if c.severity == "required" and not c.satisfied]
    if not required_failed:
        return "READY"
    return "PARTIAL"


def build_discovery_summary(wc: WorkingContext) -> DiscoverySummary:
    """Human-facing report generated *from* `WorkingContext` — what the
    Workflow UI actually shows, kept separate from the structured object
    downstream agents consume (see this module's docstring, refinement 6)."""
    items: list[DiscoverySummaryItem] = []
    for check in wc.reasoning.checks or evaluate_readiness_checks(wc):
        status = "ok" if check.satisfied else ("error" if check.severity == "required" else "warning")
        items.append(DiscoverySummaryItem(label=check.label, status=status, detail=check.detail))

    for issue in wc.reasoning.blocking_issues:
        if issue.resolved:
            continue
        items.append(
            DiscoverySummaryItem(
                label=issue.message,
                status="error" if issue.severity == "blocking" else "warning",
                detail=issue.reason,
            )
        )

    candidate_count = len(wc.knowledge.implementation_candidates)
    headline = (
        f"{candidate_count} implementation candidate(s) found"
        if candidate_count
        else "No implementation candidates identified yet"
    )

    return DiscoverySummary(items=items, readiness=wc.reasoning.readiness, headline=headline)


def _reassess(wc: WorkingContext) -> None:
    """Recompute `checks`/`readiness` in place — the one place both
    `run_discovery_loop` and `resume_discovery` derive the verdict from
    current knowledge/reasoning state, so they can never disagree on how
    a WorkingContext maps to a readiness value."""
    wc.reasoning.readiness = evaluate_readiness(wc)


class DiscoveryLoopResult:
    """What `run_discovery_loop`/`resume_discovery` return: the resulting
    WorkingContext plus whether it's paused awaiting a user answer."""

    def __init__(self, working_context: WorkingContext, evidence: list[Evidence]) -> None:
        self.working_context = working_context
        self.evidence = evidence

    @property
    def paused(self) -> bool:
        r = self.working_context.reasoning
        return r.readiness == "BLOCKED" and not r.exhausted

    @property
    def pending_question(self) -> ClarificationQuestion | None:
        if self.working_context.reasoning.exhausted:
            return None
        return self.working_context.next_blocking_question()


async def run_discovery_loop(
    *,
    raw_request: str,
    db: AsyncSession,
    graph_repo_override: Any,
    user_id: uuid.UUID | None,
    model: str | None,
    extras: dict[str, Any],
    stage: str,
) -> DiscoveryLoopResult:
    """Fresh discovery: gather evidence once through the existing pipeline,
    then reason about what's known/unknown."""
    enriched = await ContextResolutionPipeline().resolve(
        raw_request=raw_request,
        db=db,
        graph_repo_override=graph_repo_override,
        user_id=user_id,
        model=model,
        extras=extras,
    )
    wc = _seed_working_context(enriched)

    wc.reasoning.blocking_issues = _detect_blocking_issues(wc, enriched)
    architecture_hint, assumptions, llm_issues = await _assess_context(wc, model=model, stage=stage)
    wc.reasoning.assumptions.extend(assumptions)
    wc.reasoning.blocking_issues.extend(llm_issues)
    wc.reasoning.confidence = _score_capabilities(wc, architecture_hint)
    _reassess(wc)

    return DiscoveryLoopResult(working_context=wc, evidence=list(enriched.evidence))


async def _gather_targeted_repository_evidence(
    wc: WorkingContext,
    repo_name: str,
    *,
    db: AsyncSession,
    user_id: uuid.UUID | None,
    graph_repo_override: Any,
) -> Evidence | None:
    """Real additional evidence gathering — a second graph query, re-ranked
    around the repository the user just named, rather than merely
    re-reasoning over what was already fetched. Returns the tool-call
    Evidence for the retry, or None if the tool itself failed (best-effort:
    the resolved answer still counts even if this doesn't pan out)."""
    from app.tools import ToolExecutor, ToolInput, get_tool_registry

    registry = get_tool_registry()
    executor = ToolExecutor(registry=registry)
    tool_input = ToolInput(
        query=repo_name,
        parameters={
            "db": db,
            "user_id": user_id,
            "relevance_terms": [repo_name],
            "graph_repo": graph_repo_override,
        },
    )
    graph_provider = GraphProvider(executor)
    graph_result = await graph_provider.retrieve(tool_input)
    if not graph_result.data:
        return None

    indexed_repos = graph_result.data.get("indexed_repositories", [])
    components = graph_result.data.get("components", [])
    topics = graph_result.data.get("kafka_topics", [])
    ranked = graph_result.data.get("ranked_repositories", [])

    wc.knowledge.repositories = indexed_repos or wc.knowledge.repositories
    wc.knowledge.architecture = {"components": components, "topics": topics}
    wc.knowledge.implementation_candidates = ranked or [repo_name]
    wc.knowledge.graph = GraphKnowledge(
        available=bool(graph_result.success),
        has_data=bool(components or topics),
        context_text=wc.knowledge.graph.context_text,
    )

    return Evidence(
        kind="tool_call",
        reference="neo4j_graph_targeted",
        summary=f"Re-queried the graph scoped to '{repo_name}' after clarification.",
    )


async def resume_discovery(
    *,
    working_context: WorkingContext,
    question_id: str,
    answer: str,
    model: str | None,
    stage: str,
    db: AsyncSession | None = None,
    user_id: uuid.UUID | None = None,
    graph_repo_override: Any = None,
) -> DiscoveryLoopResult:
    """Re-enter the loop after a user answers a blocking issue's question.

    Performs one real additional gather step when the answer names a
    specific, indexed repository (a targeted, re-ranked graph query) —
    "gather more evidence" is an actual second retrieval here, not just a
    re-assessment of the same knowledge. Then reasons again. Bounded at
    MAX_CLARIFICATION_ROUNDS: past that, discovery finishes anyway
    (BLOCKED, no further question) so the user isn't stuck in an endless
    back-and-forth.
    """
    wc = working_context
    resolved_issue = wc.reasoning.resolve_issue(question_id, answer)
    wc.metadata.clarification_rounds += 1
    wc.metadata.iteration += 1
    evidence: list[Evidence] = []

    if (
        resolved_issue is not None
        and resolved_issue.type in ("repository_not_found", "repository_ambiguous")
        and db is not None
    ):
        known_names = {r.get("name") for r in wc.knowledge.repositories}
        candidate_name = answer.strip()
        if candidate_name and (not known_names or candidate_name in known_names or len(known_names) <= 1):
            retry_evidence = await _gather_targeted_repository_evidence(
                wc,
                candidate_name,
                db=db,
                user_id=user_id,
                graph_repo_override=graph_repo_override,
            )
            if retry_evidence is not None:
                evidence.append(retry_evidence)

    if wc.metadata.clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
        # Stop asking — any *other* still-unresolved blocking issue is kept
        # as-is (accurately keeping readiness at BLOCKED), but `exhausted`
        # tells the caller to stop pausing/asking past this round.
        if wc.reasoning.next_blocking_issue() is not None:
            wc.reasoning.readiness = "BLOCKED"
            wc.reasoning.exhausted = True
            return DiscoveryLoopResult(working_context=wc, evidence=evidence)

    architecture_hint, assumptions, llm_issues = await _assess_context(wc, model=model, stage=stage)
    wc.reasoning.assumptions.extend(assumptions)
    wc.reasoning.blocking_issues.extend(llm_issues)
    wc.reasoning.confidence = _score_capabilities(wc, architecture_hint)
    _reassess(wc)

    return DiscoveryLoopResult(working_context=wc, evidence=evidence)
