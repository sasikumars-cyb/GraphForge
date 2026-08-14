"""The concrete investigators — Jira, Confluence, GitHub, the knowledge
graph, and the request parser itself.

Each one wraps the *existing* provider adapter in `context_pipeline.
providers` (unchanged transport, unchanged MCP/REST fallback behavior) and
adds the two things the reasoning engine needs: a `propose` method that
reports what this provider could contribute given current knowledge, and a
`run` method that records facts and evidence through a `Recorder` instead of
returning a bag of data for someone else to interpret.

The ordering that used to be hardcoded in `pipeline.resolve()` is expressed
here as *preconditions* instead, which is what makes it dynamic:

- Confluence's Jira anchor (Atlassian's MCP server has no free-text search,
  only traversal from a known entity) is no longer "Confluence runs second,
  inside the Jira branch". It's `propose` returning nothing until a work
  item fact exists. If the documentation gap is already closed, or was never
  applicable, Confluence proposes nothing and is simply never called.
- GitHub re-parsing enriched Jira prose for a PR reference is no longer a
  fixed second detection pass. `RequestParseInvestigator` proposes a
  re-parse once work item text arrives, and GitHub proposes a fetch once
  that parse yields a reference — the chain assembles itself from state.
- The graph is no longer "always runs last". It proposes a broad survey when
  no repositories are known, a scoped traversal once a repository is
  identified, and a verification query when a human has claimed one.

A provider that returns no actions is how the engine learns it is exhausted,
which is the precondition for asking the human anything at all.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.agents.planning.classifier import analyse
from app.agents.planning.tools import PlanningObservation, rank_repositories
from app.context_pipeline.models import Reference, ReferenceType
from app.context_pipeline.providers import (
    ConfluenceProvider,
    GitHubProvider,
    GraphProvider,
    JiraProvider,
)
from app.context_pipeline.reasoning.capabilities import (
    CANDIDATE_FUNNEL_WIDTH,
    GRAPH_TRAVERSAL_ACTION,
    MAX_SOURCE_FILES_PER_CANDIDATE,
    SOURCE_RETRIEVAL_WIDTH,
    TIE_RATIO,
    _corroboration_evidence,
    _select_dependency_expansion_files,
    _select_relevant_source_files,
    ranked_repository_names,
    repository_role,
)
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    InvestigationOutcome,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.memory import WorkingContext
from app.context_pipeline.reference_detection import detect_references

logger = logging.getLogger(__name__)


def _reference_from_fact_value(value: dict[str, Any]) -> Reference:
    """Rebuild a provider-facing `Reference` from the flat fact that recorded
    it. Facts stay JSON-serializable (they're persisted across a pause);
    providers still get the dataclass they were written against."""
    return Reference(
        type=ReferenceType(value["type"]),
        provider=value.get("provider", ""),
        confidence=float(value.get("confidence", 1.0)),
        raw_value=value.get("raw_value", ""),
        normalized_value=value.get("normalized_value", ""),
    )


def _search_terms(state: WorkingContext) -> list[str]:
    """Relevance terms for graph ranking, derived from everything known so
    far rather than from the raw prompt alone.

    This is a small but real instance of reasoning improving gathering: once
    a Jira ticket's actual description has been folded into the enriched
    text, the graph query that follows is ranked against the ticket's real
    vocabulary, not just the one line the user typed.
    """
    text = state.derived.get("enriched_text") or state.metadata.goal
    return list(analyse(text).search_terms)


def _ticket_terms(state: WorkingContext) -> list[str]:
    """The request's own specific vocabulary only (field/function/entity
    names pulled from the brief itself) — deliberately excludes the fixed,
    generic capability keywords `_search_terms` also folds in ("batch",
    "spark", "airflow", ...), which every repository built from the same
    scaffold matches equally well and so cannot discriminate between
    candidates. Recorded onto the `repository_ranking` fact so later,
    fact-only reasoning (`capabilities._corroborated_ranking_candidates`)
    can check whether fetched source content actually mentions something
    specific to this request, not just its architecture shape.

    RFC-0025 — called from two places, deliberately. `GraphInvestigator
    .propose()` calls it once, embedded in `survey_architecture`'s own
    params, the first time repository ranking ever runs — which is
    necessarily *before* a linked work item's full description has been
    fetched (that action's own precondition is "no `repository` fact
    exists yet", and it can only ever fire once). `engine.investigate()`'s
    main loop calls it again, every cycle, purely to refresh the ticket
    terms already recorded on the ranking fact — never to redo the
    ranking itself — once `enriched_text` (this function's own text
    source) has grown to include that description. Both call sites go
    through this exact function so "what counts as the ticket's specific
    vocabulary" is defined in exactly one place regardless of when it's
    asked."""
    text = state.derived.get("enriched_text") or state.metadata.goal
    return list(analyse(text).ticket_terms)


# ---------------------------------------------------------------------------
# Request parsing — deterministic, no network, but still recorded as evidence
# ---------------------------------------------------------------------------


class RequestParseInvestigator:
    """Recognizes external references in text. Not a network provider, but
    modelled as an investigator for two reasons: its output is exactly the
    same kind of traceable fact everything else produces, and it genuinely
    needs to run more than once — a bare Jira URL's fetched description can
    name a GitHub PR the original prompt never mentioned, and local
    repository names can only be recognized once the graph has told us which
    names exist.
    """

    name = "request_parser"

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        actions: list[InvestigationAction] = []
        ledger = state.ledger
        # Every reference already recorded, so a re-parse only ever records
        # what's genuinely new rather than duplicating earlier passes.
        existing = set(ledger.subjects_of("reference"))

        if not ledger.attempted(self.name, "parse_request"):
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key="parse_request",
                    intent="First, I'll read the request for anything I can look up directly — "
                    "ticket keys, repository names, GitHub links.",
                    targets="work_item",
                    params={"text": state.metadata.goal, "existing": existing},
                    cost=0,
                )
            )

        # Re-parse once retrieved prose exists: the ticket body may reference
        # things the prompt did not.
        if ledger.has_fact("work_item", "document") and not ledger.attempted(
            self.name, "parse_retrieved_content"
        ):
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key="parse_retrieved_content",
                    intent="The ticket has content of its own — I'll check whether it "
                    "references code or pull requests I should also pull in.",
                    targets="work_item",
                    params={
                        "text": "\n".join(
                            f.text for f in ledger.facts_of("work_item", "document") if f.text
                        ),
                        # Same known-repository-name list `match_repository_names`
                        # uses (empty before any repository facts exist, which is
                        # a safe no-op — see `run()`). Without this, a ticket body
                        # naming an indexed repository by its bare name (e.g. "Repo:
                        # etl-core") could never be recognized here: `match_
                        # repository_names` already ran once, against the
                        # pre-fetch request text, and is never re-run once the
                        # ticket body makes more names visible.
                        "known_repositories": frozenset(ledger.subjects_of("repository")),
                        "existing": existing,
                    },
                    cost=0,
                )
            )

        # Local repository names are only recognizable against the set of
        # names the graph actually knows, so this pass can't happen until
        # repository facts exist.
        if ledger.has_fact("repository") and not ledger.attempted(
            self.name, "match_repository_names"
        ):
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key="match_repository_names",
                    intent="Now that I know which repositories are indexed, I'll check "
                    "whether the request names one of them directly.",
                    targets="repository",
                    params={
                        "text": state.derived.get("enriched_text") or state.metadata.goal,
                        "known_repositories": frozenset(ledger.subjects_of("repository")),
                        "existing": existing,
                    },
                    cost=0,
                )
            )

        # ADR 0010 (Theme E) — a second, independent comparison against every
        # repository the user *tracks*, not only the ones actually indexed.
        # A request naming a tracked-but-unindexed repository used to be
        # invisible: no match against the (indexed-only) set above, no
        # evidence, no gap, nothing distinguishing it from a repository that
        # was never mentioned at all. Gated on the same precondition as
        # `match_repository_names` so both passes narrate in a coherent
        # order and the dedup below (`existing`) already covers a name
        # matched by *both* — see `run()`.
        if ledger.has_fact("repository") and not ledger.attempted(
            self.name, "match_tracked_repository_names"
        ):
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key="match_tracked_repository_names",
                    intent="I'll also check whether the request names a repository "
                    "you're tracking but haven't indexed yet.",
                    targets="repository",
                    params={
                        "text": state.derived.get("enriched_text") or state.metadata.goal,
                        "existing": existing,
                    },
                    cost=0,
                )
            )

        return actions

    @staticmethod
    def _describe(key: str, known_repos: frozenset[str]) -> str:
        if key == "parse_retrieved_content":
            return "Scanned the retrieved ticket text for code and pull request references"
        if key == "match_repository_names":
            return f"Checked the request against the {len(known_repos)} indexed repository name(s)"
        return "Read the request for ticket keys, repository names and links"

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        state_text: str = action.params["text"]
        existing: set[str] = action.params.get("existing", set())

        if action.key == "match_tracked_repository_names":
            return await self._match_tracked_repository_names(
                state_text, existing, session, recorder
            )

        known_repos: frozenset[str] = action.params.get("known_repositories", frozenset())
        references = detect_references(state_text, known_repo_names=known_repos)
        # Only local-repository matches are new on the repository-name pass;
        # everything else was already found against the raw text and must not
        # be recorded twice.
        if action.key == "match_repository_names":
            references = [r for r in references if r.type == ReferenceType.LOCAL_REPOSITORY]

        fresh = [r for r in references if r.normalized_value not in existing]

        # Each pass says what it actually looked at. The three passes are
        # genuinely different searches, and giving them one shared summary made
        # the investigation trail read as the same step repeated — which looks
        # like a bug rather than like thoroughness.
        looked_for = self._describe(action.key, known_repos)

        if not fresh:
            recorder.evidence("not_found", f"{looked_for} — nothing new found.")
            return InvestigationOutcome(
                observation=f"{looked_for} — nothing I can look up.",
                yielded=False,
            )

        recorder.evidence(
            "success",
            f"{looked_for} — found "
            + ", ".join(f"{r.type.value}={r.normalized_value}" for r in fresh)
            + ".",
        )
        for ref in fresh:
            value: dict[str, Any] = {
                "type": ref.type.value,
                "provider": ref.provider,
                "confidence": ref.confidence,
                "raw_value": ref.raw_value,
                "normalized_value": ref.normalized_value,
            }
            if action.key == "match_repository_names":
                # Explicit, not merely the absence of the Theme E marker —
                # so `capabilities.py` never has to guess what an old fact
                # recorded before this field existed means.
                value["indexed"] = True
            recorder.fact("reference", ref.normalized_value, value=value)

        described = ", ".join(f"{r.normalized_value}" for r in fresh)
        return InvestigationOutcome(
            observation=f"I found {len(fresh)} reference(s) I can resolve: {described}.",
            yielded=True,
        )

    @staticmethod
    async def _match_tracked_repository_names(
        state_text: str, existing: set[str], session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        """ADR 0010 (Theme E) — a repository the user tracks but has never
        indexed is otherwise invisible to the whole pipeline if the request
        names it: `match_repository_names` above only ever compares against
        *indexed* repositories, so a tracked-but-unindexed match produces no
        reference, no evidence, no gap. Recording it here, with `indexed:
        False`, is what lets `capabilities._repository_signals` phrase the
        actionable gap ("X was mentioned but hasn't been indexed yet")
        instead of the generic "the request does not name a known
        repository" — no new capability, no new fact kind, just a marker on
        the same `reference` fact kind every other pass already uses.
        """
        from sqlalchemy import select

        from app.models.repository import Repository

        result = await session.db.execute(
            select(Repository.name).where(Repository.user_id == session.user_id)
        )
        tracked_names = frozenset(name for (name,) in result.all())

        references = [
            r
            for r in detect_references(state_text, known_repo_names=tracked_names)
            if r.type == ReferenceType.LOCAL_REPOSITORY
        ]
        fresh = [r for r in references if r.normalized_value not in existing]

        looked_for = (
            f"Checked the request against the {len(tracked_names)} tracked repository name(s)"
        )
        if not fresh:
            recorder.evidence("not_found", f"{looked_for} — nothing new found.")
            return InvestigationOutcome(
                observation=f"{looked_for} — nothing new to flag.", yielded=False
            )

        recorder.evidence(
            "success",
            f"{looked_for} — found "
            + ", ".join(f"{r.normalized_value} (not indexed)" for r in fresh)
            + ".",
        )
        for ref in fresh:
            recorder.fact(
                "reference",
                ref.normalized_value,
                value={
                    "type": ref.type.value,
                    "provider": ref.provider,
                    "confidence": ref.confidence,
                    "raw_value": ref.raw_value,
                    "normalized_value": ref.normalized_value,
                    "indexed": False,
                },
            )

        described = ", ".join(r.normalized_value for r in fresh)
        return InvestigationOutcome(
            observation=(
                f"I found {len(fresh)} repository name(s) you're tracking but haven't "
                f"indexed: {described}."
            ),
            yielded=True,
        )


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

# Section headers a real ticket description commonly uses, matched
# case-insensitively at the start of their own line, optionally followed
# by a colon — e.g. "Acceptance Criteria:", "AC", "Business Goal". Purely
# a line-boundary/heading match, never a semantic guess at what a
# paragraph "is about" — the same deterministic, no-inference precedent
# as the rest of this codebase's extraction (ADR 0007). A ticket that
# doesn't use any of these headings yields no structured sections at
# all, which is the honest outcome — this is a real-format-detector, not
# a paraphraser that invents structure a ticket never had.
_TICKET_SECTION_HEADERS: dict[str, tuple[str, ...]] = {
    "problem": ("problem", "problem statement", "issue", "bug"),
    "business_goal": ("business goal", "goal", "objective", "business objective"),
    "acceptance_criteria": ("acceptance criteria", "ac", "definition of done", "dod"),
    "constraints": ("constraints", "known constraints", "limitations"),
    "dependencies": ("dependencies", "depends on", "blocked by"),
}

_ALL_SECTION_ALIASES = [h for headers in _TICKET_SECTION_HEADERS.values() for h in headers]
# Matches a heading at the start of its own line, in either common ticket
# style: alone on its own line ("Acceptance Criteria" / "Acceptance
# Criteria:", content follows on subsequent lines), or with the content
# starting immediately after a colon on the SAME line ("Acceptance
# Criteria: user can log in"). Group 2 (the optional inline remainder)
# only participates when a colon is actually present — a bare heading
# word followed by unrelated prose on the same line ("Goal is unclear
# without more info") has no colon, so `$` must match right after the
# heading itself and correctly fails to match at all.
_SECTION_HEADER_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(h) for h in _ALL_SECTION_ALIASES) + r")\s*(?::[ \t]*(.*))?$",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_ticket_sections(description: str) -> dict[str, str]:
    """Split a ticket description into the structured sections a real
    engineer would look for (Problem, Business Goal, Acceptance Criteria,
    Constraints, Dependencies) — by real section-heading lines the
    description itself contains, never by summarizing/inferring content
    with an LLM. A ticket that doesn't use any of these headings (plain
    prose, no structure) returns an empty dict — the honest outcome; the
    raw description is still available in full via the `work_item`
    fact's own `text`, this is additive, not a replacement.
    """
    if not description:
        return {}

    matches = list(_SECTION_HEADER_RE.finditer(description))
    if not matches:
        return {}

    header_by_alias = {
        alias.lower(): key for key, aliases in _TICKET_SECTION_HEADERS.items() for alias in aliases
    }

    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        key = header_by_alias.get(match.group(1).strip().lower())
        if key is None:
            continue
        inline = (match.group(2) or "").strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(description)
        rest = description[start:end].strip()
        content = f"{inline}\n{rest}".strip() if inline and rest else (inline or rest)
        if content:
            sections[key] = content
    return sections


class JiraInvestigator:
    """Issue tracker. Proposes a fetch for any Jira reference that has been
    recognized but not yet retrieved."""

    name = "jira"

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        ledger = state.ledger
        retrieved = set(ledger.subjects_of("work_item"))
        actions: list[InvestigationAction] = []

        # A human-supplied ticket key is a claim to verify, not a value to
        # accept: the only thing that can settle it is actually fetching it.
        for gap in state.gaps:
            if gap.capability != "work_item" or gap.status != "claimed" or not gap.user_claim:
                continue
            key = f"fetch_work_item:{gap.user_claim}"
            if gap.user_claim in retrieved or ledger.attempted(self.name, key):
                continue
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key=key,
                    intent=f"You gave me '{gap.user_claim}' — I'll try fetching that ticket "
                    "to confirm it's real before I use it.",
                    targets="work_item",
                    params={
                        "reference": {
                            "type": ReferenceType.JIRA_ISSUE.value,
                            "provider": "jira",
                            "confidence": 1.0,
                            "raw_value": gap.user_claim,
                            "normalized_value": gap.user_claim,
                        }
                    },
                    cost=1,
                )
            )

        for fact in ledger.facts_of("reference"):
            if fact.value.get("type") != "jira_issue":
                continue
            key = f"fetch_work_item:{fact.subject}"
            if fact.subject in retrieved or ledger.attempted(self.name, key):
                continue
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key=key,
                    intent=f"The request references {fact.subject} — I'll pull the ticket "
                    "so I'm working from what it actually says.",
                    targets="work_item",
                    params={"reference": fact.value},
                    cost=1,
                )
            )
        return actions

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        from app.tools import ToolExecutor, get_tool_registry

        reference = _reference_from_fact_value(action.params["reference"])
        provider = JiraProvider(ToolExecutor(registry=get_tool_registry()))
        artifact = await provider.resolve(reference)

        if artifact is None or not artifact.text:
            recorder.evidence(
                "unavailable",
                f"Could not retrieve {reference.normalized_value} from Jira "
                "(not found, or Jira isn't connected).",
            )
            return InvestigationOutcome(
                observation=f"I couldn't retrieve {reference.normalized_value} from Jira — "
                "it either doesn't exist or Jira isn't connected.",
                yielded=False,
            )

        recorder.evidence("success", f"Retrieved Jira issue {reference.normalized_value}.")
        # `artifact.raw` already carries the structured fields JiraTool's
        # own result had (summary/description/status/issue_type/priority/
        # labels) — previously discarded in favor of only the combined
        # `context_text`. `sections` is this same description, further
        # split by whatever real section headings it contains (Problem,
        # Business Goal, Acceptance Criteria, Constraints, Dependencies —
        # see `_extract_ticket_sections`), so "Business Objective" and
        # "Acceptance Criteria" are answerable without an LLM re-reading
        # the raw ticket text every time.
        description = str(artifact.raw.get("description", ""))
        recorder.fact(
            "work_item",
            reference.normalized_value,
            value={
                "title": artifact.title,
                "status": artifact.raw.get("status", ""),
                "issue_type": artifact.raw.get("issue_type", ""),
                "priority": artifact.raw.get("priority", ""),
                "labels": artifact.raw.get("labels", []),
                "sections": _extract_ticket_sections(description),
            },
            text=artifact.text,
        )
        return InvestigationOutcome(
            observation=f"I have {reference.normalized_value} — I'll use its description "
            "to understand what this change is really about.",
            yielded=True,
        )


# ---------------------------------------------------------------------------
# Confluence
# ---------------------------------------------------------------------------


class ConfluenceInvestigator:
    """Documentation. Anchored on a resolved work item — expressed as a
    precondition rather than a pipeline position, so it is skipped entirely
    when documentation isn't applicable or is already satisfied."""

    name = "confluence"

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        documentation = state.assessment_for("documentation")
        if documentation is None or documentation.necessity == "not_applicable":
            return []
        if documentation.satisfied:
            return []

        work_items = state.ledger.facts_of("work_item")
        if not work_items:
            # No anchor to traverse from — this provider genuinely cannot
            # help yet, so it stays silent rather than burning a turn.
            return []

        anchor = work_items[0]
        key = f"fetch_documentation:{anchor.subject}"
        if state.ledger.attempted(self.name, key):
            # Allow retry if the previous attempt was "unavailable"
            # (infrastructure: connection not configured) rather than
            # "not_found" or "success" (a real answer was obtained).
            # On resume, the connection may have been fixed since the
            # initial run.
            prev = next(
                (e for e in state.ledger.evidence if e.provider == self.name and e.action == key),
                None,
            )
            if prev is None or prev.outcome != "unavailable":
                return []

        return [
            InvestigationAction(
                provider=self.name,
                key=key,
                intent=f"I'll look for design documentation linked to {anchor.subject} — "
                "it often explains constraints the ticket leaves out.",
                targets="documentation",
                params={
                    "work_item": anchor.subject,
                    # The document search is relevance-ranked against the
                    # request as currently understood, so a ticket that has
                    # already been fetched sharpens this query too.
                    "task_description": state.derived.get("enriched_text") or state.metadata.goal,
                },
                # Multi-turn MCP conversation: the most expensive automated
                # source, so it loses ties against cheaper ones.
                cost=3,
            )
        ]

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        work_item = action.params["work_item"]
        provider = ConfluenceProvider(session.db, session.intelligence)
        artifact = await provider.resolve_for_issue(
            jira_issue_key=work_item,
            task_description=action.params.get("task_description", work_item),
            model=session.model,
            stage=session.stage,
        )

        if artifact is None or not artifact.text:
            # The provider distinguishes "reached Confluence, nothing
            # relevant" from "unavailable" via evidence.status — and
            # "unavailable" itself covers more than "not connected": no
            # access method configured, every MCP call attempted but
            # failed (e.g. an Atlassian API token missing Teamwork Graph
            # permission), or the LLM call itself failing. ConfluenceProvider
            # already picked the right specific summary for whichever of
            # those actually happened (see providers.py's own resolve_for_
            # issue) — reusing it here, rather than collapsing every
            # non-"not_found" case back into one hardcoded "not connected"
            # message, is what actually preserves that distinction end to
            # end instead of only claiming to.
            status = (artifact.evidence.status if artifact else None) or "unavailable"
            outcome = "not_found" if status == "not_found" else "unavailable"
            summary = (
                f"Searched Confluence from {work_item} and found no relevant pages."
                if outcome == "not_found"
                else (
                    (artifact.evidence.summary if artifact else None)
                    or "Confluence lookup could not be completed."
                )
            )
            recorder.evidence(outcome, summary)
            return InvestigationOutcome(observation=summary, yielded=False)

        recorder.evidence("success", f"Retrieved Confluence documentation linked to {work_item}.")
        recorder.fact(
            "document",
            artifact.title or "Confluence",
            value={"work_item": work_item},
            text=artifact.text,
        )
        return InvestigationOutcome(
            observation=f"I found documentation linked to {work_item} and folded it in.",
            yielded=True,
        )


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


