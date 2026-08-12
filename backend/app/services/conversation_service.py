"""`ConversationService` — the Home page's conversational investigation
loop, and Migration Assistant's own conversational investigation loop.

What this deliberately is, and isn't
-------------------------------------
Not built on `EngineeringSession`/RFC-001 (Belief/Hypothesis/Decision,
propose-then-commit, Participants) — that aggregate exists in this
codebase (`app.services.session_service` and friends) but no agent reads
from it today; adopting its shape here would mean bending a conversation
turn into Beliefs and Hypotheses for a lifecycle nothing else recognizes,
for a heavier persistence model than a chat turn needs.

Not a new reasoning engine either. Every fact a turn can ground on comes
from `app.services.ask_grounding.ground()` (general mode) or
`app.services.migration_grounding.ground_migration()` (migration mode) —
both deterministic, both reused unmodified by `POST /ask`/Migration
Assistant respectively. What's new here is strictly the conversational
layer on top: a single stage-aware LLM call
(`app.agents.llm.StageAwareLLMProvider`, the same primitive every other
agent already uses to talk to a model) that reasons over the
*investigation state* — the accumulated, recomputed-not-persisted summary
of what's been grounded and discussed so far — plus the new message, and
returns a short conversational answer instead of a fresh independent
report.

One conversation, two modes
----------------------------
`Conversation.mode` ("general" | "migration") is the only thing that
differs — same tables, same recompute-from-history state model, same
entity-ref (A/B/C) mechanism, same degraded-fallback discipline. Migration
Assistant is not a second agent framework: it's this same loop, grounded
by a different deterministic service and prompted with a different system
prompt. See `_respond_general`/`_respond_migration`.

The state model
----------------
`_build_investigation_state` recomputes the running state from the
message history on every turn — the same "recompute, don't accumulate"
discipline `UnderstandingService.get_working_understanding` already
applies to `WorkingUnderstanding` (see that module's docstring). There is
therefore exactly one place a follow-up's context can come from
(`ConversationMessage.payload`, already persisted), and no separate
cache to keep in sync with it.

Grounding vs. reasoning, each turn
-----------------------------------
1. The first message in a conversation is always freshly grounded — a
   conversation has to start from a real fact, not a guess. In migration
   mode, if the message doesn't even name a source/target technology
   pair, the turn is a deterministic clarifying question instead (no LLM
   call needed to know the input was incomplete).
2. A later message is freshly grounded again only if it names a *new*
   subject the state doesn't already have (a new repository in general
   mode; a different source technology in migration mode) — topic
   expansion, not a follow-up on the current one.
3. Every other message is a pure follow-up: no new deterministic call,
   the LLM reasons only over the already-gathered `investigation_state`
   plus recent conversation history. This is what makes "what if we
   migrate Reporting first?" resolve without the user repeating which
   repositories are in scope — they're already in the state the prompt
   hands the model. That reasoning is judgment over real facts
   (`ai_insight`), never a live recomputation of the graph — see the
   system prompts' own "Rules" sections.

If the LLM call itself fails (misconfigured provider, malformed
response), the turn falls back to the deterministic facts alone
(`degraded=True`) — never to a fabricated conversational answer.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import STAGE_ASK, StageAwareLLMProvider
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.core.exceptions import AppError, NotFoundError
from app.models.conversation import Conversation, ConversationMessage
from app.models.user import User
from app.schemas.ask import AskAction, AskEvidenceItem, AskImpact, AskResponse
from app.schemas.conversation import ConversationEntityRef, ConversationTurnPayload
from app.schemas.migration import MigrationScope
from app.schemas.refinement import (
    OpenQuestion,
    RefinementPlan,
    Spike,
    WorkItem,
    WorkItemEdge,
)
from app.services.ask_grounding import classify, ground
from app.services.migration_grounding import ground_migration, parse_migration_intent
from app.services.refinement_grounding import (
    compute_critical_paths,
    compute_downstream_impact,
    compute_parallelizable,
    compute_readiness,
    ground_engineering_context,
    resolve_requirement,
)

logger = logging.getLogger(__name__)

_TITLE_MAX_LEN = 120
_HISTORY_TURNS = 8  # user+assistant messages, most recent first when trimmed
_REF_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_GENERAL_SYSTEM_PROMPT = """You are GraphForge's engineering investigation assistant, embedded \
in the product's Home page. An engineer is investigating the consequences of a change across \
their real, indexed engineering systems. You are given, as JSON:

- investigation_state: what has already been established this conversation — a resolved \
repository (if any), named entities with short reference letters (A, B, C, ...), the most \
recent impact assessment, and the most recent conclusion reached.
- new_graph_facts: fresh, real data just computed from the Knowledge Graph for THIS turn, or \
null if this turn didn't need a new graph query.
- conversation_history: the recent back-and-forth, oldest first.
- new_message: what the user just asked.

Rules:
- Use ONLY the facts supplied above. Never invent a repository, service, relationship, or \
number that isn't present in investigation_state or new_graph_facts.
- Resolve references like "it", "that dependency", "those repositories", "A and B", "the \
remaining one" against investigation_state's entities and resolved_repository — do not ask the \
user to repeat an identifier that's already in investigation_state.
- If the user asks something the supplied facts don't directly cover (e.g. "what should I \
test?"), reason qualitatively from the structural facts you do have and say so — do not \
present a guess as a certainty.
- Keep "answer" conversational and short (1-4 sentences) — this is a chat turn, not a report.
- "entities" should list every distinct system your answer names, each with the SAME name \
already used in investigation_state when it's the same system (so the caller can match it back \
to its existing reference letter) — do not invent a new name for an existing entity.
- Respond with ONLY a JSON object, no prose outside it, matching exactly:
{"answer": "...", "why": "...", \
"entities": [{"name": "...", "impact_level": "low"|"medium"|"high"|null}], \
"grounded_in_new_facts": true|false}
"""

_MIGRATION_SYSTEM_PROMPT = """You are GraphForge's Migration Assistant, embedded in the AI \
Workspace. An engineer is planning a technology migration (e.g. PostgreSQL to BigQuery, Spark \
to Databricks, Python 3.9 to 3.12) and reasoning about it against GraphForge's real dependency \
graph. You are given, as JSON:

