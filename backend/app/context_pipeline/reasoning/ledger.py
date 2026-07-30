"""The evidence ledger — facts, the investigations that produced them, and
the interpretations layered on top.

The central rule this module exists to enforce: **facts and interpretation
are different kinds of thing and never share a container.**

- An `EvidenceRecord` is a record of *an investigation that happened*: a
  provider was asked something and produced a specific outcome. It exists
  whether or not anything useful came back — "Confluence is not connected"
  is evidence, and the reason discovery can later explain what it searched
  rather than only what it found.
- A `Fact` is a single atomic thing discovered to be true, and it always
  carries `evidence_id` pointing at the investigation that established it.
  There is no way to add a fact without saying where it came from — the
  constructor requires it.
- An `Inference` is an interpretation: an assumption, a ranked candidate,
  a conclusion about architecture. It always carries
  `supporting_fact_ids`, so "why do you believe this" is answerable by
  walking to facts and then to evidence, with no second reasoning pass.

Everything is append-only. Discovery may supersede an inference (mark it
`withdrawn`) but never rewrites history, because the transcript and the
confidence explanations both cite this ledger by id.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# What an investigation attempt concluded. Deliberately mirrors
# `Evidence.status` on the agent contract (app.agents._contract) so
# projecting a ledger into contract Evidence needs no translation table.
Outcome = Literal["success", "not_found", "unavailable", "failed"]

# The kinds of fact discovery can establish. A closed vocabulary rather
# than free-form strings, because `capabilities.py` evaluates confidence
# signals by asking the ledger for facts of a specific kind — a typo in a
# fact kind would silently mean "this capability is unsupported" instead of
# failing loudly.
FactKind = Literal[
    # An external reference recognized in the request text (a Jira key, a
    # GitHub PR URL, a repository name). Produced by parsing, not retrieval.
    "reference",
    # A work item (Jira issue) actually fetched, with its real content.
    "work_item",
    # A documentation page actually retrieved.
    "document",
    # A pull request / GitHub issue actually retrieved.
    "pull_request",
    # A repository that exists and is indexed in the knowledge graph.
    "repository",
    # An architecture component discovered by graph traversal.
    "component",
    # A Kafka topic discovered by graph traversal.
    "topic",
    # Something the human asserted. A claim, NOT a verified fact — see
    # `Fact.verified` and engine.py's verify-then-resolve flow.
    "user_statement",
]

InferenceKind = Literal[
    # Something inferred rather than observed, that discovery is proceeding on
    # anyway — surfaced to the user precisely because it is not a fact.
    "assumption",
    # A repository discovery believes the work could belong to.
    "repository_candidate",
]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class EvidenceRecord(BaseModel):
    """One investigation that actually happened.

    `action` names what was attempted in provider-agnostic terms
    ("fetch_work_item", "traverse_architecture_graph") and `outcome` says
    how it went. A `not_found`/`unavailable` record is just as important as
    a successful one: it is what lets discovery say "I searched Confluence
    and it isn't connected" instead of staying silent about a source it
    never reached.
    """

    evidence_id: str = Field(default_factory=lambda: _new_id("ev"))
    provider: str
    action: str
    outcome: Outcome
    summary: str
    # Which reasoning cycle ran this. Makes the investigation replayable in
    # order and lets the UI group "what happened in round 2".
    iteration: int = 0
    # Human-readable statement of *why the engine chose to do this*, copied
    # from the InvestigationAction it came from. Without this, an evidence
    # trail explains what happened but not what the engine was trying to
    # learn — which is most of what makes an investigation legible.
    intent: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome == "success"


class Fact(BaseModel):
    """One atomic discovered thing, permanently tied to its evidence.

    `subject` is the natural key a human would recognize ("payment-service",
    "PROT-123") and is what confidence explanations and clarification
    options are rendered from. `value` carries the structured payload; `text`
    carries retrieved prose (a ticket description, a doc body) already
    redacted and untrusted-wrapped by the investigator that produced it, so
    projection can concatenate it into the planning prompt without
    re-sanitizing.

    `verified` is False only for `user_statement` facts that haven't been
    corroborated yet. Every retrieved fact is verified by construction — it
    came from a provider that returned it. This flag is what makes
    "the user said X" structurally distinguishable from "X is true", which
    is the distinction the old design collapsed.
    """

    fact_id: str = Field(default_factory=lambda: _new_id("f"))
    kind: FactKind
    subject: str
    provider: str
    evidence_id: str
    value: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    iteration: int = 0
    verified: bool = True


class Inference(BaseModel):
    """An interpretation of facts — never itself a fact.

    Must cite `supporting_fact_ids`. An inference with no supporting facts
    is a hallucination by definition, and `Ledger.add_inference` rejects
    it rather than storing it.

    `withdrawn` marks an inference later evidence contradicted. It is kept
    rather than deleted so the transcript's "I assumed X, then found out
    otherwise" remains readable.
    """

    inference_id: str = Field(default_factory=lambda: _new_id("i"))
    kind: InferenceKind
    statement: str
    supporting_fact_ids: list[str] = Field(default_factory=list)
    iteration: int = 0
    withdrawn: bool = False


class Ledger(BaseModel):
    """Append-only store of everything discovery has established, plus the
    trail explaining how.

    This is the sole source of truth for confidence: `capabilities.py`
    computes every capability score by querying facts here, so a score can
    always be traced to specific facts and from there to specific
    investigations. Nothing else in the engine is allowed to hold retrieved
    knowledge.
    """

    evidence: list[EvidenceRecord] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    inferences: list[Inference] = Field(default_factory=list)

    # -- writes ------------------------------------------------------------

    def add_evidence(
        self,
        *,
        provider: str,
        action: str,
        outcome: Outcome,
        summary: str,
        iteration: int = 0,
        intent: str = "",
    ) -> EvidenceRecord:
        record = EvidenceRecord(
            provider=provider,
            action=action,
            outcome=outcome,
            summary=summary,
            iteration=iteration,
            intent=intent,
        )
        self.evidence.append(record)
        return record

    def add_fact(
        self,
        *,
        kind: FactKind,
        subject: str,
        provider: str,
        evidence_id: str,
        value: dict[str, Any] | None = None,
        text: str = "",
        iteration: int = 0,
        verified: bool = True,
    ) -> Fact:
        """Record a discovered fact. `evidence_id` is required and must name
        an evidence record already in this ledger — a fact whose provenance
        can't be resolved is exactly what this architecture exists to make
        impossible, so this raises rather than storing an orphan."""
        if not any(e.evidence_id == evidence_id for e in self.evidence):
            raise ValueError(
                f"Cannot add fact {subject!r}: evidence_id {evidence_id!r} is not in this ledger."
            )
        fact = Fact(
            kind=kind,
            subject=subject,
            provider=provider,
            evidence_id=evidence_id,
            value=value or {},
            text=text,
            iteration=iteration,
            verified=verified,
        )
        self.facts.append(fact)
        return fact

    def add_inference(
        self,
        *,
        kind: InferenceKind,
        statement: str,
        supporting_fact_ids: list[str],
        iteration: int = 0,
    ) -> Inference:
        """Record an interpretation. Requires at least one supporting fact —
        an uncited interpretation is an unsupported assumption, and the whole
        point of separating inference from fact is that those can't be
        stored as if they were knowledge."""
        if not supporting_fact_ids:
            raise ValueError(
                f"Cannot add inference {statement!r} with no supporting facts — "
                "every interpretation must cite the facts it rests on."
            )
        inference = Inference(
            kind=kind,
            statement=statement,
            supporting_fact_ids=list(supporting_fact_ids),
            iteration=iteration,
        )
        self.inferences.append(inference)
        return inference

    def withdraw_inferences(self, kind: InferenceKind) -> None:
        """Mark every live inference of `kind` withdrawn. Called when new
        evidence makes a previous interpretation obsolete (e.g. a scoped
        graph traversal replaces the repository candidates a broad one
        produced) — superseded, not erased."""
        for inference in self.inferences:
            if inference.kind == kind and not inference.withdrawn:
                inference.withdrawn = True

    # -- reads -------------------------------------------------------------

    def facts_of(self, *kinds: FactKind, verified_only: bool = True) -> list[Fact]:
        return [f for f in self.facts if f.kind in kinds and (f.verified or not verified_only)]

    def has_fact(self, *kinds: FactKind) -> bool:
        return bool(self.facts_of(*kinds))

    def subjects_of(self, *kinds: FactKind) -> list[str]:
        """Distinct fact subjects, in discovery order — e.g. every indexed
        repository name. Order is stable so clarification options and
        confidence explanations don't reshuffle between cycles."""
        seen: list[str] = []
        for fact in self.facts_of(*kinds):
            if fact.subject not in seen:
                seen.append(fact.subject)
        return seen

    def evidence_for(self, *kinds: FactKind) -> list[str]:
        """Evidence ids backing every fact of the given kinds — what a
        confidence signal cites to justify itself."""
        ids: list[str] = []
        for fact in self.facts_of(*kinds):
            if fact.evidence_id not in ids:
                ids.append(fact.evidence_id)
        return ids

    def live_inferences(self, kind: InferenceKind) -> list[Inference]:
        return [i for i in self.inferences if i.kind == kind and not i.withdrawn]

    def attempted(self, provider: str, action: str) -> bool:
        """Whether this exact investigation has already been run. The
        engine's guard against proposing the same action forever, and half
        of how "providers are exhausted" is determined."""
        return any(e.provider == provider and e.action == action for e in self.evidence)

    def evidence_by_id(self, evidence_id: str) -> EvidenceRecord | None:
        return next((e for e in self.evidence if e.evidence_id == evidence_id), None)

    def explain_fact(self, fact_id: str) -> list[EvidenceRecord]:
        """The evidence trail behind one fact — the primitive the UI's
        "why do you believe this?" affordance is built on."""
        fact = next((f for f in self.facts if f.fact_id == fact_id), None)
        if fact is None:
            return []
        record = self.evidence_by_id(fact.evidence_id)
        return [record] if record is not None else []