class GitHubInvestigator:
    """Source control. Proposes a fetch for any GitHub PR/issue/repository
    reference recognized in the request *or* inside retrieved ticket prose —
    which is how a failed Jira lookup can still end up with useful code
    context instead of an immediate question to the user.

    "github_repository" (a bare "owner/repo" mention, no #issue/#pr) is
    included alongside the PR/issue types: `GitHubProvider.resolve()`
    already declared it resolvable (`can_resolve()`) and now actually
    fetches it (see providers.py), so proposing it here is what makes that
    reachable rather than a reference that's detected and then dropped."""

    name = "github"

    _FETCHABLE = ("github_pull_request", "github_issue", "github_repository")

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        ledger = state.ledger
        retrieved = set(ledger.subjects_of("pull_request"))
        actions: list[InvestigationAction] = []

        for fact in ledger.facts_of("reference"):
            if fact.value.get("type") not in self._FETCHABLE:
                continue
            key = f"fetch_pull_request:{fact.subject}"
            if fact.subject in retrieved or ledger.attempted(self.name, key):
                continue
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key=key,
                    intent=f"{fact.subject} is referenced — I'll read it for the code "
                    "context around this change.",
                    targets="architecture",
                    params={"reference": fact.value},
                    cost=2,
                )
            )
        if actions:
            return actions

        # -- corroborate via source (RFC-0011, escalation path widened by
        # RFC-0013, made evidence-real by RFC-0014): the funnel's most
        # expensive stage, reached only for a ranked candidate the cheap
        # graph stage already scoped (`scope_architecture:{name}` already
        # attempted — see `GraphInvestigator.propose`) but whose
        # relationship evidence isn't independently sufficient. Bounded to
        # `SOURCE_RETRIEVAL_WIDTH` candidates, best-ranked first — never
        # "fetch every ranked repository". A candidate whose source WAS
        # fetched but doesn't mention this request's own vocabulary simply
        # stays uncorroborated (see `capabilities._corroborated_ranking_
        # candidates`) — this is how source evidence can reject a lexical
        # leader, not only confirm one.
        #
        # RFC-0014 — this fetches actual *file content* now
        # (`_select_relevant_source_files`, bounded to `MAX_SOURCE_FILES_
        # PER_CANDIDATE`), not repository metadata: fetching `/repos/{owner}
        # /{repo}` and calling that "reading its source" meant this stage's
        # only real signal was frequently the repository's own *name*
        # showing up in its own metadata text — corroborating a candidate
        # via what's structurally still a name match, dressed up as
        # "source evidence". See `_run_source_file_fetch` for the fetch
        # itself.
        repository = state.assessment_for("repository")
        if repository is None or repository.satisfied:
            return actions
        by_name = {f.subject: f for f in ledger.facts_of("repository")}
        # RFC-0013 — *already independently corroborated* (RFC-0012's
        # specificity-weighted evidence: confidence tier ÷ relationship
        # degree, at or above the sufficiency bar), not merely "has some
        # relationship fact of any strength." The pre-RFC-0012 version of
        # this check excluded a candidate the moment *any* relationship
        # fact named it — exactly wrong once relationship evidence is
        # weighted, since a common/shared-dependency relationship (weak by
        # design) would then wrongly exempt a candidate from the one
        # remaining stage that could actually resolve it. Reusing
        # `_corroboration_evidence` directly (rather than re-deriving the
        # same weighting a second way) is what keeps "strongly corroborated
        # already, skip source retrieval" and "weakly related, escalate to
        # source retrieval" from ever silently drifting apart — the same
        # weighting decides both.
        already_corroborated = set(_corroboration_evidence(ledger))
        eligible = [
            name
            for name in ranked_repository_names(ledger, limit=CANDIDATE_FUNNEL_WIDTH)
            if ledger.attempted("graph", f"scope_architecture:{name}")
            and name not in already_corroborated
        ][:SOURCE_RETRIEVAL_WIDTH]
        # `enumerate` over `eligible`'s own best-ranked-first order becomes
        # each action's `priority` — same reasoning as `GraphInvestigator`'s
        # corroboration branch: several same-capability, same-cost actions
        # proposed in one cycle must not fall through to alphabetical
        # `action.key` ordering when a real ranking signal already exists.
        for rank, name in enumerate(eligible):
            repo_fact = by_name.get(name)
            full_name = (repo_fact.value.get("full_name") if repo_fact else None) or name
            key = f"fetch_source_files:{full_name}"
            if ledger.attempted(self.name, key):
                continue
            file_paths = _select_relevant_source_files(
                ledger, name, limit=MAX_SOURCE_FILES_PER_CANDIDATE
            )
            if not file_paths:
                # Nothing already indexed for this repository shares a word
                # with the ticket's own vocabulary — no fetch, no guess.
                # This is a real, honest outcome (see `_select_relevant_
                # source_files`'s own docstring), not a reason to fall back
                # to fetching repository metadata instead.
                continue
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key=key,
                    intent=f"'{name}' still isn't confirmed after checking its graph "
                    f"relationships — I'll read {', '.join(file_paths)} to look for "
                    "evidence that actually corroborates it.",
                    targets="repository",
                    params={"full_name": full_name, "repository": name, "file_paths": file_paths},
                    cost=2,
                    priority=rank,
                )
            )

        # RFC-0022 — dependency-aware expansion: one structural CALLS/
        # IMPORTS hop beyond source *this run has actually fetched*
        # (`source_file` facts), never a fresh lexical-ranking pass and
        # never before something real has been read. Only ever proposed
        # for a candidate already in this same `eligible` funnel (the
        # existing `CANDIDATE_FUNNEL_WIDTH`/`SOURCE_RETRIEVAL_WIDTH`
        # bounds are unchanged) that already has fetched source to expand
        # from — a repository whose initial fetch action is proposed in
        # *this same* `propose()` call has no `source_file` fact yet
        # (recorded only once `run()` for that action completes), so this
        # naturally never fires in the same cycle as the fetch it expands
        # from; it becomes eligible only on a later cycle, bounded by the
        # engine's existing `MAX_CYCLES` the same way every other stage
        # already is.
        fetched_repos = {
            str(f.value.get("repository"))
            for f in ledger.facts_of("source_file")
            if f.value.get("repository")
        }
        for rank, name in enumerate(eligible):
            if name not in fetched_repos:
                continue
            repo_fact = by_name.get(name)
            full_name = (repo_fact.value.get("full_name") if repo_fact else None) or name
            key = f"dependency_expand_source_files:{full_name}"
            if ledger.attempted(self.name, key):
                continue
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key=key,
                    intent=f"I already read source from '{name}' — I'll follow its own "
                    "CALLS/IMPORTS relationships one hop to see if a directly connected "
                    "file carries more evidence.",
                    targets="repository",
                    params={
                        "full_name": full_name,
                        "repository": name,
                        "dependency_expansion": True,
                    },
                    cost=2,
                    priority=rank,
                )
            )
        return actions

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        # RFC-0022 — dependency expansion resolves its own file list (a
        # graph query, so it can't happen in `propose()`) and then
        # delegates to the exact same fetch/record path below.
        if action.params.get("dependency_expansion"):
            return await self._run_dependency_expansion(action, session, recorder)

        # RFC-0014 — the escalation branch above proposes a bounded set of
        # actual file paths (`params["file_paths"]`), never a generic
        # `Reference`; every other branch (explicit PR/issue/repository
        # references) is unchanged from before.
        if "file_paths" in action.params:
            return await self._run_source_file_fetch(action, session, recorder)

        from app.core.config import get_settings
        from app.services.github_service import get_decrypted_access_token
        from app.tools import ToolExecutor, get_tool_registry
        from app.tools.implementations.github_tool import GitHubTool

        reference = _reference_from_fact_value(action.params["reference"])

        token = (
            await get_decrypted_access_token(session.db, session.user_id)
            if session.user_id is not None
            else None
        )
        if token is None:
            recorder.evidence(
                "unavailable",
                f"Cannot read {reference.normalized_value}: no GitHub account is connected.",
            )
            return InvestigationOutcome(
                observation=f"I can see {reference.normalized_value} referenced, but no GitHub "
                "account is connected so I can't read it.",
                yielded=False,
            )

        provider = GitHubProvider(
            ToolExecutor(registry=get_tool_registry()),
            GitHubTool(
                {
                    "github_token": token,
                    "github_mcp_server_url": get_settings().github_mcp_default_server_url,
                    "github_mcp_api_key": token,
                }
            ),
        )
        artifact = await provider.resolve(reference)

        if artifact is None or not artifact.text:
            recorder.evidence(
                "not_found", f"Could not retrieve {reference.normalized_value} from GitHub."
            )
            return InvestigationOutcome(
                observation=f"I couldn't retrieve {reference.normalized_value} from GitHub.",
                yielded=False,
            )

        recorder.evidence("success", f"Retrieved {reference.normalized_value} from GitHub.")
        recorder.fact(
            "pull_request",
            reference.normalized_value,
            value={"title": artifact.title},
            text=artifact.text,
        )
        return InvestigationOutcome(
            observation=f"I read {reference.normalized_value} for the surrounding code context.",
            yielded=True,
        )

    async def _run_source_file_fetch(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        """RFC-0014 — fetches actual file *content* for the bounded set of
        paths `propose()`'s escalation branch selected, via the existing
        `GitHubTool.get_file_contents` (already bounded to 8000 chars per
        file — reused unchanged, not re-invented). One `source_file` fact
        per successfully-fetched file, all citing the same evidence record
        — the same one-evidence-many-facts shape `GraphInvestigator`'s own
        scoped traversal already uses for `component`/`topic` facts.
        """
        from app.core.config import get_settings
        from app.services.github_service import get_decrypted_access_token
        from app.tools.implementations.github_tool import GitHubTool

        full_name = str(action.params["full_name"])
        repository = str(action.params["repository"])
        file_paths = list(action.params["file_paths"])
        owner_repo = full_name.split("/", 1)

        token = (
            await get_decrypted_access_token(session.db, session.user_id)
            if session.user_id is not None
            else None
        )
        if token is None or len(owner_repo) != 2:
            recorder.evidence(
                "unavailable",
                f"Cannot read source for {full_name}: no GitHub account is connected."
                if token is None
                else f"'{full_name}' isn't a valid owner/repo reference.",
            )
            return InvestigationOutcome(
                observation=f"I wanted to read {full_name}'s source but couldn't — no GitHub "
                "account is connected.",
                yielded=False,
            )

        owner, repo = owner_repo
        tool = GitHubTool(
            {
                "github_token": token,
                "github_mcp_server_url": get_settings().github_mcp_default_server_url,
                "github_mcp_api_key": token,
            }
        )

        fetched: list[tuple[str, str]] = []
        for path in file_paths:
            result = await tool.get_file_contents(owner, repo, path)
            content = str(result.data.get("content") or "") if result.success else ""
            if content:
                fetched.append((path, content))

        if not fetched:
            recorder.evidence(
                "not_found", f"Could not read {', '.join(file_paths)} from {full_name}."
            )
            return InvestigationOutcome(
                observation=f"I couldn't read source for {full_name}.", yielded=False
            )

        fetched_paths = [path for path, _content in fetched]
        evidence = recorder.evidence(
            "success",
            f"Read {len(fetched)} source file(s) from {full_name}: {', '.join(fetched_paths)}.",
        )
        for path, content in fetched:
            recorder.fact(
                "source_file",
                f"{full_name}::{path}",
                value={"repository": repository, "full_name": full_name, "path": path},
                text=content,
                evidence=evidence,
            )
        return InvestigationOutcome(
            observation=f"I read {', '.join(fetched_paths)} from {full_name} for corroborating "
            "evidence.",
            yielded=True,
        )

    async def _run_dependency_expansion(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        """RFC-0022 — one structural `CALLS`/`IMPORTS` hop beyond source
        this run has *actually fetched*, reusing `get_neighborhood`
        (already hop-budget-wrapped, already used unchanged for RFC-004's
        shadow call-chain reconstruction — see `curate_evidence`) instead
        of a new graph query, and delegating the real fetch to
        `_run_source_file_fetch` unchanged once the target paths are
        resolved — only the *selection* upstream of it is new.
        """
        from app.graph.neo4j_repository import Neo4jGraphRepository
        from app.graph.session import get_driver

        full_name = str(action.params["full_name"])
        repository = str(action.params["repository"])

        fetched_paths = {
            str(f.value.get("path"))
            for f in recorder.facts_of("source_file")
            if f.value.get("repository") == repository and f.value.get("path")
        }
        if not fetched_paths:
            recorder.evidence(
                "not_found", f"No source has been read from '{repository}' yet to expand from."
            )
            return InvestigationOutcome(
                observation=f"I haven't read any source from '{repository}' yet.", yielded=False
            )

        node_ids_by_file_path: dict[str, list[str]] = {}
        file_path_by_node_id: dict[str, str] = {}
        for fact in recorder.facts_of("component"):
            if fact.value.get("repository") != repository:
                continue
            file_path = str(fact.value.get("file_path") or "")
            node_id = str(fact.value.get("id") or "")
            if not file_path or not node_id:
                continue
            node_ids_by_file_path.setdefault(file_path, []).append(node_id)
            file_path_by_node_id[node_id] = file_path

        seed_ids = [nid for path in fetched_paths for nid in node_ids_by_file_path.get(path, [])]
        if not seed_ids:
            recorder.evidence(
                "not_found",
                f"The source read from '{repository}' has no indexed symbols to expand from.",
            )
            return InvestigationOutcome(
                observation=f"'{repository}''s fetched source has no indexed CALLS/IMPORTS "
                "to follow.",
                yielded=False,
            )

        repository_id = seed_ids[0].split(":", 1)[0]
        graph_repo = session.graph_repo_override or Neo4jGraphRepository(get_driver())
        try:
            payload = await graph_repo.get_neighborhood(
                repository_id, seed_ids, ["CALLS", "IMPORTS"], 1, direction="outgoing"
            )
        except Exception:
            logger.exception(
                "context_discovery_dependency_expansion_failed repository=%s", repository
            )
            recorder.evidence(
                "failed",
                f"Could not traverse CALLS/IMPORTS relationships from '{repository}'.",
            )
            return InvestigationOutcome(
                observation=f"I couldn't follow '{repository}''s dependency relationships.",
                yielded=False,
            )

        # RFC-0023 — the induced subgraph `get_neighborhood` returns
        # already carries each touched node's own `properties` (`name`,
        # `file_path`, and — via `labels` — its type), the exact shape
        # every other component in this codebase is already scored by.
        # Read straight from `payload.nodes` (the query's own result, not
        # only the ledger's pre-existing `component` facts) so a target
        # this run hasn't otherwise indexed a fact for still scores
        # correctly.
        node_by_id = {node.id: node for node in payload.nodes}
        for node_id, node in node_by_id.items():
            fp = str(node.properties.get("file_path") or "")
            if fp:
                file_path_by_node_id.setdefault(node_id, fp)

        # Only edges whose *source* is one of the fetched files' own seeds
        # — direction="outgoing" already guarantees this at the graph
        # layer, this second check is just being explicit about the
        # invariant `_select_dependency_expansion_files` relies on: every
        # target's provenance traces back to something actually fetched.
        seed_id_set = set(seed_ids)
        dependency_targets: dict[str, set[str]] = {}
        target_components: dict[str, list[dict[str, Any]]] = {}
        # RFC-0024 — which targets are reached by at least one direct
        # `CALLS` edge (a real invocation) rather than only `IMPORTS` (a
        # static reference) — see `_select_dependency_expansion_files`'s
        # directness tie-break.
        direct_targets: set[str] = set()
        for edge in payload.edges:
            if edge.type not in ("CALLS", "IMPORTS") or edge.source_id not in seed_id_set:
                continue
            target_path = file_path_by_node_id.get(edge.target_id)
            source_path = file_path_by_node_id.get(edge.source_id)
            if not target_path or not source_path or target_path in fetched_paths:
                continue
            dependency_targets.setdefault(target_path, set()).add(source_path)
            if edge.type == "CALLS":
                direct_targets.add(target_path)
            target_node = node_by_id.get(edge.target_id)
            if target_node is not None:
                target_components.setdefault(target_path, []).append(
                    {
                        "name": target_node.properties.get("name", target_node.id),
                        "type": next(
                            (label for label in target_node.labels if label != "Component"),
                            "Component",
                        ),
                        "file_path": target_path,
                        "is_test": target_node.properties.get("is_test"),
                    }
                )

        # RFC-0024 — repo-wide fan-in per candidate target, the structural
        # analogue of the term-frequency count already used for text (see
        # `Neo4jGraphRepository.get_dependency_fan_in`'s own docstring).
        # Optional and best-effort: a test double substituted via
        # `graph_repo_override` that predates this RFC simply won't have
        # the method, and ranking degrades to exactly RFC-0023's behavior
        # (no structural discount) rather than failing the whole action —
        # the same "None means unavailable, degrade silently" spirit
        # `SessionContext.intelligence`/`progress_sink` already use.
        fan_in: dict[str, int] = {}
        get_fan_in = getattr(graph_repo, "get_dependency_fan_in", None)
        if get_fan_in is not None and dependency_targets:
            try:
                fan_in = await get_fan_in(
                    repository_id, list(dependency_targets.keys()), ["CALLS", "IMPORTS"]
                )
            except Exception:
                logger.exception(
                    "context_discovery_dependency_fan_in_failed repository=%s", repository
                )
                fan_in = {}

        file_paths = _select_dependency_expansion_files(
            recorder.ledger,
            repository,
            dependency_targets,
            target_components,
            direct_targets=direct_targets,
            fan_in=fan_in,
        )
        if not file_paths:
            recorder.evidence(
                "not_found",
                f"'{repository}''s fetched source has no CALLS/IMPORTS relationship worth "
                "following further.",
            )
            return InvestigationOutcome(
                observation=f"'{repository}''s fetched source doesn't lead anywhere new.",
                yielded=False,
            )

        fetch_action = InvestigationAction(
            provider=action.provider,
            key=action.key,
            intent=action.intent,
            targets=action.targets,
            params={**action.params, "file_paths": file_paths},
            cost=action.cost,
            priority=action.priority,
        )
        return await self._run_source_file_fetch(fetch_action, session, recorder)


class GoogleDriveInvestigator:
    """Documentation capability — Drive's counterpart to GitHubInvestigator
    above: proposes a fetch for any Google Drive file/folder reference
    recognized in the request or inside retrieved ticket prose. Feeds the
    same `documentation` capability Confluence does (see capabilities.py)
    rather than a new one — from Context Discovery's point of view, Drive
    is just another place design docs happen to live.
    """

    name = "google_drive"

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        ledger = state.ledger
        retrieved = set(ledger.subjects_of("document"))
        actions: list[InvestigationAction] = []

        for fact in ledger.facts_of("reference"):
            if fact.value.get("type") != "google_drive_file":
                continue
            key = f"fetch_drive_file:{fact.subject}"
            if fact.subject in retrieved or ledger.attempted(self.name, key):
                continue
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key=key,
                    intent=f"{fact.subject} is referenced — I'll read it as linked "
                    "documentation for this change.",
                    targets="documentation",
                    params={"reference": fact.value},
                    cost=2,
                )
            )
        return actions

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        from app.context_pipeline.providers import GoogleDriveProvider
        from app.services.google_drive_service import get_decrypted_access_token
        from app.tools import ToolExecutor, get_tool_registry
        from app.tools.implementations.google_drive_tool import GoogleDriveTool

        reference = _reference_from_fact_value(action.params["reference"])

        token = (
            await get_decrypted_access_token(session.db, session.user_id)
            if session.user_id is not None
            else None
        )
        if token is None:
            recorder.evidence(
                "unavailable",
                "Cannot read the referenced Google Drive file: no Google Drive account "
                "is connected.",
            )
            return InvestigationOutcome(
                observation="I can see a Google Drive file referenced, but no Google Drive "
                "account is connected so I can't read it.",
                yielded=False,
            )

        provider = GoogleDriveProvider(
            ToolExecutor(registry=get_tool_registry()),
            GoogleDriveTool({"google_drive_access_token": token}),
        )
        artifact = await provider.resolve(reference)

        if artifact is None or not artifact.text:
            recorder.evidence("not_found", "Could not retrieve the referenced Google Drive file.")
            return InvestigationOutcome(
                observation="I couldn't retrieve the referenced Google Drive file.",
                yielded=False,
            )

        recorder.evidence("success", f"Retrieved '{artifact.title}' from Google Drive.")
        recorder.fact(
            "document",
            reference.normalized_value,
            value={"title": artifact.title},
            text=artifact.text,
        )
        return InvestigationOutcome(
            observation=f"I read '{artifact.title}' from Google Drive for the surrounding context.",
            yielded=True,
        )


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