- investigation_state.migration: what's already established — source_technology, \
target_technology, direct (repositories whose dependency graph actually references the source \
technology), indirect (repositories reached through those within a bounded blast radius), and \
risks (derived findings, each already evidenced). All three lists are real, already-computed \
facts, never yours to invent.
- investigation_state.entities: named systems with reference letters (A, B, C, ...) and any \
impact level already assigned to them.
- investigation_state.last_conclusion: the most recent thing you told the user.
- new_graph_facts: a freshly (re)computed migration scope for THIS turn (same shape as \
investigation_state.migration), or null if this turn reasons over the existing scope instead.
- conversation_history: the recent back-and-forth, oldest first.
- new_message: what the user just asked.

Rules:
- Use ONLY the direct/indirect repositories and risk findings supplied — never invent one, and \
never state a count that isn't in the data.
- A constraint the user states ("what if we migrate Reporting first", "what if Validation stays \
on Postgres temporarily") changes how you REASON about the existing scope, not the underlying \
graph facts themselves — say plainly which named systems remain exposed under the stated \
constraint and which don't, referencing direct/indirect by name.
- Resolve "it", "that dependency", "the remaining one", "A and B" against investigation_state's \
entities — do not ask the user to repeat a name already there.
- When asked what to test, or for a migration plan, ground the answer in the actual \
direct/indirect repositories you were given — name them; do not produce a generic checklist \
that could apply to any migration.
- Keep "answer" conversational and short (1-4 sentences) — this is a chat turn, not a report.
- "entities" should list every distinct system your answer names — reuse the SAME name \
already used in investigation_state.migration/entities when it's the same system, and assign \
impact_level "high" to systems you consider highest-risk, "medium"/"low" otherwise.
- Respond with ONLY a JSON object, no prose outside it, matching exactly:
{"answer": "...", "why": "...", \
"entities": [{"name": "...", "impact_level": "low"|"medium"|"high"|null}]}
"""

_REFINEMENT_SYSTEM_PROMPT = """You are GraphForge's Refinement Planner, embedded in the AI \
Workspace. An engineer is turning a requirement (a Jira issue, a pasted requirement, or a \
plain description) into a refinement-ready engineering plan, grounded in GraphForge's real \
engineering knowledge rather than a generic checklist. You are given, as JSON:

