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
from app.context_pipeline.reasoning.capabilities import GRAPH_TRAVERSAL_ACTION
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    InvestigationOutcome,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.memory import WorkingContext
from app.context_pipeline.reference_detection import detect_references

logger = logging.getLogger(__name__)

# A runner-up repository within this fraction of the leader's relevance score
# is treated as equally plausible, so both survive as candidates and the
# `repository` capability's "Owning repository identified" signal stays
# unsatisfied. Ambiguity is therefore represented as *two live inferences*,
# not as a special-cased ambiguity flag — nothing downstream needs to know
# the concept exists.
_TIE_RATIO = 0.9

_REPO_REFERENCE_TYPES = ("local_repository", "github_repository")


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
        known_repos: frozenset[str] = action.params.get("known_repositories", frozenset())
        existing: set[str] = action.params.get("existing", set())

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
            recorder.fact(
                "reference",
                ref.normalized_value,
                value={
                    "type": ref.type.value,
                    "provider": ref.provider,
                    "confidence": ref.confidence,
                    "raw_value": ref.raw_value,
                    "normalized_value": ref.normalized_value,
                },
            )

        described = ", ".join(f"{r.normalized_value}" for r in fresh)
        return InvestigationOutcome(
            observation=f"I found {len(fresh)} reference(s) I can resolve: {described}.",
            yielded=True,
        )


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


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
        recorder.fact(
            "work_item",
            reference.normalized_value,
            value={"title": artifact.title},
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
        provider = ConfluenceProvider(session.db)
        artifact = await provider.resolve_for_issue(
            jira_issue_key=work_item,
            task_description=action.params.get("task_description", work_item),
            model=session.model,
            stage=session.stage,
        )

        if artifact is None or not artifact.text:
            # The provider distinguishes "reached Confluence, nothing
            # relevant" from "Confluence isn't connected" via evidence.status;
            # preserving that distinction is what lets the explanation say
            # which of the two actually happened.
            status = (artifact.evidence.status if artifact else None) or "unavailable"
            outcome = "not_found" if status == "not_found" else "unavailable"
            summary = (
                f"Searched Confluence from {work_item} and found no relevant pages."
                if outcome == "not_found"
                else "Confluence is not connected, so no documentation could be searched."
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
        return actions

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
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
            observation=f"I read '{artifact.title}' from Google Drive for the surrounding "
            "context.",
            yielded=True,
        )


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------


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
        for gap in state.gaps:
            if gap.status != "claimed" or not gap.user_claim:
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
                    },
                    cost=1,
                )
            )
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

        observation, ranked = self._reassess_candidates(
            recorder, repo_facts, components, terms, focus
        )
        if ranked:
            # The full relevance ordering of every indexed repository, best
            # first — distinct from the candidate shortlist. Planning consumes
            # this as a *ranking* (star ratings by position, `[0]` as the target
            # repository for its component-ownership verification), so it must
            # stay complete and deterministically ordered. Narrowing it to the
            # surviving candidates silently broke that verification: in a
            # genuine tie two repositories both looked like the target, which is
            # exactly the component-misattribution hole its regression test
            # guards.
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
                    "Reached this run's graph read budget, so no further traversal was "
                    "performed.",
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

    def _reassess_candidates(
        self,
        recorder: Recorder,
        repo_facts: list[Any],
        components: list[dict[str, Any]],
        terms: list[str],
        focus: str | None,
    ) -> tuple[str, list[str]]:
        """Re-derive which repositories are plausible implementation sites.

        Candidates are *inferences* citing repository facts, never facts
        themselves — "this is where the work probably goes" is an
        interpretation, and keeping it on the inference side of the ledger is
        what stops it from being read back later as established truth.

        Ambiguity needs no special representation: two surviving candidates
        leave `repository`'s "Owning repository identified" signal
        unsatisfied, which is what eventually produces the question. One
        surviving candidate satisfies it.
        """
        # Superseded, not deleted — a later scoped query replaces an earlier
        # broad query's shortlist.
        recorder.withdraw("repository_candidate")

        if not repo_facts:
            return (
                "No repositories are indexed in the knowledge graph, so I can't tell which "
                "service this request belongs to.",
                [],
            )

        by_name = {f.subject: f for f in repo_facts}

        # A verification/scoping query names its target explicitly: if the
        # graph confirms that repository exists, it *is* the candidate, cited
        # to the fact proving it exists. No ranking heuristic needed, and no
        # fabricated fallback when the name isn't there.
        if focus is not None:
            match = by_name.get(focus)
            if match is None:
                return (
                    f"'{focus}' isn't among the indexed repositories, so I can't confirm it — "
                    f"what I do have is: {', '.join(sorted(by_name))}.",
                    [],
                )
            recorder.inference("repository_candidate", focus, [match])
            # The confirmed repository leads the ordering; the rest keep a
            # stable position so Planning's per-repository star ranking still
            # covers every indexed repository.
            rest = sorted(n for n in by_name if n != focus)
            # Say what the traversal actually returned. "Traversed it for
            # architecture" read as success even when the repository turned out
            # to hold nothing, which is the one case the user most needs to know
            # about.
            observation = (
                f"Confirmed '{focus}' is indexed, and traversed it for architecture."
                if components
                else f"Confirmed '{focus}' is indexed, but it has no architecture indexed yet."
            )
            return observation, [focus, *rest]

        if len(by_name) == 1:
            only = next(iter(by_name.values()))
            recorder.inference("repository_candidate", only.subject, [only])
            # Reasoning from absence, not from a match: nothing in the request
            # actually pointed at this repository, it is simply the only one
            # indexed. That is an assumption and is recorded as one, so the user
            # sees it stated rather than discovering later that "the repository"
            # was a default.
            recorder.inference(
                "assumption",
                f"This work belongs to '{only.subject}' — it is the only indexed repository, "
                "not something the request named.",
                [only],
            )
            return (
                f"Only one repository is indexed — '{only.subject}' — "
                "so that's the one I'll use.",
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
        if not scored or scored[0][0] <= 0:
            # Nothing in the request matched anything in the graph. Honest
            # outcome: no *candidate* at all, rather than arbitrarily promoting
            # whichever repository happened to sort first. The ranking is still
            # returned — it is an ordering, not a claim of ownership.
            return (
                f"I found {len(by_name)} indexed repositories but nothing in the request "
                "matches any of them strongly enough for me to pick one.",
                ranked,
            )

        top_score = scored[0][0]
        leaders = [name for score, name in scored if score >= top_score * _TIE_RATIO]
        for name in leaders:
            fact = by_name.get(name)
            if fact is not None:
                recorder.inference("repository_candidate", name, [fact])

        if len(leaders) == 1:
            leader_fact = by_name.get(leaders[0])
            if leader_fact is not None and not any(
                f.subject == leaders[0]
                for f in recorder.facts_of("reference")
                if f.value.get("type") in _REPO_REFERENCE_TYPES
            ):
                # Chosen by component-name relevance, not because the request
                # named it. Worth stating: relevance ranking is a heuristic, and
                # a user who disagrees can only push back if they know it was a
                # judgement rather than a lookup.
                recorder.inference(
                    "assumption",
                    f"This work belongs to '{leaders[0]}' — inferred from how closely its "
                    "components match the request, which did not name a repository.",
                    [leader_fact],
                )
            return f"'{leaders[0]}' is the clear match for this request.", ranked
        return (
            f"{len(leaders)} repositories score almost identically for this request: "
            f"{', '.join(leaders)}.",
            ranked,
        )


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
                else f"None of the {total_synced} synced/uploaded test case(s) matched this request."
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