# Deterministic, template-based explanations for a tracked repository that
# `GraphHealthService` (app.graph.health) did NOT report as HEALTHY —
# "GraphHealthStatus.value" -> "why", so a repository that can't be used
# says why instead of just silently not appearing in `indexed_repositories`,
# the same way NOT_INDEXED, INDEXING, and GRAPH_MISSING all used to be
# indistinguishable from each other and from "never tracked at all".
_UNHEALTHY_EXPLANATIONS: dict[str, str] = {
    "graph_missing": (
        "'{name}' is tracked and its latest indexing job completed, but no graph "
        "currently exists for it in the knowledge graph — re-index required before "
        "it can be used."
    ),
    "indexing": "'{name}' is currently being indexed — no graph is available yet.",
    "not_indexed": (
        "'{name}' is tracked but has never been successfully indexed — run "
        "indexing before it can be used."
    ),
}


def _describe_unhealthy(repo: dict[str, Any]) -> str:
    template = _UNHEALTHY_EXPLANATIONS.get(
        repo.get("status", ""), "'{name}' is tracked but its graph isn't currently usable."
    )
    return template.format(name=repo.get("name", ""))


class GraphInvestigator:
    """Repository metadata and architecture. The only investigator that
    proposes more than one *kind* of action, because the graph answers three
    genuinely different questions depending on what is already known:

    - **survey** — nothing is known about repositories yet; find out what
      exists and which of them this request plausibly touches.
    - **scope** — a repository has been identified but no architecture has
      been discovered for it; traverse it specifically.
    - **verify** — a human named a repository; check whether the graph
      actually contains it before believing them. This is the query that
      makes `verify-then-resolve` real rather than nominal: if the graph has
      no such repository, no repository fact is created, so no confidence
      signal flips, and the claim is refuted instead of accepted.
    """

    name = "graph"

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        ledger = state.ledger
        actions: list[InvestigationAction] = []

        # -- verify: a human claim outranks everything else, because until
        # it's checked the engine is holding an unverified belief.
        #
        # Scoped to `repository` claims only: a claimed gap on *any*
        # capability used to reach this loop, so answering a work_item
        # clarification question (a corrected Jira key) was treated as a
        # repository name to verify. That ran a real graph query for a
        # ticket key, narrated a nonsensical "You pointed me at 'PROJ-456'"
        # intent, and — because `_reassess_candidates` unconditionally
        # withdraws every live `repository_candidate` inference before
        # deciding whether the focus matched anything — silently destroyed
        # an already-correct, already-satisfied repository identification
        # any time an unrelated claim was being settled.
        for gap in state.gaps:
            if gap.capability != "repository" or gap.status != "claimed" or not gap.user_claim:
                continue
            key = f"verify_repository:{gap.user_claim}"
            if ledger.attempted(self.name, key):
                continue
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key=key,
                    intent=f"You pointed me at '{gap.user_claim}' — I'll confirm it exists in "
                    "the indexed graph and traverse it before I rely on that.",
                    targets="repository",
                    params={
                        "claim": gap.user_claim,
                        "gap_id": gap.gap_id,
                        "query": self._query_text(state),
                        "search_terms": _search_terms(state),
                    },
                    cost=1,
                )
            )

        if actions:
            return actions

        # -- explicit references: the user pre-selected repositories (rerun).
        # Verify each one individually instead of doing a global survey that
        # would pull in components from every indexed repository. This
        # guarantees graph traversal is scoped to only the selected repos.
        if not ledger.has_fact("repository"):
            explicit_refs = [
                f
                for f in ledger.facts_of("reference")
                if f.value.get("type") == "local_repository"
                and f.value.get("source") == "explicit_selection"
            ]
            if explicit_refs:
                for ref in explicit_refs:
                    key = f"verify_repository:{ref.subject}"
                    if not ledger.attempted(self.name, key):
                        actions.append(
                            InvestigationAction(
                                provider=self.name,
                                key=key,
                                intent=f"You selected '{ref.subject}' — I'll confirm it exists "
                                "in the indexed graph and traverse it.",
                                targets="repository",
                                params={
                                    "claim": ref.subject,
                                    "query": self._query_text(state),
                                    "search_terms": _search_terms(state),
                                },
                                cost=1,
                            )
                        )
                if actions:
                    return actions

        repository = state.assessment_for("repository")
        architecture = state.assessment_for("architecture")

        # -- survey: no repository knowledge at all yet.
        if not ledger.has_fact("repository") and not ledger.attempted(
            self.name, "survey_architecture"
        ):
            actions.append(
                InvestigationAction(
                    provider=self.name,
                    key="survey_architecture",
                    intent="I'll search the indexed repositories and architecture graph to "
                    "work out which service this request belongs to.",
                    targets="repository",
                    params={
                        "query": self._query_text(state),
                        "search_terms": _search_terms(state),
                        "ticket_terms": _ticket_terms(state),
                    },
                    cost=1,
                )
            )
            return actions

        # -- corroborate (RFC-0011): repository knowledge exists but nothing
        # is confidently identified yet — only ranking survivors, which
        # `capabilities._repository_signals` deliberately does not treat as
        # "identified" on their own (a lone lexical winner is not evidence
        # the request is actually about that repository). Scope-traverse a
        # BOUNDED set of the top-ranked candidates (`CANDIDATE_FUNNEL_WIDTH`,
        # not every indexed repository) specifically to gather the
        # cross-repository-relationship evidence that can corroborate — or
        # fail to corroborate — one of them. Reuses the exact same scoped
        # traversal ("scope_architecture:{target}") the post-identification
        # branch below already used for a single confirmed repository; the
        # only change is *when* it's allowed to fire and for how many
        # candidates at once.
        if repository is not None and not repository.satisfied:
            # `enumerate` over `ranked_repository_names`'s own best-first
            # order becomes each action's `priority` — the funnel's ranking
            # signal, carried through to `_select`'s tie-break. Without
            # this, every one of these same-capability, same-cost actions
            # ties on `_select`'s remaining criteria and falls through to
            # `action.key` (`scope_architecture:{target}`), which sorts
            # *alphabetically by repository name* — an accident of string
            # comparison, not a relevance judgement. On PROT-5764's live
            # benchmark that meant the #3 and #4 ranked candidates (whose
            # names happened to sort first) were investigated before #1
            # and #2, exhausting the cycle budget before the actual answer
            # — ranked #2 — was ever scoped. Rank position, not the name,
            # must decide investigation order here.
            for rank, target in enumerate(ranked_repository_names(ledger, limit=CANDIDATE_FUNNEL_WIDTH)):
                key = f"scope_architecture:{target}"
                if ledger.attempted(self.name, key):
                    continue
                actions.append(
                    InvestigationAction(
                        provider=self.name,
                        key=key,
                        intent=f"'{target}' ranks closely against this request but isn't "
                        "confirmed yet — I'll traverse its dependencies specifically to look "
                        "for corroborating evidence before I rely on it.",
                        targets="repository",
                        params={
                            "repository": target,
                            "query": self._query_text(state),
                            "search_terms": _search_terms(state),
                            # Tells `run()` not to let this single, still-
                            # unconfirmed candidate overwrite the shared
                            # full-repository ranking Planning reads —
                            # see `run()`'s own note on `derived
                            # ["ranked_repositories"]`.
                            "corroboration_probe": True,
                        },
                        cost=1,
                        priority=rank,
                    )
                )
            if actions:
                return actions

        # -- scope: an owner is known; go deeper on it specifically. Only
        # worth doing when architecture is still unsatisfied — if the graph
        # already told us enough, we stop rather than gathering for its own
        # sake.
        candidates = ledger.live_inferences("repository_candidate")
        if (
            len(candidates) == 1
            and repository is not None
            and repository.satisfied
            and architecture is not None
            and not architecture.satisfied
        ):
            target = candidates[0].statement
            key = f"scope_architecture:{target}"
            if not ledger.attempted(self.name, key):
                actions.append(
                    InvestigationAction(
                        provider=self.name,
                        key=key,
                        intent=f"'{target}' looks like the owner — I'll traverse its "
                        "dependencies specifically to fill in the architecture.",
                        targets="architecture",
                        params={
                            "repository": target,
                            "query": self._query_text(state),
                            "search_terms": _search_terms(state),
                        },
                        cost=1,
                    )
                )

        return actions

    @staticmethod
    def _query_text(state: WorkingContext) -> str:
        """What the graph tool searches against — the request as currently
        understood, including any ticket/doc prose already folded in, so a
        later traversal is better-informed than an earlier one."""
        return state.derived.get("enriched_text") or state.metadata.goal

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        from app.tools import ContextBuilder, ToolExecutor, ToolInput, get_tool_registry

        focus: str | None = action.params.get("claim") or action.params.get("repository")
        terms: list[str] = action.params.get("search_terms") or []
        if focus:
            # A focused query ranks around the specific repository in
            # question, while keeping the request's own terms so component
            # relevance inside that repository is still meaningful.
            terms = [focus, *terms]

        provider = GraphProvider(ToolExecutor(registry=get_tool_registry()))
        result = await provider.retrieve(
            ToolInput(
                query=action.params.get("query", ""),
                parameters={
                    "db": session.db,
                    "user_id": session.user_id,
                    "relevance_terms": terms,
                    "graph_repo": session.graph_repo_override,
                    # A "scope"/"verify" action already knows which
                    # repository this is about — restrict traversal to it
                    # instead of re-fetching every OTHER indexed
                    # repository's full component list too. A genuine
                    # "survey" (focus is None) still traverses everything,
                    # which is the one case that legitimately needs to.
                    "repository_filter": [focus] if focus else None,
                },
            )
        )
        repos_obs, traverse_obs = GraphProvider.observations_from_result(result)

        if not repos_obs.succeeded:
            recorder.evidence(
                "failed",
                f"The knowledge graph could not be queried: {repos_obs.error or 'unknown error'}.",
            )
            return InvestigationOutcome(
                observation="I couldn't reach the knowledge graph, so I have no architecture "
                "to reason about.",
                yielded=False,
            )

        indexed: list[dict[str, Any]] = result.data.get("indexed_repositories", [])
        unhealthy: list[dict[str, Any]] = result.data.get("unhealthy_repositories", [])
        components: list[dict[str, Any]] = result.data.get("components", [])
        topics: list[dict[str, Any]] = result.data.get("kafka_topics", [])

        # Two distinct records, because the underlying tool performs two
        # distinct operations that fail independently, and conflating them
        # fabricates evidence in both directions:
        #
        # - The repository list comes from Postgres and can succeed while Neo4j
        #   is down. Reporting that as "queried the knowledge graph" claims a
        #   graph traversal that never happened.
        # - Traversal can succeed and return nothing, which is an *indexing*
        #   problem, not a reachability one. Reporting that as a failure sends
        #   the user to check a connection that is fine.
        #
        # This one carries the action key, so it is what `attempted()` dedupes
        # on regardless of how traversal went.
        repos_evidence = recorder.evidence(
            "success",
            f"Looked up indexed repositories: {len(indexed)} found"
            + (f", scoped to '{focus}'." if focus else "."),
        )

        if focus is None and unhealthy:
            # A genuine survey (no repository named yet) — explain every
            # tracked-but-unusable repository once, deterministically, so
            # "0 indexed repositories" doesn't read as "nothing is tracked"
            # when the real, actionable story is "tracked, but re-index
            # required" (GRAPH_MISSING), "still indexing" (INDEXING), or
            # "never indexed" (NOT_INDEXED). A focused verify/scope action
            # gives the same explanation only for its own target — see
            # `_record_observations` — so this branch doesn't repeat it.
            for repo in unhealthy[:5]:
                recorder.evidence("unavailable", _describe_unhealthy(repo))

        repo_facts = [
            recorder.fact_once(
                "repository",
                str(repo.get("name", "")),
                value=dict(repo),
                evidence=repos_evidence,
            )
            for repo in indexed
            if repo.get("name")
        ]
        self._record_traversal(recorder, components, topics, traverse_obs, bool(indexed), focus)

        derived: dict[str, Any] = {}
        if components or topics:
            derived["graph_context_text"] = ContextBuilder().build([result]).context_text

        observation, ranked = self._record_observations(
            recorder,
            repo_facts,
            components,
            terms,
            focus,
            unhealthy,
            ticket_terms=action.params.get("ticket_terms"),
        )
        recorded_relationships = self._record_relationships(
            recorder, result.data.get("cross_repository_edges", []), repo_facts, repos_evidence
        )
        if recorded_relationships:
            # Purely descriptive: these are relationships *observed*, not
            # relationships *promoted* into suggestions — deciding which of
            # them become a suggested candidate is `capabilities.resync_
            # relationship_candidates`'s job, not this investigator's (ADR
            # 0010, invariant I1). This never claims more than was recorded.
            observation += (
                f" Found {len(recorded_relationships)} relationship(s) to other repositories "
                "in the knowledge graph."
            )
        if ranked and not action.params.get("corroboration_probe"):
            # The full relevance ordering of every indexed repository, best
            # first — distinct from the candidate shortlist. Planning consumes
            # this as a *ranking* (star ratings by position, `[0]` as the target
            # repository for its component-ownership verification), so it must
            # stay complete and deterministically ordered. Narrowing it to the
            # surviving candidates silently broke that verification: in a
            # genuine tie two repositories both looked like the target, which is
            # exactly the component-misattribution hole its regression test
            # guards.
            #
            # RFC-0011's new candidate-corroboration probes are excluded here
            # for the same underlying reason, from the opposite direction: a
            # probe's `focus` is a single *unconfirmed* candidate among
            # several, not "the" repository — letting it overwrite this
            # single-repo-first ordering would non-deterministically flip
            # which of several tied candidates Planning treats as the target,
            # depending on nothing more meaningful than which corroboration
            # probe happened to run last.
            derived["ranked_repositories"] = ranked
        return InvestigationOutcome(
            observation=observation,
            yielded=bool(repo_facts or components or topics),
            derived=derived,
        )

    def _record_traversal(
        self,
        recorder: Recorder,
        components: list[dict[str, Any]],
        topics: list[dict[str, Any]],
        traverse_obs: PlanningObservation,
        had_repositories: bool,
        focus: str | None,
    ) -> None:
        """Record the traversal attempt and, if it worked, its component/topic
        facts. Always records something, in one of three states:

        - `failed`  — traversal raised. This is what "Knowledge graph
          reachable" reads, so an unreachable Neo4j can never look like a
          successful query just because the repository list (which comes from
          Postgres) was readable.
        - `not_found` — traversal worked and the graph holds nothing for this
          request. The precise diagnosis behind an unindexed repository, and it
          can only be stated because it was recorded.
        - `success` — traversal worked and returned architecture.
        """
        # With no repositories there was nothing to traverse from, so the tool
        # reporting "succeeded" says nothing about whether Neo4j is reachable.
        # Treating that as a successful traversal would assert reachability we
        # never actually established.
        if not had_repositories:
            # `unavailable`, not `not_found`: no traversal was performed at all,
            # so this record must not read as "the graph answered and was
            # empty". Reachability is keyed on success/not_found precisely so
            # this case cannot assert a graph query we never made.
            recorder.evidence(
                "unavailable",
                "No indexed repositories to traverse, so no architecture could be read.",
                action=GRAPH_TRAVERSAL_ACTION,
            )
            return

        if not traverse_obs.succeeded:
            error = traverse_obs.error or ""
            if "hop budget" in error:
                # The agent hit its own manifest ceiling, not an infrastructure
                # problem. Recording this as `failed` marked the graph
                # unreachable and pointed the user at their Neo4j connection for
                # something entirely internal. `unavailable` says "no traversal
                # happened here" without impugning the graph.
                logger.warning("context_discovery_graph_budget_exhausted error=%s", error)
                recorder.evidence(
                    "unavailable",
                    "Reached this run's graph read budget, so no further traversal was performed.",
                    action=GRAPH_TRAVERSAL_ACTION,
                )
                return
            recorder.evidence(
                "failed",
                f"Graph traversal failed: {error or 'the architecture graph could not be read'}.",
                action=GRAPH_TRAVERSAL_ACTION,
            )
            return

        traverse_evidence = recorder.evidence(
            "success" if (components or topics) else "not_found",
            (
                f"Traversed the architecture graph: {len(components)} component(s), "
                f"{len(topics)} topic(s)."
                if (components or topics)
                else "Traversed the architecture graph and found no components or topics — "
                "nothing relevant is indexed for this request."
            ),
            action=GRAPH_TRAVERSAL_ACTION,
        )

        # Component/topic identity includes the owning repository: two services
        # can each have a `RetryHandler`, and they are not the same fact.
        for component in components:
            recorder.fact_once(
                "component",
                str(component.get("name", "")),
                value=dict(component),
                evidence=traverse_evidence,
                unique_on=("repository",),
            )
        for topic in topics:
            recorder.fact_once(
                "topic",
                str(topic.get("name", "")),
                value=dict(topic),
                evidence=traverse_evidence,
                unique_on=("repository",),
            )

    def _record_observations(
        self,
        recorder: Recorder,
        repo_facts: list[Any],
        components: list[dict[str, Any]],
        terms: list[str],
        focus: str | None,
        unhealthy: list[dict[str, Any]],
        ticket_terms: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """Record what this query observed about repository ranking —
        nothing more. Interpretation of what these observations mean for
        candidacy (explicit/suggested, single-leader-vs-tie) belongs
        exclusively to `capabilities.LEDGER_RESYNC_HOOKS` (ADR 0010,
        invariant I1) — this method writes only `Fact`s (via `recorder`,
        which has no way to write an `Inference` at all) and returns
        human-readable narration.

        `unhealthy` is only used to explain *why* nothing/no match was
        found (see `_describe_unhealthy`) — it never turns into a `Fact`
        here, so a GRAPH_MISSING/INDEXING/NOT_INDEXED repository can never
        become a repository candidate through this path.

        Returns `(observation, ranked_names)` — `ranked_names` is the full
        relevance ordering of every indexed repository, best first (or, for
        a focused query, the focused repository first and the rest stably
        ordered); Planning consumes this positionally regardless of what
        became a candidate.
        """
        unhealthy_by_name = {r.get("name", ""): r for r in unhealthy}

        if not repo_facts:
            if unhealthy_by_name:
                count = len(unhealthy_by_name)
                return (
                    "No repositories are indexed in the knowledge graph, so I can't tell "
                    f"which service this request belongs to. {count} repositor"
                    f"{'y is' if count == 1 else 'ies are'} tracked but not currently "
                    f"usable: {', '.join(sorted(unhealthy_by_name))}.",
                    [],
                )
            return (
                "No repositories are indexed in the knowledge graph, so I can't tell which "
                "service this request belongs to.",
                [],
            )

        by_name = {f.subject: f for f in repo_facts}

        # A verification/scoping query names its target explicitly: if the
        # graph confirms that repository exists, the `repository` fact
        # already recorded above *is* the observation — nothing further to
        # record here. No ranking is computed for a focused query, matching
        # what a focused query has always meant: "tell me about this one,"
        # not "rank everything."
        if focus is not None:
            match = by_name.get(focus)
            if match is None:
                unhealthy_match = unhealthy_by_name.get(focus)
                if unhealthy_match is not None:
                    # The claimed repository IS tracked — say exactly why it
                    # can't be used yet instead of the generic "isn't among
                    # the indexed repositories", which used to read
                    # identically whether the repository never existed at
                    # all or was one re-index away from working.
                    return _describe_unhealthy(unhealthy_match), []
                return (
                    f"'{focus}' isn't among the indexed repositories, so I can't confirm it — "
                    f"what I do have is: {', '.join(sorted(by_name))}.",
                    [],
                )
            # The confirmed repository leads the ordering; the rest keep a
            # stable position so Planning's per-repository star ranking still
            # covers every indexed repository.
            rest = sorted(n for n in by_name if n != focus)
            observation = (
                f"Confirmed '{focus}' is indexed, and traversed it for architecture."
                if components
                else f"Confirmed '{focus}' is indexed, but it has no architecture indexed yet."
            )
            return observation, [focus, *rest]

        if len(by_name) == 1:
            only = next(iter(by_name.values()))
            return (
                f"Only one repository is indexed — '{only.subject}' — so that's the one I'll use.",
                [only.subject],
            )

        # `rank_repositories` indexes component dicts by key (`name`, `type`).
        # Graph rows are external input, so a row missing a field must degrade
        # ranking, not abort an investigation whose facts are already recorded.
        try:
            scored = rank_repositories([dict(f.value) for f in repo_facts], components, terms)
        except (KeyError, TypeError):
            logger.warning(
                "context_discovery_ranking_skipped reason=malformed_component_rows count=%d",
                len(components),
            )
            scored = []

        ranked = [name for _score, name in scored]
        # Recorded even when nothing scored — `resync_ranked_candidates`
        # needs to see "we tried, nothing matched" as much as a real ranking,
        # and a fact is the only honest way to say that happened.
        recorder.fact(
            "repository_ranking",
            "ranking",
            value={
                "scored": [[score, name] for score, name in scored],
                # The request's own specific vocabulary, separate from the
                # generic capability terms folded into `terms` above —
                # `capabilities._corroborated_ranking_candidates` reads this
                # back to check fetched source content for something that
                # actually discriminates between candidates, not just
                # architecture shape every sibling scaffold shares.
                "ticket_terms": list(ticket_terms or []),
            },
        )

        if not scored or scored[0][0] <= 0:
            return (
                f"I found {len(by_name)} indexed repositories but nothing in the request "
                "matches any of them strongly enough for me to pick one.",
                ranked,
            )

        top_score = scored[0][0]
        leaders = [name for score, name in scored if score >= top_score * TIE_RATIO]
        if len(leaders) == 1:
            return f"'{leaders[0]}' is the clear match for this request.", ranked
        return (
            f"{len(leaders)} repositories score almost identically for this request: "
            f"{', '.join(leaders)}.",
            ranked,
        )

    @staticmethod
    def _relationship_reason(source_repo: str, rel_type: str, properties: dict[str, Any]) -> str:
        if rel_type == "CALLS_SERVICE":
            base = f"Called by {source_repo} via a Feign client."
        elif rel_type == "SHARES_TOPIC":
            topics = properties.get("topics") or []
            topic_text = ", ".join(f"'{t}'" for t in topics) if topics else "a Kafka topic"
            base = f"Shares Kafka topic {topic_text} with {source_repo}."
        elif rel_type == "DEPENDS_ON_REPOSITORY":
            base = f"{source_repo} declares a dependency matching this repository's name."
        elif rel_type == "IMPORTS_REPOSITORY":
            base = f"{source_repo}'s source code imports a module matching this repository's name."
        else:
            base = f"Related to {source_repo} in the knowledge graph."
        # RFC-0016 — surface the target's graph-wide architectural role
        # alongside the raw edge reason, so a report/synthesis reading this
        # fact's evidence trail sees *why* this one relationship carries
        # less identifying weight than a rare, specific edge would (a
        # generic, structural signal, never this specific repository or
        # capability by name).
        consumer_count = properties.get("target_consumer_count")
        if isinstance(consumer_count, int) and repository_role(consumer_count) == "shared_provider":
            base += (
                f" This target is shared infrastructure — {consumer_count} distinct "
                "repositories in the graph depend on it, so this edge alone is weak "
                "evidence for identifying any one of them."
            )
        return base

    def _record_relationships(
        self,
        recorder: Recorder,
        cross_repository_edges: list[dict[str, Any]],
        repo_facts: list[Any],
        repos_evidence: Any,
    ) -> list[str]:
        """Record a `repository_relationship` fact for *every* real
        cross-repository graph edge observed (see
        `app.indexer.graph.cross_repo_linker`), unconditionally — this sole
        renderer of `reason` text (`_relationship_reason`) stores it onto the
        fact so `capabilities.resync_relationship_candidates` never needs to
        recompute it or call back into this module.

        Deciding *which* of these ever becomes a suggested candidate (only
        ever from an already-explicit source, per ADR 0010 Theme A) is not
        this method's job — it happens later, in the resync hook, which can
        run on a cycle after this one recorded the fact. Recording every edge
        here regardless of whether its source is explicit *yet* is exactly
        what closes the ordering gap the original implementation had.
        """
        by_name = {f.subject: f for f in repo_facts}
        recorded: list[str] = []
        for edge in cross_repository_edges:
            source_repo = str(edge.get("source_repository", ""))
            target_repo = str(edge.get("target_repository", ""))
            if not source_repo or target_repo not in by_name:
                continue
            rel_type = str(edge.get("type", ""))
            properties = dict(edge.get("properties") or {})
            reason = self._relationship_reason(source_repo, rel_type, properties)
            fact = recorder.fact_once(
                "repository_relationship",
                target_repo,
                value={
                    "via": rel_type,
                    "source_repository": source_repo,
                    "reason": reason,
                    **properties,
                },
                evidence=repos_evidence,
                unique_on=("via", "source_repository"),
            )
            recorded.append(fact.subject)
        return recorded


class TestCoverageInvestigator:
    """Existing test coverage — TestRail-synced or CSV/Excel-uploaded test
    cases (same graph subtree, see app.indexer.graph.testrail_builder).

    Unlike the graph investigator above, this has no survey/scope/verify
    split: there is no "which project owns this" question to narrow down
    first, just one relevance-ranked search over whatever has been synced
    or uploaded. Proposes exactly once per session (the `attempted()`
    guard), the same shape `GraphInvestigator`'s initial survey uses.
    """

    name = "test_coverage"

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        if state.ledger.attempted(self.name, "survey_test_coverage"):
            return []
        return [
            InvestigationAction(
                provider=self.name,
                key="survey_test_coverage",
                intent="I'll check for existing TestRail or uploaded test cases relevant to "
                "this request.",
                targets="test_coverage",
                params={"search_terms": _search_terms(state)},
                cost=1,
            )
        ]

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        from app.context_pipeline.providers import TestCoverageProvider
        from app.graph.session import get_driver
        from app.graph.test_case_repository import Neo4jTestCaseGraphRepository

        terms: list[str] = action.params.get("search_terms") or []
        repo = session.test_case_graph_repo_override or Neo4jTestCaseGraphRepository(get_driver())
        provider = TestCoverageProvider(repo)

        try:
            cases, total_synced = await provider.retrieve(terms)
        except Exception as exc:
            recorder.evidence("failed", f"The test-case graph could not be queried: {exc}.")
            return InvestigationOutcome(
                observation="I couldn't reach the test-case graph, so I have no existing "
                "coverage to check against.",
                yielded=False,
            )

        if total_synced == 0:
            recorder.evidence(
                "not_found", "No TestRail or uploaded test cases have been synced yet."
            )
            return InvestigationOutcome(
                observation="No test cases have been synced from TestRail or uploaded yet, "
                "so there's no existing coverage to check.",
                yielded=False,
                derived={},
            )

        evidence = recorder.evidence(
            "success" if cases else "not_found",
            (
                f"Found {len(cases)} relevant test case(s) out of {total_synced} synced/uploaded."
                if cases
                else f"None of the {total_synced} synced/uploaded test case(s) matched this "
                "request."
            ),
        )
        for case in cases:
            recorder.fact_once(
                "test_case",
                str(case.get("title", "")),
                value=dict(case),
                evidence=evidence,
            )

        derived: dict[str, Any] = {}
        if cases:
            case_lines = [
                f"  {c['title']}" + (f" (refs: {c['refs']})" if c.get("refs") else "")
                for c in cases
            ]
            derived["test_coverage_text"] = (
                "**Existing test coverage relevant to this request**:\n" + "\n".join(case_lines)
            )

        return InvestigationOutcome(
            observation=(
                f"Found {len(cases)} existing test case(s) relevant to this request."
                if cases
                else f"{total_synced} test case(s) are synced, but none matched this request."
            ),
            yielded=bool(cases),
            derived=derived,
        )


def default_investigators() -> list[Any]:
    """The investigator set a discovery run uses, cheapest-first as a tiebreak
    hint only — the engine ranks by value, not by this order."""
    return [
        RequestParseInvestigator(),
        JiraInvestigator(),
        GraphInvestigator(),
        GitHubInvestigator(),
        ConfluenceInvestigator(),
        GoogleDriveInvestigator(),
        TestCoverageInvestigator(),
    ]


# ---------------------------------------------------------------------------
# Curation — the new stage that runs once, after the investigation loop
# exits (see engine.investigate), turning the flat `component` facts
# GraphInvestigator already gathered into a bounded, tiered
# `EvidencePackage` instead of a raw, unranked dump (see
# app.context_pipeline.reasoning.curation's own module docstring for the
# real bug this closes).
#
# Not an Investigator: it proposes nothing and never competes for a
# reasoning cycle's action slot — the necessity-ranked action selection
# in engine._select is for gathering facts, and this runs deterministically
# once gathering is already finished, over whatever facts exist by then.
# ---------------------------------------------------------------------------

_NEIGHBORHOOD_EDGE_TYPES = ("CALLS", "IMPORTS", "INHERITS_FROM", "CONTAINS")
_NEIGHBORHOOD_MAX_HOPS = 2


def _primary_repository(state: WorkingContext) -> str | None:
    """The single repository the bounded-neighborhood fetch is scoped to
    — the redesign's own "Primary Repository: 1" concept, not every
    repository the request touches. Prefers an explicit/live candidate
    (ADR 0010) in ranked order; falls back to the top of the raw
    relevance ranking when no candidate has resolved yet.

    Candidate order is preserved from the ledger (insertion order, via
    `dict.fromkeys` — not a `set`, whose iteration order is not something
    this codebase should ever depend on): this function must return the
    same repository on every call over the same state, matching the
    engine's own deterministic-and-reproducible design (see engine.py's
    module docstring on `_select` never being an LLM call for the same
    reason). A `set`-backed fallback here would pick a repository whose
    identity varies across process restarts (Python's string hashing is
    randomized per-process) whenever no ranking covers any candidate —
    rare, but a real reproducibility bug once it does happen.
    """
    candidates = list(
        dict.fromkeys(i.statement for i in state.ledger.live_inferences("repository_candidate"))
    )
    ranked: list[str] = state.derived.get("ranked_repositories") or []
    for name in ranked:
        if name in candidates:
            return name
    if candidates:
        return candidates[0]
    return ranked[0] if ranked else None


def _target_repositories(state: WorkingContext) -> list[str]:
    """Every repository under consideration — used for the ownership
    bonus in `curate()`, deliberately broader than `_primary_repository`
    (a component in a *related*, non-primary repository can still score
    as an architecture dependency, it just never seeds the neighborhood
    traversal itself)."""
    return [i.statement for i in state.ledger.live_inferences("repository_candidate")]


async def curate_evidence(state: WorkingContext, session: SessionContext) -> None:
    """Compute `state.derived["evidence_package"]` from the component
    facts already gathered — see the module-level comment above for why
    this runs once, deterministically, rather than as another proposed
    investigation action.

    Degrades gracefully, never raises: a failed or skipped neighborhood
    fetch (no primary repository identified yet, no anchor found, the
    graph read itself failing) still produces a valid `EvidencePackage`
    — relevance/repository-ownership/test-penalty scoring alone, with
    every component's `proximity_score` at 0 rather than a fabricated
    value. An empty anchor set is a real, honest outcome (see
    `select_anchor_ids`'s own docstring) — nothing here manufactures a
    neighborhood to compensate for one not being found.

    Also records Runtime Execution Discovery's `call_edge` facts (RFC-004
    Capability 1, shadow mode) from this same neighborhood fetch — see the
    inline comment at that call site. This is additive and read by nothing
    yet: `EvidencePackage`'s construction below is unaffected regardless of
    whether any `CALLS` edge was found.
    """
    from app.context_pipeline.reasoning.curation import EvidencePackage, curate, select_anchor_ids
    from app.context_pipeline.reasoning.runtime_execution import build_call_chains
    from app.graph.hop_budget import GraphHopBudgetExceeded
    from app.graph.neo4j_repository import Neo4jGraphRepository
    from app.graph.session import get_driver

    components = [dict(f.value) for f in state.ledger.facts_of("component")]
    if not components:
        state.derived["evidence_package"] = EvidencePackage().model_dump()
        return

    enriched_text = state.derived.get("enriched_text", "")
    target_repos = _target_repositories(state)
    primary_repository = _primary_repository(state)

    neighborhood_nodes: list[dict[str, Any]] = []
    if primary_repository:
        anchor_ids = select_anchor_ids(components, enriched_text, primary_repository)
        if anchor_ids:
            repository_id = anchor_ids[0].split(":", 1)[0]
            graph_repo = session.graph_repo_override or Neo4jGraphRepository(get_driver())
            try:
                payload = await graph_repo.get_neighborhood(
                    repository_id,
                    anchor_ids,
                    list(_NEIGHBORHOOD_EDGE_TYPES),
                    _NEIGHBORHOOD_MAX_HOPS,
                )
                neighborhood_nodes = [
                    {"id": node.id, "hop_distance": node.properties.get("hop_distance")}
                    for node in payload.nodes
                ]
                state.ledger.add_evidence(
                    provider="graph",
                    action="get_neighborhood",
                    outcome="success" if neighborhood_nodes else "not_found",
                    summary=(
                        f"Traversed {_NEIGHBORHOOD_MAX_HOPS} hops from {len(anchor_ids)} "
                        f"component(s) named in the request — found {len(neighborhood_nodes)} "
                        f"connected component(s) in '{primary_repository}'."
                    ),
                    iteration=state.metadata.iteration,
                )

                # Runtime Execution Discovery (RFC-004 Capability 1, shadow
                # mode — see the Phase 1a Execution Plan). Reuses the exact
                # same neighborhood `payload` already fetched above: no
                # second query, no new GraphPayload, no new traversal
                # algorithm — `build_call_chains` is Commit 3's frozen
                # library, called here unchanged. Output is Ledger facts
                # only; nothing below reads `state.derived["evidence_
                # package"]`, no capability is registered, and nothing
                # downstream (Planning, Engineering Understanding, capability
                # scoring, readiness) consumes a "call_edge" fact yet.
                call_chains = build_call_chains(
                    payload, anchor_ids, max_depth=_NEIGHBORHOOD_MAX_HOPS
                )
                chains_with_steps = [c for c in call_chains if c.steps]
                call_edge_evidence = state.ledger.add_evidence(
                    provider="graph",
                    action="build_call_chains",
                    outcome="success" if chains_with_steps else "not_found",
                    summary=(
                        f"Reconstructed call chains from {len(anchor_ids)} component(s) — "
                        f"{len(chains_with_steps)} produced at least one CALLS edge."
                    ),
                    iteration=state.metadata.iteration,
                )
                for chain in call_chains:
                    state.ledger.add_fact(
                        kind="call_edge",
                        subject=chain.entry_point,
                        provider="graph",
                        evidence_id=call_edge_evidence.evidence_id,
                        value=chain.model_dump(mode="json"),
                        iteration=state.metadata.iteration,
                    )
            except GraphHopBudgetExceeded:
                # RFC-0028 — the agent hit its own manifest ceiling, not an
                # infrastructure problem. Reuses the same `unavailable`
                # classification `GraphInvestigator._record_traversal`
                # already applies to this exact exception (see its "hop
                # budget" branch) rather than inventing a second taxonomy:
                # recording this as `failed` would mark the graph
                # unreachable for the rest of the run and point the
                # confidence explanation at "check the Neo4j connection" for
                # something entirely internal and, in practice, often
                # resolved by a later retry within the same run (e.g. a
                # fresh pass after a clarification answer gets a fresh
                # budget) — see `_latest_graph_evidence`'s docstring in
                # capabilities.py for how that later success supersedes
                # this record in the final explanation without deleting it.
                logger.warning(
                    "context_discovery_curation_neighborhood_budget_exhausted repository=%s",
                    primary_repository,
                )
                state.ledger.add_evidence(
                    provider="graph",
                    action="get_neighborhood",
                    outcome="unavailable",
                    summary=(
                        f"Reached this run's graph read budget before the neighborhood "
                        f"around '{primary_repository}' could be traversed."
                    ),
                    iteration=state.metadata.iteration,
                )
            except Exception:
                logger.exception(
                    "context_discovery_curation_neighborhood_failed repository=%s",
                    primary_repository,
                )
                state.ledger.add_evidence(
                    provider="graph",
                    action="get_neighborhood",
                    outcome="failed",
                    summary=(
                        f"Could not traverse the architecture graph around '{primary_repository}'."
                    ),
                    iteration=state.metadata.iteration,
                )

    # RFC-0033 — whatever source content this run already fetched (for
    # source-selection/dependency-expansion, RFC-0022/0027), keyed for
    # curate()'s optional excerpt-attachment. Built here, not inside
    # curate(), because this is the one place with ledger access;
    # curate() itself stays a pure function of plain data.
    source_file_texts = {
        (str(f.value.get("repository", "")), str(f.value.get("path", ""))): f.text
        for f in state.ledger.facts_of("source_file")
        if f.text
    }

    package = curate(
        components=components,
        neighborhood_nodes=neighborhood_nodes,
        enriched_text=enriched_text,
        target_repositories=target_repos,
        source_file_texts=source_file_texts,
    )
    state.derived["evidence_package"] = package.model_dump()