- investigation_state.refinement: the CURRENT plan, if one exists yet — requirement \
understanding (objective, scope, functional/non-functional requirements, constraints, \
assumptions), the proposed work_items (epics/stories/tasks/spikes, each with an id — a real \
Jira key like "PROT-5263" if it already exists, or "PROPOSED-01"/"PROPOSED-02"/... if you're \
proposing it), edges between them (blocks/depends_on/enables/related/parent_child), \
open_questions, and missing_work_categories. On a follow-up turn, MODIFY this plan: reuse the \
exact same ids for anything that isn't changing, keep the PROPOSED-NN numbering going for \
genuinely new items, remove items the user asks to remove, and return the WHOLE updated plan, \
not a diff.
- new_graph_facts: on the first turn, the real requirement text just fetched (Jira issue \
content, or the user's own pasted text) plus any engineering context found (a matching \
repository and its relationship count) — ground the plan in this, don't invent requirements \
it doesn't state. On a later turn, a deterministic "if this item slips, these are downstream" \
list when the user asked a "what if X is delayed/removed" question — reuse that list, don't \
recompute it yourself.
- conversation_history: the recent back-and-forth, oldest first.
- new_message: what the user just asked.

Rules:
- Extract requirement understanding from what's ACTUALLY stated. Never invent scope, \
constraints, or acceptance criteria the requirement doesn't mention — an unstated concern \
(e.g. backward compatibility) belongs in open_questions as "unknown", not in \
functional_requirements.
- Every story needs: title, objective, context, scope, concrete testable acceptance_criteria \
(never vague phrases like "works as expected"), related_systems, risks, and evidence_note \
(where this came from / why you propose it).
- A spike is for GENUINE uncertainty GraphForge cannot resolve from the given context — not \
because something is merely difficult. Only propose one when you can state real questions it \
would answer and exit criteria.
- If a proposed story bundles multiple independent outcomes, split it — never leave an \
oversized story as one item.
- Only surface missing_work_categories actually relevant to this requirement's own content \
and discovered context — never pad with every category on principle.
- Never assign story points. If asked about sizing, say sizing should be completed by the \
delivery team during refinement.
- Classify every open question as "known" (stated in the requirement), "derived" (calculated \
from available information), "assumption" (plausible but unconfirmed), or "unknown" (needs \
clarification/investigation) — never silently convert an assumption into a stated requirement.
- Resolve references like "the second story", "that spike", "A and B" against the CURRENT \
plan's own work_items — do not ask the user to repeat a title you already proposed.
- Keep "answer" conversational and short (1-4 sentences) — this is a chat turn, not a report; \
detail belongs in the structured plan fields, not repeated in prose.
- Respond with ONLY a JSON object, no prose outside it, matching exactly:
{"answer": "...", "requirement_summary": "...", "objective": "...", "desired_outcome": "...", \
"scope": ["..."], "out_of_scope": ["..."], "functional_requirements": ["..."], \
"non_functional_requirements": ["..."], "constraints": ["..."], "assumptions": ["..."], \
"missing_work_categories": ["..."], "work_items": [{"id": "...", \
"type": "epic"|"story"|"task"|"spike", "status": "existing"|"proposed", "title": "...", \
"objective": "...", "context": "...", "scope": "...", "acceptance_criteria": ["..."], \
"related_systems": ["..."], "risks": ["..."], "evidence_note": "..."}], \
"edges": [{"source_id": "...", "target_id": "...", \
"relationship": "blocks"|"depends_on"|"enables"|"related"|"parent_child"}], \
"spikes": [{"work_item_id": "...", "why": "...", "questions": ["..."], \
"exit_criteria": "..."}], "open_questions": [{"question": "...", \
"category": "known"|"derived"|"assumption"|"unknown", "note": "..."}]}
"""


@dataclass
class InvestigationState:
    resolved_repository_id: str | None = None
    resolved_repository_name: str | None = None
    # ref letter -> {"name": ..., "impact_level": ...}
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_impact: dict[str, Any] | None = None
    last_conclusion: str = ""
    # Migration mode only — the last-grounded `MigrationScope`, as a plain
    # dict (mirrors how `last_impact` is carried: read off the prior
    # assistant payload, never recomputed here).
    migration: dict[str, Any] | None = None
    # Refinement mode only — the current `RefinementPlan`, as a plain
    # dict. Replaced wholesale each refinement turn (see
    # `_respond_refinement`), never diffed — the LLM is handed the whole
    # current plan and returns the whole updated one.
    refinement: dict[str, Any] | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "resolved_repository_name": self.resolved_repository_name,
            "entities": [
                {"ref": ref, "name": e["name"], "impact_level": e.get("impact_level")}
                for ref, e in self.entities.items()
            ],
            "last_impact": self.last_impact,
            "last_conclusion": self.last_conclusion,
            "migration": self.migration,
            "refinement": self.refinement,
        }

    def name_to_ref(self) -> dict[str, str]:
        return {e["name"].strip().lower(): ref for ref, e in self.entities.items()}

    def next_ref(self) -> str:
        used = set(self.entities)
        for letter in _REF_LETTERS:
            if letter not in used:
                return letter
        return f"E{len(self.entities) + 1}"  # exhausted the alphabet — degrade gracefully

    def add_entities(self, names: list[str]) -> None:
        name_to_ref = self.name_to_ref()
        for name in names:
            key = name.strip().lower()
            if key not in name_to_ref:
                ref = self.next_ref()
                name_to_ref[key] = ref
                self.entities[ref] = {"name": name}


def _build_investigation_state(messages: list[ConversationMessage]) -> InvestigationState:
    state = InvestigationState()
    for message in messages:
        if message.role != "assistant" or not message.payload:
            continue
        payload = message.payload
        if payload.get("resolved_repository_id"):
            state.resolved_repository_id = payload["resolved_repository_id"]
            state.resolved_repository_name = payload.get("resolved_repository_name")
        for entity in payload.get("entities") or []:
            state.entities[entity["ref"]] = {
                "name": entity["name"],
                "impact_level": entity.get("impact_level"),
            }
        if payload.get("impact"):
            state.last_impact = payload["impact"]
        if payload.get("migration"):
            state.migration = payload["migration"]
        if payload.get("refinement"):
            state.refinement = payload["refinement"]
        state.last_conclusion = message.content
    return state


def _should_reground(state: InvestigationState, message: str) -> bool:
    """A later message triggers a fresh deterministic grounding only when
    it names an impact/dependency question about a repository the state
    doesn't already have — a topic expansion, not a follow-up on the
    current one. Everything else (pronouns, "A and B", "what should I
    test") reasons over the existing state instead."""
    # repository resolution (possibly a no-op if unresolved) decides the rest
    return classify(message) != "general"


def _assign_refs(
    state: InvestigationState, llm_entities: list[dict[str, Any]]
) -> list[ConversationEntityRef]:
    name_to_ref = state.name_to_ref()
    refs: list[ConversationEntityRef] = []
    for raw in llm_entities:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        ref = name_to_ref.get(key)
        if ref is None:
            ref = state.next_ref()
            name_to_ref[key] = ref
            state.entities[ref] = {"name": name}  # reserve it for this turn's own dedup
        impact_level = raw.get("impact_level")
        if impact_level not in ("low", "medium", "high"):
            impact_level = None
        refs.append(ConversationEntityRef(ref=ref, name=name, impact_level=impact_level))
    return refs


def _grounding_entities(grounded: AskResponse) -> list[dict[str, Any]]:
    """Seed entities straight from a fresh grounding's own impact list —
    real, derived data — so the LLM has something concrete to attach
    reference letters to even before it says anything."""
    if grounded.impact is None:
        return []
    names = [
        *grounded.impact.affected_repositories,
        *grounded.impact.affected_apis,
        *grounded.impact.affected_databases,
        *grounded.impact.affected_queues,
    ]
    return [{"name": n} for n in dict.fromkeys(names)]  # de-duplicated, order preserved


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return without_open.rsplit("```", 1)[0].strip()


# -- Refinement plan parsing — defensive: a malformed entry from the LLM's
# own JSON is dropped, never allowed to crash the turn or half-populate a
# plan the user can't trust. --------------------------------------------

_VALID_WORK_ITEM_TYPES = {"epic", "story", "task", "spike"}
_VALID_STATUSES = {"existing", "proposed"}
_VALID_RELATIONSHIPS = {"blocks", "depends_on", "enables", "related", "parent_child"}
_VALID_QUESTION_CATEGORIES = {"known", "derived", "assumption", "unknown"}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, str | int | float) and str(v).strip()]


def _parse_work_items(raw: Any, known_jira_key: str | None) -> list[WorkItem]:
    items: list[WorkItem] = []
    seen_ids: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id") or "").strip()
        item_type = entry.get("type")
        title = str(entry.get("title") or "").strip()
        if not item_id or item_type not in _VALID_WORK_ITEM_TYPES or not title:
            continue
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        # Never let the LLM claim an item is "existing" unless it's the
        # one real Jira key GraphForge actually fetched this
        # conversation — anything else being marked "existing" would
        # misrepresent a proposal as already-created work.
        status = "existing" if known_jira_key and item_id == known_jira_key else "proposed"
        provenance = "fact" if status == "existing" else "recommendation"

        items.append(
            WorkItem(
                id=item_id,
                type=item_type,
                status=status,
                title=title,
                objective=str(entry.get("objective") or ""),
                context=str(entry.get("context") or ""),
                scope=str(entry.get("scope") or ""),
                acceptance_criteria=_string_list(entry.get("acceptance_criteria")),
                related_systems=_string_list(entry.get("related_systems")),
                risks=_string_list(entry.get("risks")),
                evidence_note=str(entry.get("evidence_note") or ""),
                provenance=provenance,
            )
        )
    return items


def _parse_edges(raw: Any, valid_ids: set[str]) -> list[WorkItemEdge]:
    edges: list[WorkItemEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        source_id = str(entry.get("source_id") or "").strip()
        target_id = str(entry.get("target_id") or "").strip()
        relationship = entry.get("relationship")
        if relationship not in _VALID_RELATIONSHIPS:
            continue
        if source_id not in valid_ids or target_id not in valid_ids or source_id == target_id:
            continue
        key = (source_id, target_id, relationship)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            WorkItemEdge(source_id=source_id, target_id=target_id, relationship=relationship)
        )
    return edges


def _parse_spikes(raw: Any, valid_ids: set[str]) -> list[Spike]:
    spikes: list[Spike] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        work_item_id = str(entry.get("work_item_id") or "").strip()
        if work_item_id not in valid_ids:
            continue
        spikes.append(
            Spike(
                work_item_id=work_item_id,
                why=str(entry.get("why") or ""),
                questions=_string_list(entry.get("questions")),
                exit_criteria=str(entry.get("exit_criteria") or ""),
            )
        )
    return spikes


def _parse_open_questions(raw: Any) -> list[OpenQuestion]:
    questions: list[OpenQuestion] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        question = str(entry.get("question") or "").strip()
        category = entry.get("category")
        if not question or category not in _VALID_QUESTION_CATEGORIES:
            continue
        questions.append(
            OpenQuestion(question=question, category=category, note=str(entry.get("note") or ""))
        )
    return questions


def _find_mentioned_work_item(message: str, items: list[dict[str, Any]]) -> str | None:
    """The one existing work item (by id, then by title) the message
    plausibly refers to — used only to decide whether to run a real
    downstream-impact BFS before this turn's LLM call, never to resolve
    the reference itself (the LLM does that, against the full plan it's
    handed either way)."""
    lowered = message.lower()
    for item in items:
        item_id = str(item.get("id", ""))
        if item_id and item_id.lower() in lowered:
            return item_id
    for item in items:
        title = str(item.get("title", ""))
        if len(title) >= 6 and title.lower() in lowered:
            return str(item.get("id") or "") or None
    return None


def _refinement_evidence(
    new_graph_facts: dict[str, Any] | None, plan: RefinementPlan
) -> list[AskEvidenceItem]:
    evidence: list[AskEvidenceItem] = []
    facts = new_graph_facts or {}
    if facts.get("jira_key"):
        evidence.append(
            AskEvidenceItem(
                source="Jira", label=f"Fetched {facts['jira_key']}", provenance="fact"
            )
        )
    engineering_context = facts.get("engineering_context")
    if engineering_context:
        evidence.append(
            AskEvidenceItem(
                source="Dependency Graph",
                label=(
                    f"{engineering_context['relationship_count']} relationship(s) tracked for "
                    f"{engineering_context['repository_name']}"
                ),
                provenance="derived",
            )
        )
    if plan.work_items:
        evidence.append(
            AskEvidenceItem(
                source="Refinement Analysis",
                label=f"{len(plan.work_items)} work item(s), {len(plan.edges)} dependency edge(s)",
                provenance="derived",
            )
        )
    return evidence


def _refinement_why(plan: RefinementPlan) -> str:
    if plan.requirement_summary:
        return plan.requirement_summary
    if plan.readiness:
        return (
            f"Readiness: {plan.readiness.level.replace('_', ' ')} "
            f"({plan.readiness.score}%)."
        )
    return ""


def _migration_actions(scope: dict[str, Any], message_text: str) -> list[AskAction]:
    """Deep-links out of the conversation — "one GraphForge investigation,
    multiple views," never a hand-off to a disconnected agent. Built from
    whatever migration scope is currently known (freshly grounded this
    turn, or carried forward from state), so a pure follow-up turn still
    offers the same gateways a freshly-grounded one does."""
    repo_id = scope.get("primary_repository_id")
    if not repo_id:
        return []
    source = scope.get("source_technology", "")
    target = scope.get("target_technology", "")
    return [
        AskAction(
            label="Explore impact",
            kind="explore_impact",
            href=f"/workspace/impact-analysis?repository={repo_id}",
        ),
        AskAction(
            label="View dependency graph", kind="view_dependency_graph", href="/architecture"
        ),
        AskAction(
            label="Create migration plan",
            kind="create_migration_plan",
            href=f"/workspace/planning?prefill={quote(message_text)}",
        ),
        AskAction(
            label="Validate migration",
            kind="validate_migration",
            href=(
                "/workspace/testing?prefill="
                f"{quote(f'Validation strategy for migrating {source} to {target}')}"
            ),
        ),
    ]


def _refinement_actions(
    plan: dict[str, Any], conversation_id: uuid.UUID, message_text: str
) -> list[AskAction]:
    """Deep-links out of the refinement conversation — same "one
    investigation, multiple views" contract `_migration_actions` already
    established. `view_work_graph` is refinement's own addition: the
    interactive dependency map lives at its own route, keyed by this
    conversation, so it always reads whichever plan is currently in
    state rather than a frozen snapshot."""
    if not plan.get("work_items"):
        return []
    testing_prefill = f"Test strategy for: {plan.get('objective') or message_text}"
    actions = [
        AskAction(
            label="Show dependencies",
            kind="view_work_graph",
            href=f"/workspace/refinement-planner/graph/{conversation_id}",
        ),
        AskAction(
            label="Create planning workflow",
            kind="create_planning_workflow",
            href=f"/workspace/planning?prefill={quote(message_text)}",
        ),
        AskAction(
            label="Generate testing strategy",
            kind="generate_testing_strategy",
            href=f"/workspace/testing?prefill={quote(testing_prefill)}",
        ),
    ]
    jira_key = plan.get("source_jira_key")
    jira_url = plan.get("source_jira_url")
    if jira_key and jira_url:
        actions.append(
            AskAction(label=f"View {jira_key} in Jira", kind="view_jira_issue", href=jira_url)
        )
    return actions


class ConversationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _get_owned(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
        result = await self._db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation

    async def _messages(self, conversation_id: uuid.UUID) -> list[ConversationMessage]:
        result = await self._db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at)
        )
        return list(result.scalars().all())

    async def get_conversation(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[Conversation, list[ConversationMessage]]:
        conversation = await self._get_owned(conversation_id, user_id)
        return conversation, await self._messages(conversation_id)

    async def list_recent(
        self, user_id: uuid.UUID, *, limit: int = 5, mode: str | None = None
    ) -> list[Conversation]:
        """Most-recently-active first (`updated_at`, bumped by every new
        message — see `Conversation.updated_at`'s `onupdate`), not
        creation order — a conversation resumed yesterday should surface
        above one started and abandoned last week. `mode` scopes the
        history icon to its own surface — Migration Assistant's history
        shouldn't list Ask GraphForge's general investigations, and vice
        versa, even though both live in the same table."""
        query = select(Conversation).where(Conversation.user_id == user_id)
        if mode is not None:
            query = query.where(Conversation.mode == mode)
        result = await self._db.execute(
            query.order_by(Conversation.updated_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def start(
        self, user: User, question: str, *, mode: str = "general"
    ) -> tuple[Conversation, ConversationMessage]:
        conversation = Conversation(title=question[:_TITLE_MAX_LEN], user_id=user.id, mode=mode)
        self._db.add(conversation)
        await self._db.flush()

        user_message = ConversationMessage(
            conversation_id=conversation.id, role="user", content=question
        )
        self._db.add(user_message)
        await self._db.flush()

        assistant_message = await self._respond(
            conversation, user_id=user.id, message_text=question, prior=[]
        )
        await self._db.commit()
        return conversation, assistant_message

    async def post_message(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, text: str
    ) -> ConversationMessage:
        conversation = await self._get_owned(conversation_id, user_id)
        prior = await self._messages(conversation_id)

        user_message = ConversationMessage(
            conversation_id=conversation.id, role="user", content=text
        )
        self._db.add(user_message)
        await self._db.flush()

        assistant_message = await self._respond(
            conversation, user_id=user_id, message_text=text, prior=prior
        )
        await self._db.commit()
        return assistant_message

    async def _respond(
        self,
        conversation: Conversation,
        *,
        user_id: uuid.UUID,
        message_text: str,
        prior: list[ConversationMessage],
    ) -> ConversationMessage:
        state = _build_investigation_state(prior)
        if conversation.mode == "migration":
            content, payload = await self._respond_migration(
                user_id=user_id, message_text=message_text, prior=prior, state=state
            )
        elif conversation.mode == "refinement":
            content, payload = await self._respond_refinement(
                user_id=user_id,
                message_text=message_text,
                prior=prior,
                state=state,
                conversation_id=conversation.id,
            )
        else:
            content, payload = await self._respond_general(
                user_id=user_id, message_text=message_text, prior=prior, state=state
            )
        return await self._save_turn(conversation, message_text, content, payload)

    async def _save_turn(
        self,
        conversation: Conversation,
        message_text: str,
        content: str,
        payload: ConversationTurnPayload,
    ) -> ConversationMessage:
        assistant_message = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=content,
            payload=payload.model_dump(),
        )
        self._db.add(assistant_message)
        conversation.title = conversation.title or message_text[:_TITLE_MAX_LEN]
        # Explicit, not left to `onupdate` alone: when `title` was already
        # set, the assignment above is a same-value no-op that SQLAlchemy's
        # change tracking won't consider dirty, so no UPDATE — and no
        # `onupdate` trigger — would otherwise fire for a conversation that
        # already had a title (i.e. every `post_message` after the first).
        # `list_recent`'s "most recently active first" ordering depends on
        # this actually moving on every turn, not just at creation.
        conversation.updated_at = datetime.now(UTC)
        await self._db.flush()
        return assistant_message

    # -- general mode --------------------------------------------------

    async def _respond_general(
        self,
        *,
        user_id: uuid.UUID,
        message_text: str,
        prior: list[ConversationMessage],
        state: InvestigationState,
    ) -> tuple[str, ConversationTurnPayload]:
        grounded: AskResponse | None = None
        if not prior or _should_reground(state, message_text):
            candidate = await ground(self._db, user_id, message_text)
            if candidate.status == "answered" and (
                not prior or candidate.resolved_repository_id != state.resolved_repository_id
            ):
                grounded = candidate

        if grounded is not None:
            state.add_entities([e["name"] for e in _grounding_entities(grounded)])

        return await self._synthesize_general(
            state=state,
            grounded=grounded,
            history=prior[-_HISTORY_TURNS:],
            message_text=message_text,
        )

    async def _synthesize_general(
        self,
        *,
        state: InvestigationState,
        grounded: AskResponse | None,
        history: list[ConversationMessage],
        message_text: str,
    ) -> tuple[str, ConversationTurnPayload]:
        user_prompt = json.dumps(
            {
                "investigation_state": state.to_prompt_dict(),
                "new_graph_facts": grounded.model_dump() if grounded else None,
                "conversation_history": [{"role": m.role, "content": m.content} for m in history],
                "new_message": message_text,
            }
        )

        try:
            provider = StageAwareLLMProvider(stage=STAGE_ASK)
            response = await provider.complete(
                system_prompt=_GENERAL_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                options=LLMRequestOptions(response_format=ResponseFormat.JSON),
            )
            parsed = json.loads(_strip_json_fence(response.text))
            answer = str(parsed.get("answer") or "").strip()
            if not answer:
                raise ValueError("empty answer")
        except (AppError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            logger.warning("conversation_synthesis_failed error=%s", exc)
            return self._degraded_general_turn(state, grounded)

        why = str(parsed.get("why") or "")
        entity_refs = _assign_refs(state, parsed.get("entities") or [])

        evidence: list[AskEvidenceItem] = grounded.evidence if grounded else []
        impact: AskImpact | None = grounded.impact if grounded else None
        actions: list[AskAction] = list(grounded.actions) if grounded else []
        if not actions and state.resolved_repository_id:
            actions = [
                AskAction(
                    label="View repository",
                    kind="view_repository",
                    href=f"/repositories/{state.resolved_repository_id}",
                )
            ]

        payload = ConversationTurnPayload(
            intent=grounded.intent if grounded else "reasoning",
            resolved_repository_id=grounded.resolved_repository_id
            if grounded
            else state.resolved_repository_id,
            resolved_repository_name=grounded.resolved_repository_name
            if grounded
            else state.resolved_repository_name,
            why=why,
            evidence=evidence,
            impact=impact,
            actions=actions,
            entities=entity_refs,
            degraded=False,
        )
        return answer, payload

    def _degraded_general_turn(
        self, state: InvestigationState, grounded: AskResponse | None
    ) -> tuple[str, ConversationTurnPayload]:
        """The LLM call failed — fall back to whatever deterministic facts
        exist rather than fabricating a conversational answer. Honest,
        not polished: the frontend uses `degraded=True` to skip the "AI
        Insight" framing this turn never actually produced."""
        if grounded is not None:
            return grounded.answer, ConversationTurnPayload(
                intent=grounded.intent,
                resolved_repository_id=grounded.resolved_repository_id,
                resolved_repository_name=grounded.resolved_repository_name,
                why=grounded.why,
                evidence=grounded.evidence,
                impact=grounded.impact,
                actions=grounded.actions,
                entities=[],
                degraded=True,
            )
        # No fresh grounding this turn either — but a previously resolved
        # repository is still a real, known fact (not a synthesized one),
        # so it's carried forward rather than silently dropped just
        # because this turn's reasoning step failed.
        return (
            "I couldn't reason over this one — the conversational model is unavailable right "
            "now. Try rephrasing, or ask about a specific repository.",
            ConversationTurnPayload(
                intent="general",
                resolved_repository_id=state.resolved_repository_id,
                resolved_repository_name=state.resolved_repository_name,
                degraded=True,
            ),
        )

    # -- migration mode --------------------------------------------------

    async def _respond_migration(
        self,
        *,
        user_id: uuid.UUID,
        message_text: str,
        prior: list[ConversationMessage],
        state: InvestigationState,
    ) -> tuple[str, ConversationTurnPayload]:
        parsed = parse_migration_intent(message_text)
        migration_scope: MigrationScope | None = None

        if parsed:
            source, target = parsed
            current_source = (state.migration or {}).get("source_technology", "")
            is_new_topic = not state.migration or source.strip().lower() != str(
                current_source
            ).strip().lower()
            if is_new_topic:
                migration_scope = await ground_migration(self._db, user_id, source, target)
                if migration_scope is None:
                    return (
                        f'I couldn\'t find anything in your indexed repositories that '
                        f'references "{source}" — nothing to ground a migration scope on yet. '
                        "Double-check the name, or point me at a specific repository or service.",
                        ConversationTurnPayload(intent="migration_empty", degraded=False),
                    )
        elif not prior:
            # First message, and it doesn't even name a source/target pair
            # — a focused clarifying question, not a guess (no LLM call
            # needed to know the input is incomplete).
            return (
                "What are you migrating, and to what? For example: "
                '"migrate PostgreSQL to BigQuery" or "move our Spark jobs to Databricks".',
                ConversationTurnPayload(intent="clarification", degraded=False),
            )

        if migration_scope is not None:
            state.migration = migration_scope.model_dump()
            state.add_entities([*migration_scope.direct, *migration_scope.indirect])

        return await self._synthesize_migration(
            state=state,
            migration_scope=migration_scope,
            history=prior[-_HISTORY_TURNS:],
            message_text=message_text,
        )

    async def _synthesize_migration(
        self,
        *,
        state: InvestigationState,
        migration_scope: MigrationScope | None,
        history: list[ConversationMessage],
        message_text: str,
    ) -> tuple[str, ConversationTurnPayload]:
        user_prompt = json.dumps(
            {
                "investigation_state": state.to_prompt_dict(),
                "new_graph_facts": migration_scope.model_dump() if migration_scope else None,
                "conversation_history": [{"role": m.role, "content": m.content} for m in history],
                "new_message": message_text,
            }
        )

        try:
            provider = StageAwareLLMProvider(stage=STAGE_ASK)
            response = await provider.complete(
                system_prompt=_MIGRATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                options=LLMRequestOptions(response_format=ResponseFormat.JSON),
            )
            parsed = json.loads(_strip_json_fence(response.text))
            answer = str(parsed.get("answer") or "").strip()
            if not answer:
                raise ValueError("empty answer")
        except (AppError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            logger.warning("migration_synthesis_failed error=%s", exc)
            return self._degraded_migration_turn(state, migration_scope)

        why = str(parsed.get("why") or "")
        entity_refs = _assign_refs(state, parsed.get("entities") or [])

        active_scope = migration_scope.model_dump() if migration_scope else state.migration
        evidence: list[AskEvidenceItem] = []
        impact: AskImpact | None = None
        actions: list[AskAction] = []

        if active_scope:
            direct = active_scope.get("direct", [])
            indirect = active_scope.get("indirect", [])
            total = len(direct) + len(indirect)
            severity = "high" if total >= 5 else ("medium" if total else "low")
            impact = AskImpact(
                severity=severity,
                summary=(
                    f"{len(direct)} direct, {len(indirect)} indirect "
                    "repositories affected."
                ),
                affected_repositories=[*direct, *indirect],
            )
            if migration_scope is not None:
                # Only claim fresh evidence on the turn that actually
                # (re)computed it — a pure follow-up reasoning turn didn't
                # re-query the graph, so it shouldn't re-cite it as if it
                # had.
                evidence = [
                    AskEvidenceItem(
                        source="Dependency Graph",
                        label=(
                            f"{len(direct)} repositor{'y' if len(direct) == 1 else 'ies'} "
                            f"directly reference {migration_scope.source_technology}"
                        ),
                        provenance="derived",
                    )
                ]
                for risk in migration_scope.risks:
                    evidence.append(
                        AskEvidenceItem(
                            source="Dependency Graph",
                            label=f"{risk.label}: {risk.reason}",
                            provenance=risk.provenance,
                        )
                    )
            actions = _migration_actions(active_scope, message_text)

        # Carried forward even on a pure follow-up turn (migration_scope
        # is None but active_scope isn't) — otherwise
        # `_build_investigation_state` would lose the scope on the very
        # next turn, since it only reads `payload["migration"]` off
        # whichever assistant message most recently set it.
        migration_payload = (
            migration_scope
            if migration_scope is not None
            else (MigrationScope(**active_scope) if active_scope else None)
        )

        payload = ConversationTurnPayload(
            intent="migration" if active_scope else "reasoning",
            why=why,
            evidence=evidence,
            impact=impact,
            actions=actions,
            entities=entity_refs,
            migration=migration_payload,
            degraded=False,
        )
        return answer, payload

    def _degraded_migration_turn(
        self, state: InvestigationState, migration_scope: MigrationScope | None
    ) -> tuple[str, ConversationTurnPayload]:
        scope = migration_scope.model_dump() if migration_scope else state.migration
        if scope:
            direct, indirect = scope.get("direct", []), scope.get("indirect", [])
            total = len(direct) + len(indirect)
            severity = "high" if total >= 5 else ("medium" if total else "low")
            content = (
                f"Found {len(direct)} repositor{'y' if len(direct) == 1 else 'ies'} directly "
                f"wired to {scope.get('source_technology', 'that technology')}, and "
                f"{len(indirect)} more reachable through them."
            )
            return content, ConversationTurnPayload(
                intent="migration",
                impact=AskImpact(
                    severity=severity,
                    summary=(
                        f"{len(direct)} direct, {len(indirect)} indirect "
                        "repositories affected."
                    ),
                    affected_repositories=[*direct, *indirect],
                ),
                evidence=[
                    AskEvidenceItem(
                        source="Dependency Graph",
                        label=(
                            f"{len(direct)} repositor{'y' if len(direct) == 1 else 'ies'} "
                            f"directly reference {scope.get('source_technology', '')}"
                        ),
                        provenance="derived",
                    )
                ],
                actions=_migration_actions(scope, ""),
                migration=MigrationScope(**scope),
                degraded=True,
            )
        return (
            "I couldn't reason over this one — the conversational model is unavailable right "
            "now. Try rephrasing, or name the technology you're migrating.",
            ConversationTurnPayload(intent="migration", degraded=True),
        )

    # -- refinement mode --------------------------------------------------

    async def _respond_refinement(
        self,
        *,
        user_id: uuid.UUID,
        message_text: str,
        prior: list[ConversationMessage],
        state: InvestigationState,
        conversation_id: uuid.UUID,
    ) -> tuple[str, ConversationTurnPayload]:
        new_graph_facts: dict[str, Any] | None = None
        source_jira_key = (state.refinement or {}).get("source_jira_key")
        source_jira_url = (state.refinement or {}).get("source_jira_url")

        if not prior:
            fetch = await resolve_requirement(self._db, message_text)
            if fetch.source == "confluence_unsupported":
                return fetch.unresolved_note, ConversationTurnPayload(
                    intent="clarification", degraded=False
                )

            requirement_text = fetch.text or message_text
            repo_id, repo_name, rel_count = await ground_engineering_context(
                self._db, user_id, requirement_text
            )
            new_graph_facts = {
                "requirement_source": fetch.source,
                "requirement_text": requirement_text[:4000],
                "jira_key": fetch.jira_key,
                "unresolved_note": fetch.unresolved_note,
                "engineering_context": (
                    {
                        "repository_id": repo_id,
                        "repository_name": repo_name,
                        "relationship_count": rel_count,
                    }
                    if repo_id
                    else None
                ),
            }
            source_jira_key = fetch.jira_key
            source_jira_url = fetch.jira_url
        elif state.refinement:
            # Follow-up — ground a "what if X slips/is removed" question
            # deterministically against the CURRENT plan's own edges,
            # whenever the message plausibly names an existing item. Real
            # graph math, handed to the LLM as a fact rather than left for
            # it to guess at.
            existing_items = state.refinement.get("work_items") or []
            mentioned = _find_mentioned_work_item(message_text, existing_items)
            if mentioned:
                try:
                    existing_edges = [
                        WorkItemEdge(**e) for e in (state.refinement.get("edges") or [])
                    ]
                except (TypeError, ValueError):
                    existing_edges = []
                downstream = compute_downstream_impact(existing_edges, mentioned)
                new_graph_facts = {
                    "graph_impact_analysis": {
                        "item_id": mentioned,
                        "downstream_item_ids": downstream,
                    }
                }

        return await self._synthesize_refinement(
            state=state,
            new_graph_facts=new_graph_facts,
            history=prior[-_HISTORY_TURNS:],
            message_text=message_text,
            source_jira_key=source_jira_key,
            source_jira_url=source_jira_url,
            conversation_id=conversation_id,
        )

    async def _synthesize_refinement(
        self,
        *,
        state: InvestigationState,
        new_graph_facts: dict[str, Any] | None,
        history: list[ConversationMessage],
        message_text: str,
        source_jira_key: str | None,
        source_jira_url: str | None,
        conversation_id: uuid.UUID,
    ) -> tuple[str, ConversationTurnPayload]:
        user_prompt = json.dumps(
            {
                "investigation_state": state.to_prompt_dict(),
                "new_graph_facts": new_graph_facts,
                "conversation_history": [{"role": m.role, "content": m.content} for m in history],
                "new_message": message_text,
            }
        )

        try:
            provider = StageAwareLLMProvider(stage=STAGE_ASK)
            response = await provider.complete(
                system_prompt=_REFINEMENT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                options=LLMRequestOptions(response_format=ResponseFormat.JSON),
            )
            parsed = json.loads(_strip_json_fence(response.text))
            answer = str(parsed.get("answer") or "").strip()
            if not answer:
                raise ValueError("empty answer")
        except (AppError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            logger.warning("refinement_synthesis_failed error=%s", exc)
            return self._degraded_refinement_turn(state, conversation_id)

        work_items = _parse_work_items(parsed.get("work_items"), source_jira_key)
        valid_ids = {item.id for item in work_items}
        edges = _parse_edges(parsed.get("edges"), valid_ids)
        spikes = _parse_spikes(parsed.get("spikes"), valid_ids)
        open_questions = _parse_open_questions(parsed.get("open_questions"))

        engineering_context_grounded = bool(
            (new_graph_facts or {}).get("engineering_context")
        ) or bool((state.refinement or {}).get("engineering_context_grounded"))

        readiness = compute_readiness(
            objective=str(parsed.get("objective") or ""),
            work_items=work_items,
            engineering_context_grounded=engineering_context_grounded,
            open_questions=open_questions,
        )

        plan = RefinementPlan(
            requirement_summary=str(parsed.get("requirement_summary") or ""),
            objective=str(parsed.get("objective") or ""),
            desired_outcome=str(parsed.get("desired_outcome") or ""),
            scope=_string_list(parsed.get("scope")),
            out_of_scope=_string_list(parsed.get("out_of_scope")),
            functional_requirements=_string_list(parsed.get("functional_requirements")),
            non_functional_requirements=_string_list(parsed.get("non_functional_requirements")),
            constraints=_string_list(parsed.get("constraints")),
            assumptions=_string_list(parsed.get("assumptions")),
            missing_work_categories=_string_list(parsed.get("missing_work_categories")),
            work_items=work_items,
            edges=edges,
            spikes=spikes,
            open_questions=open_questions,
            engineering_context_grounded=engineering_context_grounded,
            readiness=readiness,
            critical_paths=compute_critical_paths(work_items, edges),
            parallelizable_ids=compute_parallelizable(work_items, edges),
            unresolved_source_note=str((new_graph_facts or {}).get("unresolved_note") or ""),
            source_jira_key=source_jira_key,
            source_jira_url=source_jira_url,
        )

        payload = ConversationTurnPayload(
            intent="refinement",
            why=_refinement_why(plan),
            evidence=_refinement_evidence(new_graph_facts, plan),
            actions=_refinement_actions(plan.model_dump(), conversation_id, message_text),
            refinement=plan,
            degraded=False,
        )
        return answer, payload

    def _degraded_refinement_turn(
        self, state: InvestigationState, conversation_id: uuid.UUID
    ) -> tuple[str, ConversationTurnPayload]:
        """The LLM call failed — the plan stays exactly as it was rather
        than being silently reset or half-updated; only the message is
        apologetic, never the (unchanged) structured plan."""
        if state.refinement:
            work_item_count = len(state.refinement.get("work_items") or [])
            content = (
                f"I couldn't update the plan just now — the conversational model is "
                f"unavailable. The current plan still has {work_item_count} work item(s); "
                "try rephrasing your last message."
            )
            try:
                plan = RefinementPlan(**state.refinement)
            except (TypeError, ValueError):
                plan = None
            return content, ConversationTurnPayload(
                intent="refinement",
                actions=_refinement_actions(state.refinement, conversation_id, ""),
                refinement=plan,
                degraded=True,
            )
        return (
            "I couldn't analyze this requirement — the conversational model is unavailable "
            "right now. Try again shortly.",
            ConversationTurnPayload(intent="clarification", degraded=True),
        )
