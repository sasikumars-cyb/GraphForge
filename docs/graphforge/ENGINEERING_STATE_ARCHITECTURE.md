# GraphForge — Engineering State Architecture Contract

**Status:** NORMATIVE. Accepted. This document is a canonical architecture
contract, not a proposal, not a discussion, and not one design option among
several.

> **This document is the canonical normative specification for GraphForge
> Engineering State.**
>
> Any implementation, agent behavior, architectural decision, or future
> design proposal concerning Engineering State — including Evidence,
> Observation, Belief, Hypothesis, Assumption, Prediction, Decision, Plan,
> Human authority, Execution Authorization, multi-agent coordination, event
> sourcing, or state reconstruction — **MUST conform to this contract.**
>
> **If existing code conflicts with this contract, the code is considered
> non-conforming — not the contract.** Do not silently weaken this contract
> to accommodate existing implementation, existing schemas, existing APIs,
> or model limitations.
>
> **Any proposed change to this contract requires an explicit architectural
> decision and an update to this document.** No agent, session, or
> individual contributor may reinterpret, narrow, or silently supersede it
> by writing code, other documentation, or a differing informal description
> elsewhere in the repository.

---

## 0. How this document relates to the rest of GraphForge's documentation

**Authority hierarchy** (highest to lowest):

**Two peer canonical contracts exist alongside this one:**
[`REASONING_ENGINE_ARCHITECTURE.md`](REASONING_ENGINE_ARCHITECTURE.md)
(canonical for reasoning, ActionProposal, action selection, reasoning
cycles) and
[`CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md`](CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md)
(canonical for the Capability model, capability registration and promotion,
the Control Plane's validation and authorization pipeline, Authorization
Grants, and the proposal→verified state ladder). Both build directly on this
document and neither relaxes any invariant here, but neither is subordinate;
read all three before any decision touching how reasoning or execution reads
or writes Engineering State.

```text
This document (Engineering State Architecture Contract)
        — canonical, normative, for everything it covers
        ↓
docs/graphforge/*.md (ARCHITECTURE.md, AGENT_FRAMEWORK.md, PRODUCT_VISION.md,
ROADMAP.md, API_CONTRACTS.md, ...) — target-design documents for the rest
of the system; where any of these describes Engineering-State-adjacent
concepts (e.g. ARCHITECTURE.md's "Shared Memory" / RunContext section),
this contract wins on any conflict
        ↓
docs/adr/*.md — point-in-time architecture decision records; an ADR that
touches Engineering-State-shaped concepts (Belief, Hypothesis, Evidence,
event/state modeling) documents a decision made *before* this contract
existed and MUST be read as historical unless and until it is explicitly
reconciled with this contract via a new ADR (see §9 below for the two
known cases)
        ↓
docs/rfcs/*.md — implementation records for a specific ADR/architecture
baseline; same rule as ADRs
        ↓
docs/handbook/*.md, docs/presentation/*.md — defense/summary material,
derivative of the above, never authoritative on its own
        ↓
Implementation (backend/app/, frontend/src/)
        ↓
Tests
```

**There is exactly one canonical Engineering State specification: this
document.** No other file in this repository may claim to independently
define Engineering State, Belief, Hypothesis, Evidence-as-a-general-concept,
event sourcing, state replay, human-approval semantics, or
Execution-Authorization semantics. Where another document already contains
material touching these concepts, it is either (a) a historical record of a
decision made before this contract, cross-referenced from here and from it
back to here, or (b) wrong, and must be corrected to defer to this document
rather than maintained as a second, competing definition.

### Known pre-existing terminology collisions (see §9 for detail)

Two things already implemented in this repository use terminology this
contract also uses, for narrower or different purposes. **Neither is
automatically conformant, and neither has been silently reconciled by
writing this document:**

1. **RFC-001 / the `EngineeringSession` aggregate** (`app/models/belief.py`,
   `app/models/contradiction.py`, etc.) implements `Belief`, `Hypothesis`,
   `Evidence`, `Decision`, `Contradiction` as real, tested, in-place-mutated
   Postgres rows ("A Belief is 'mutated in place, never versioned'" — RFC-001
   §3.2). This directly conflicts with §6/§8/§18 of this contract, which
   require Belief confidence to be derived from an append-only Evidence
   trail, never manually mutated or stored as an independently-edited value.
   **This is an open, flagged conflict, not resolved by this document.**
2. **ADR 0018's Evidence → Hypothesis → Validation → Confidence → Knowledge
   pipeline** (`app/knowledge_engine/`) implements its own `Hypothesis`/
   `Evidence` classes, scoped narrowly to cross-repository knowledge
   derivation, content-addressed and immutable once generated. This is
   structurally closer to this contract's model than RFC-001's is, but it is
   a **specific implemented pipeline**, not a general Engineering State
   substrate, and MUST NOT be assumed to already satisfy this contract's
   general Belief/Hypothesis requirements (§3, §6, §8) without an explicit
   reconciliation decision.

Resolving either collision (rename, merge, or formally scope one as a
specialization of the other) requires the architectural-decision process in
§0's own authority statement above — it is explicitly out of scope for this
document to resolve unilaterally, per the instruction that produced it.

---

## Preventing architectural drift

If work on this repository — implementation, a new ADR, a new design
document, or an agent's own reasoning — appears to conflict with this
contract, the required response is:

1. **Identify the conflict explicitly.** State which invariant (§18/§21) or
   which section is in tension with what the code or the other document
   does.
2. **Do not silently reinterpret the contract** to make the conflict
   disappear. A weaker reading that happens to make existing code compliant
   is not a valid resolution.
3. **Do not weaken the invariant to make implementation easier.** See "Do
   Not Weaken," immediately below — this is non-negotiable.
4. **Explicitly name the architectural decision required** to resolve the
   conflict (e.g., "RFC-001's Belief model needs to be reconciled with §6/§8
   of this contract — options are: migrate RFC-001's Belief to an
   event-derived model, or formally scope RFC-001's Belief as a different,
   narrower concept with a different name").
5. **Update this contract only through that explicit decision**, recorded
   the same way any other change to this document must be — never as a
   side effect of an unrelated code change or documentation edit.

### Do Not Weaken

Do **not** weaken, narrow, or quietly reinterpret an Engineering State
invariant in this document because:

- the current implementation is simpler without it,
- the database schema makes it inconvenient,
- an existing API does not support it,
- a model cannot currently reason about it,
- migration would be difficult,
- tests are difficult to write for it, or
- the implementation would be faster without it.

**Implementation complexity is not a reason to change the architecture.
Architecture comes first.** If an invariant is genuinely wrong — not merely
inconvenient — the correction happens through the explicit
architectural-decision process above, argued on architectural grounds, not
implementation convenience.

---

# The Contract

*(Everything below this line is the complete, unabridged accepted
Engineering State Architecture Contract. It has not been shortened,
summarized, or altered for implementation convenience.)*

## 1. Purpose and scope

This contract defines the domain architecture of **Engineering State**: what
it is, what it owns, what it does not own, and the invariants any
implementation MUST satisfy. It is independent of programming language,
storage technology, and deployment topology. It does not define database
schemas, APIs, or class structures. Two independent engineering teams
implementing this contract MUST arrive at behaviorally compatible systems
with respect to every invariant in §18/§21.

This contract governs the state of a single Reasoning Engine's pursuit of a
Goal, and the multi-agent extensions where more than one Role pursues
related Goals cooperatively. It does not govern Knowledge's internal
storage mechanics, Workspace's internal execution mechanics, or Policy's
rule-authoring format — it defines only the **boundary contracts** those
systems MUST honor when interacting with Engineering State.

## 2. Engineering State vs. Knowledge

**Ownership boundary.** Engineering State owns facts about **this task's
reasoning process**: what was observed, believed, decided, attempted, and
communicated by a human, in pursuit of one Goal. Knowledge owns facts about
**the software system and its environment**, independent of any task, valid
on their own terms across tasks and time.

A fact belongs to Engineering State if it answers "what did this task
do/believe/decide." A fact belongs to Knowledge if it answers "what is true
about the system," independent of whether any task ever asked.

**Promotion (Engineering State → Knowledge).** An Observation made during a
task MAY be promoted into Knowledge only when:
- it describes a durable, reusable property of the system (not a
  task-specific artifact or transient condition), AND
- it has been corroborated — either by an independent, deterministic
  Capability (e.g., an indexer re-confirming a structural fact) or by two or
  more independently-produced Observations converging on the same
  conclusion, AND
- the promotion is performed by a designated Knowledge-writing Capability,
  never directly by the Reasoning Engine.

A promoted fact MUST retain a backward reference to the originating
Observation(s) as its provenance. Promotion MUST NOT occur for facts whose
origin_class is `repository_content` interpreted by an LLM without
independent corroboration (see §15).

**Demotion / staleness.** A Knowledge fact's temporal validity (§5) MUST be
re-evaluated whenever:
- the repository revision it was derived from is superseded, or
- a later Observation, made in the course of any task, contradicts it.

A stale or contradicted Knowledge fact MUST NOT be silently deleted. It
MUST be marked with reduced confidence or superseded status, retaining its
history, so a later query can see both the current best understanding and
what was previously believed.

**Authority boundary.** Knowledge is authoritative about the system state
it has independently observed or inferred, graded by its own epistemic
model (§5) — it is NOT a single-tier ground truth. Engineering State is
authoritative about this task's own reasoning trace only — it is NEVER
authoritative about the system itself, only about what this task concluded
about the system.

## 3. Epistemic model — primitives and their boundaries

Each primitive below exists to answer a distinct question. A value MUST be
represented using the single most specific applicable primitive; using a
more general primitive when a more specific one applies is a contract
violation (e.g., representing a Prediction as a bare Belief).

| Primitive | Question it answers | MUST NOT be used for |
|---|---|---|
| **Knowledge** | What is durably true about the system, independent of any task? | Task-specific conclusions; anything not corroborated per §2's promotion rule |
| **Evidence** | What was directly observed or retrieved, with traceable origin? | Interpretation of what was observed (that is a Belief) |
| **Observation** | What was the raw result of executing or querying something? | A summary or interpretation of that result (kept separate, see §4) |
| **Belief** | What does the reasoning process currently think is true, and why? | Anything not revisable; anything asserted as settled fact |
| **Hypothesis** | Which of several competing explanations for an open question might be correct? | A single uncontested interpretation (that's an ordinary Belief, not a Hypothesis) |
| **Assumption** | What is being taken as given for planning purposes, not (yet) evidenced? | Any claim that already has strong supporting Evidence (promote it to a Belief instead) |
| **Prediction** | What outcome is expected from an action not yet taken? | A statement about the past or present (that's a Belief) |
| **Decision** | What was chosen, from which alternatives, and why? | Trivial or reversible read-only actions (see §12) |
| **Human Instruction** | What did a human direct the system to do? | A human's factual claim about the world (that is a Human Assertion) |
| **Human Assertion** | What did a human claim to be true about the world? | An authorization to act (that is a Human Instruction, or requires an explicit Decision/Approval) |

**Constraint** (Policy-derived or human-declared boundary on permissible
action) and **Contradiction** (a detected conflict between two
Evidence/Belief/Decision items) are structural relationships/records
referenced throughout this document; they are not additional epistemic
*tiers* — a Contradiction is a fact about two other records' relationship,
not a new kind of claim about the world.

## 4. Evidence model

- **Provenance.** Every Evidence item MUST record: the Capability that
  produced it, the Role/actor that invoked that Capability, and a
  timestamp. Evidence with no provenance MUST NOT be created.
- **origin_class.** Every Evidence item MUST carry exactly one origin_class
  from the closed set `{world_fact, human_directive, repository_content}`.
  This field is structural and MUST be assigned by the Capability at the
  moment of Observation — it MUST NOT be inferable, overridable, or
  reclassifiable by the Reasoning Engine after the fact. A Capability that
  reads repository-hosted content (source, docs, comments, tests, build
  scripts) MUST NOT be capable of emitting `human_directive`-class Evidence,
  regardless of that content's textual form.
- **source_trust.** Every Evidence item MUST carry a source_trust grade,
  independent of origin_class, reflecting the reliability of the mechanism
  that produced it (e.g., deterministic tool output ranks higher than an
  LLM's free-text interpretation of retrieved content). source_trust MUST
  be set at creation and MUST NOT be upgraded later by the reasoning layer.
- **Temporal validity.** Every Evidence item derived from the software
  system MUST carry an Execution Context (§7). Evidence without an
  Execution Context is invalid and MUST NOT be created for any code-,
  environment-, or system-derived claim.
- **Supersession.** An Evidence item MAY be marked as superseded by a newer
  Evidence item concerning the same subject. Supersession is a recorded
  relationship (`supersedes: <EvidenceID>`), never an overwrite. Superseded
  Evidence MUST remain readable.
- **Contradiction.** Two Evidence items MAY be marked as contradicting one
  another. This relationship MUST be recorded explicitly, not resolved
  implicitly by recency or by discarding one side.
- **Citation.** Every Belief, Hypothesis, Decision, and Plan Assumption that
  relies on Evidence MUST cite it by reference. A citation MUST NOT
  duplicate the cited Evidence's content beyond what is necessary for the
  citing record to remain independently interpretable if the underlying
  Knowledge fact later changes.
- **Raw Observation vs. derived summary.** The raw output of an executed or
  queried Capability (an Observation) MUST be retained in full. Any human-
  or machine-readable summary of that Observation (e.g., "test passed")
  MUST be explicitly marked as derived and MUST remain re-derivable from the
  raw Observation; a summary MUST NOT be stored as the sole surviving
  record.
- **Immutability.** Evidence, once recorded, MUST NOT be edited or deleted.
  Correction is achieved only through supersession.
- **Overclaim guard.** Evidence produced by interpreting a bounded retrieval
  (e.g., reading one file) MUST NOT be used to support a
  universally-quantified Belief (e.g., "only," "never," "all") beyond what
  the retrieval actually covered. A Belief asserting such universal scope
  MUST cite Evidence whose retrieval was itself exhaustive over the claimed
  scope, or MUST be recorded with explicitly reduced confidence/status
  reflecting the gap.

## 5. Knowledge epistemic model

Knowledge is not a single-tier authority. Every Knowledge fact MUST carry:

- **Derivation class** — exactly one of `observed` (directly witnessed,
  e.g., a runtime trace), `inferred` (derived by a deterministic or
  heuristic process from other facts, e.g., a static call inferred from an
  annotation), or `declared` (asserted by an external source such as
  documentation, not independently confirmed).
- **Provenance** — which Capability/source produced it and when.
- **Confidence** — graded, not implied by mere presence in the store;
  `declared` facts MUST default to lower confidence than `observed` facts
  unless independently corroborated.
- **Temporal validity** — the repository revision (or, more precisely,
  Execution Context, §7) the fact was true as of, and whether it has since
  been superseded or invalidated.
- **Competing claims.** When two sources disagree about the same subject
  (code vs. documentation, static vs. runtime), this MUST be recorded as an
  explicit `KnowledgeContradiction` relationship. The system MUST NOT
  silently pick one source as "the" truth and discard the other.
- **Runtime vs. static truth.** A fact about what the code *can* do (static
  analysis) and a fact about what the code *actually does* (runtime
  observation) MUST be represented as distinct facts, never merged into one
  reconciled value. Both MUST remain independently queryable.

## 6. Belief epistemic model

A Belief MUST carry all of the following, computed together, never
confidence alone:

- **Proposition** — the claim itself.
- **Confidence** — a graded estimate, always accompanied by its derivation
  method (deterministic function over structured Evidence, or
  model-elicited judgment); a confidence value MUST NOT be presented,
  stored, or consumed without its derivation method being resolvable.
- **Uncertainty** — a distinct property indicating how much additional
  plausible Evidence would be expected to change the confidence. A Belief
  with high confidence and high uncertainty (confident given very little
  looked at) MUST be distinguishable from one with high confidence and low
  uncertainty.
- **Evidence sufficiency** — an ordinal status (`none` / `sparse` /
  `adequate` / `strong`) independent of confidence, reflecting how much
  investigation has actually occurred.
- **Qualitative epistemic status** — one of `speculative`, `corroborated`,
  `contested`, `refuted`, `verified`. Any consumer (human or downstream
  Role) MUST be able to read this status without first interpreting a
  numeric confidence value.
- **Contradiction flag** — set structurally whenever contradicting Evidence
  exists, and MUST gate downstream consumption independently of what the
  confidence number states. A contested Belief MUST NOT drive a Decision
  above its risk-appropriate threshold while contested, regardless of its
  numeric confidence.
- **Derivation method** — whether the Belief is directly Evidence-derived or
  inferred from other Beliefs (an inference chain); inference-derived
  Beliefs MUST record the chain, not flatten it to look identical to a
  single-hop Belief.
- **Provenance chain** — source (Role/model/Capability), timestamp, and the
  Execution Context it was formed under.

Confidence, uncertainty, and qualitative status MUST be re-derived whenever
the underlying Evidence set changes — none of these fields MAY be
independently mutated by any actor.

## 7. Execution Context

An Execution Context is the composite anchor binding an Evidence item,
Observation, Plan, or Workspace to the state of the world at the moment it
was formed. It MUST include:

- Repository revision (commit identity).
- Resolved dependency versions (a lockfile-level resolution, not merely a
  declared version range).
- The subset of environment/feature-flag state that the invoking Capability
  explicitly declares as relevant to its own operation.
- A timestamp, serving as the anchor for external/world state that cannot
  be pinned (external API/service behavior).

It MUST NOT include an unbounded dump of the full environment, full
infrastructure configuration, or full external-system state — only the
dimensions a Capability declares itself dependent on. Two Observations MUST
NOT be treated as contradicting one another (§10) until their Execution
Contexts have been compared; a mismatch in Execution Context is grounds to
classify a discrepancy as expected variation, not Contradiction.

## 8. Event model

- **Event identity.** Every event MUST have a unique, immutable identifier
  and belong to exactly one task's event stream (or an explicitly-scoped
  sub-stream for multi-agent sub-tasks, §14).
- **Causal relationships.** Every event that arises as a consequence of
  another (a Decision caused by a Belief reaching threshold, a Replan
  caused by a Contradiction) MUST reference the causing record(s) by
  identifier. Events MUST NOT be recorded as free-floating facts with no
  causal linkage when a cause exists.
- **Ordering.** Within one task's event stream, events MUST have a single,
  total, append-determined order. Concurrent appends from multiple Roles to
  the same stream MUST be serialized such that no two events are assigned
  the same position.
- **Idempotency.** Any event representing the outcome of a retried Action
  MUST be distinguishable from a duplicate — a retried Action MUST carry a
  stable identifier linking all its attempts, so repeated appends do not
  produce ambiguous duplicate "completions" for what was one real-world
  effect.
- **Append authorization.** Not every actor may append every event type.
  `Human*`-class events (§13) MUST be appendable only through a
  verified-human-originated path; no Role or Capability may author an event
  of this class on a human's behalf.
- **Immutable history.** No event, once appended, may be edited or removed.
  Correction is achieved only by appending a new event that supersedes or
  revises the prior one, with an explicit reference.
- **Reconciliation.** The event log MUST NOT be assumed complete relative to
  external reality. Any Action with an effect outside the system's own
  transactional boundary MUST be subject to a reconciliation process that
  checks recorded outcome against observable external state and appends a
  correcting event if a discrepancy is found.
- **ActionOutcomeUnknown.** An Action whose outcome cannot be determined at
  completion time (e.g., an ambiguous timeout on an external write) MUST be
  recorded with this explicit status — never optimistically as
  `ActionCompleted` nor pessimistically as `ActionFailed`. No downstream
  reasoning may treat an `ActionOutcomeUnknown` Action as either succeeded
  or failed until reconciliation resolves it.

## 9. State reconstruction — four distinct guarantees

These are four separate claims. An implementation MUST NOT conflate them or
advertise one as if it implies another.

- **State replay** — reconstructing the materialized Engineering State
  (Beliefs, Hypotheses, Plan status, Decisions) as it existed at any
  historical point in time, by folding the event log up to that point.
  **Guarantee: unconditional.** This MUST always be possible from the event
  log alone.
- **Reasoning replay** — re-running the same reasoning process and
  obtaining the same output. **Guarantee: conditional.** This is only
  possible for tasks where model identity/version, prompt version,
  retrieval/ranking algorithm version, Knowledge index version, and invoked
  Capability versions were pinned at execution time and remain available.
  Where any of these is unpinned or unavailable, reasoning replay MUST NOT
  be claimed as achievable.
- **Execution replay** — re-running the same Actions and obtaining the same
  external effects. **Guarantee: not provided.** External systems are not
  fully deterministic or rewindable. At most, a sandboxed approximation MAY
  be attempted; it MUST NOT be represented as guaranteeing identical
  outcomes.
- **World-state reconstruction** — determining what was objectively true
  about the software/environment at a historical point. **Guarantee:
  bounded by Knowledge's own historical fidelity** (§5). This reconstructs
  "what was recorded as observed," not "what was objectively true
  independent of observation" — the distinction MUST be preserved in how
  this guarantee is communicated.

## 10. Observation classification

Every Observation resulting from an Action MUST be classified into exactly
one of the following before any downstream reasoning consumes it:

| Classification | Meaning | Triggers |
|---|---|---|
| **Expected** | The Observation matches the Prediction associated with the Action. | Normal Evidence accrual. No Contradiction, no Replan. |
| **Anomaly** | The Action did not complete as expected for reasons unrelated to the correctness of the reasoning that proposed it (timeout, network failure, infrastructure failure, unavailable dependency). | MUST trigger retry-with-backoff. Repeated Anomaly on the same Action MUST escalate to a human/operational channel. MUST NOT trigger Belief revision or Replan. |
| **Blocked** | The Action was correctly reasoned but not authorized (insufficient permission, Policy denial, resource unavailability). | MUST escalate to Policy/human for authorization. MUST NOT trigger Belief revision or Replan. |
| **Uncertain-Outcome** | The result is ambiguous or apparently non-deterministic (e.g., a flaky test). | MUST trigger a repeat-observation Capability before further classification. MUST NOT be classified as Contradiction or Anomaly until repeated observation disambiguates it. |
| **Contradiction** | A currently active Belief, Assumption, or PlanStep postcondition is genuinely falsified by this Observation, after Execution Context comparison (§7) rules out environmental mismatch as the explanation. | MUST trigger Belief revision, dependent PlanStep invalidation, and Replan. This is the ONLY classification that may trigger Replan. |

An Observation MUST NOT be classified as Contradiction by default;
classification into Anomaly/Blocked/Uncertain-Outcome MUST be actively ruled
out first where applicable to the failure's actual character.

## 11. Plan model

A Plan is a directed acyclic graph, not a flat ordered list.

- **Plan** — a Goal reference, declared Scope, declared Assumptions
  (references to Assumption records, §3), Preconditions, a DAG of
  PlanSteps, Risk classification, Rollback strategy, and Completion criteria
  (the Goal's postconditions restated as a checklist).
- **PlanStep** — an action (a specific Capability invocation) OR a nested
  Plan (a sub-Plan with its own postconditions and verification strategy),
  dependencies on other PlanSteps (forming the DAG edges), an expected
  outcome (a Prediction), postconditions, and a verification strategy
  declared at planning time.
- **Nested plans** — a PlanStep MAY itself be a full Plan. Its parent Plan
  MUST treat its completion/verification as an opaque postcondition,
  without needing to inspect its internal structure.
- **Conditional branches** — a PlanStep MAY declare multiple alternative
  successor paths, each guarded by a condition evaluated against
  Engineering State at the time the guard is reached. Only one guarded
  branch may be active at a time per conditional point.
- **Exploratory steps** — a PlanStep MAY have a postcondition that is purely
  informational ("sufficient understanding gathered to plan the next step")
  rather than a completed external action.
- **Ownership** — every Plan and PlanStep MUST have exactly one owning Role
  at any given time (§14). No two Roles may hold ownership of the same
  unclaimed PlanStep simultaneously.
- **Invalidation** — a PlanStep is invalidated when a Contradiction (§10)
  affects a Belief/Assumption it depends on. Invalidation MUST propagate
  only to dependent PlanSteps in the DAG, not to the whole Plan by default.
- **Supersession** — a Plan whose Goal has changed (rather than been
  falsified) MUST be marked superseded, not invalidated — this is a
  distinct transition from Contradiction-driven invalidation.
- **Completion** — a Plan is complete only when every PlanStep's
  postcondition has been independently verified — never inferred from the
  absence of failure.
- **Approval binding** — an approved Plan MUST be content-hashed at
  approval time (§13). Any revision after approval MUST produce a new Plan
  version; the approved version MUST remain retrievable unchanged.

## 12. Decision model

A Decision — selected option, alternatives considered (with rejection
reasons), citing Evidence/Beliefs, Constraints checked, confidence at
decision time, decision maker, resulting Action — **MUST** be recorded
whenever:

- the Action has an external or state-changing effect (a write, a
  deployment, a deletion, any effect visible outside the reasoning process
  itself), OR
- the Action commits resources or forecloses a genuinely viable alternative
  that was under consideration.

A Decision **MUST NOT** be required, and recording one is prohibited from
being treated as mandatory scaffolding, when:

- the Action is read-only or purely exploratory (a query, a file read, a
  graph traversal), OR
- the Action is deterministic with no genuine alternative under
  consideration (e.g., re-running a test to check for flakiness), OR
- the Action is fully and cheaply reversible and entirely internal to the
  reasoning process (no external visibility).

Safety-critical or destructive Actions MUST additionally record a
non-empty alternatives-considered field including at minimum a "do not
proceed" alternative, subject to Policy check.

## 13. Human authority

Five distinct concepts, related as follows:

- **Human Intent** — the durable, high-level Goal a human wants achieved.
  Rarely revoked; changes to it are recorded as `HumanScopeChanged`/new
  `GoalCreated` events, not edits.
- **Human Approval** — a specific, immutable, content-hash-pinned
  authorization of a specific Plan/Scope, recorded at a specific point in
  time. It MUST NOT be edited after the fact; it remains a permanent
  historical record even after it stops being sufficient for Execution
  Authorization.
- **Policy Authorization** — the standing rules, independent of any one
  human's click, determining what is permitted now, including safety rules
  capable of overriding a stale Approval's practical effect.
- **Execution Authorization** — the currently valid permission to proceed
  with a specific next Action. It **MUST** be computed fresh, immediately
  before the Action, as: `Human Approval (pinned, historical) AND Policy
  (current) AND Safety Validity (current)`. It MUST NOT be read directly
  off the historical Approval record alone.
- **Safety Validity** — a continuously re-evaluated property answering
  whether the basis of the original Approval (repository state, risk
  assessment, threat landscape) still holds.

**Interaction rules, normatively stated:**

- The system MUST halt Execution the moment Safety Validity evaluates false
  for a pending Action, regardless of a prior valid Human Approval.
- Halting on invalidated Safety Validity MUST NOT be treated as overriding
  the human's decision — the Human Approval record remains unchanged and
  true as a historical fact; only its sufficiency for current Execution
  Authorization has lapsed.
- A halt of this kind MUST re-escalate to a human with an explicit
  explanation of what changed. The system MUST NOT silently resume on its
  own authority, and MUST NOT silently continue on the stale Approval's
  authority either.
- **Approved scope changes** (discovered mid-execution to differ from what
  was approved) MUST cause Execution Authorization to fail for the affected
  delta and re-escalate; unaffected, already-approved-and-matching portions
  MAY proceed if independently still authorized.
- **Repository changes** mid-execution MUST trigger the Execution Context
  comparison in §7/§10 before any pending Action proceeds.
- **Risk changes / a vulnerability appears / Policy changes** MUST be
  evaluated as Safety Validity inputs; any of these becoming unfavorable
  MUST halt pending Execution per the rule above.
- **Approval becomes stale** (time-based or condition-based, per Policy)
  MUST be treated identically to a Safety Validity failure.

## 14. Multi-agent semantics

- **Ownership.** Every Plan, PlanStep, and open Hypothesis under active
  investigation MUST have exactly one owning Role at a time.
- **Leases.** A Role MUST acquire an explicit claim (lease) before
  beginning work that would duplicate another Role's active investigation
  of the same Hypothesis or PlanStep. A lease MUST have a bounded validity
  and MUST be releasable/expirable so a stalled Role does not permanently
  block others.
- **Causality.** Cross-Role dependencies (Role B's Decision depending on
  Role A's Belief) MUST be recorded via explicit event causal references
  (§8), not inferred from timing.
- **Concurrent investigation.** Two Roles MAY independently investigate
  different Hypotheses about the same Goal concurrently, provided neither
  holds a conflicting lease.
- **Conflicting Decisions.** Two Roles reaching different Decisions about
  the same question MUST surface as a Contradiction (§10) requiring
  resolution (via an authority hierarchy defined by Policy, or human
  escalation) before either proceeds to Action.
- **Competing Plans.** Two Roles MUST NOT both hold execution ownership of
  the same PlanStep. A Plan revision proposed by a non-owning Role MUST be
  submitted for the owning Role's (or Policy's) explicit approval, not
  applied unilaterally.
- **Stale beliefs.** Every Role MUST read the materialized Engineering
  State fresh at the start of each reasoning cycle; no Role may cache and
  reuse a Belief snapshot across cycles without re-validating it against
  current state.
- **Cross-Role trust.** A Belief authored by one Role and consumed by
  another MUST retain its full provenance chain (§6) so the consuming Role
  can grade it independently — a foreign Role's Belief MUST NOT be treated
  as equivalent in trust to the consuming Role's own directly-Evidence-derived
  Belief by default.
- **Shared state poisoning.** Because all writes are append-only and
  attributed (§8), a poisoned or erroneous contribution from one Role
  remains fully traceable and reversible via supersession — it MUST NOT be
  capable of silently overwriting another Role's record.

## 15. Security boundary

- **origin_class enforcement.** Every Evidence-producing Capability MUST
  assign origin_class (§4) at the point of Observation, based on the
  Capability's own nature (does it read repository content, does it
  constitute a verified human input channel, or does it observe world state
  independent of both) — never based on the *content* of what was
  retrieved.
- **Repository content cannot manufacture Human Instruction.** A Capability
  that reads repository-hosted content (source, README, comments, tests,
  build scripts, dependency metadata) MUST be structurally incapable of
  emitting an event or Evidence item with origin_class `human_directive`.
  This MUST hold regardless of the textual content encountered, including
  text that is grammatically imperative or claims elevated authority.
- **Repository content cannot manufacture Human Approval.**
  `HumanApprovalGranted` and related `Human*` events MUST be appendable
  only through the verified-human-originated path defined in §8's
  append-authorization rule. No Capability that processes repository
  content has access to this append path.
- **Repository content cannot manufacture Policy Authorization.** Policy
  Authorization is evaluated from Policy's own rule set and current Safety
  Validity (§13), neither of which accepts repository content as an input
  capable of altering Policy rules themselves. Repository content MAY
  inform a Belief that in turn informs a Decision, but that Decision
  remains subject to the full Decision/Policy gate (§12/§13) — it cannot
  bypass it.
- **Residual risk, stated explicitly.** This boundary prevents structural
  forgery of authority. It does NOT prevent a Role from being *persuaded*
  by low-trust, `repository_content`-class Evidence into a mistaken Belief
  that then drives a Decision through the normal, legitimate Decision/Policy
  path. Mitigating this residual risk requires implementation-level
  discipline (corroboration thresholds before repository-content-class
  Evidence may singularly justify a high-risk Decision) and is not fully
  solved by the data model alone; this MUST be treated as an ongoing
  operational requirement, not a closed problem.

## 16. State-scale architecture

- **Durable history.** The full event log MUST be retained without
  summarization or loss, subject only to archival (below), for as long as
  audit/replay guarantees (§9) are required to hold.
- **Active materialized state.** The Reasoning Engine MUST read only a
  continuously-maintained materialized projection scoped to currently-open
  Beliefs, Hypotheses, and PlanSteps for the active task/sub-task. Resolved
  (rejected, promoted, or completed-and-verified) items MUST drop out of
  this active projection while remaining in the durable log.
- **Working-set boundaries.** In multi-agent operation (§14), each Role's
  materialized working set MUST be scoped to what it owns or has an active
  lease on — not the full task's history.
- **Evidence retrieval scope.** Retrieval of Evidence relevant to a
  specific open Hypothesis or PlanStep MUST be performed via explicit
  citation-graph traversal (what Evidence is actually linked to this
  question), not unscoped search across all Evidence ever gathered by the
  task.
- **Archival.** Event data beyond a retention/volume threshold MAY be moved
  to slower/cold storage but MUST NOT be deleted, and MUST remain
  retrievable for forensic replay (§9, State replay guarantee).

## 17. Non-goals — what Engineering State does NOT own

- Engineering State does NOT own facts about the software system
  independent of any task (that is Knowledge, §2).
- Engineering State does NOT own a live mirror of Workspace's internal
  filesystem/process state (that is Workspace's own concern; Engineering
  State holds only references and Observations from defined checkpoints).
- Engineering State does NOT own credential material of any kind.
- Engineering State does NOT own Policy's rule definitions (it records
  which Policy version was evaluated against, not the rules themselves).
- Engineering State does NOT own the authoritative current state of
  external systems (a live GitHub PR status, a live Jira ticket) — only
  citations of what was observed about them, when.
- Engineering State does NOT guarantee Execution replay or unconditional
  World-state reconstruction (§9).
- Engineering State does NOT resolve confidence to a single trusted number
  without accompanying uncertainty/evidence-sufficiency/qualitative-status
  context (§6).

## 18. Formal state invariants

1. A Belief MUST NOT silently become a Knowledge fact. Knowledge is written
   only through the promotion process in §2, never directly by the
   Reasoning Engine.
2. A Decision, where required per §12, MUST have recorded provenance
   including alternatives considered.
3. A Plan MUST NOT be considered approved if its content differs from the
   version that was content-hashed and approved; any drift MUST reopen
   approval for the affected delta.
4. A failure MUST be classified per §10 before triggering any downstream
   response; only Contradiction MAY trigger Belief revision, PlanStep
   invalidation, and Replan.
5. The materialized Engineering State at any historical point in time MUST
   be exactly reconstructable by folding the event log up to that point
   (State replay, §9, unconditional).
6. Reasoning replay, Execution replay, and World-state reconstruction MUST
   be represented as distinct, conditionally-or-partially-guaranteed
   claims, never conflated with State replay.
7. Confidence values MUST NOT be presented, stored, or consumed without
   their derivation method and accompanying
   uncertainty/evidence-sufficiency/qualitative-status context.
8. Repository content MUST NOT be structurally capable of producing
   `human_directive`-class Evidence or any `Human*`-class event.
9. Execution Authorization MUST be recomputed immediately before every
   risk-relevant Action from current Human Approval, Policy, and Safety
   Validity — never read as a static consequence of a historical Approval
   alone.
10. Execution MUST halt and re-escalate to a human — never proceed
    silently, never self-reauthorize — the moment Safety Validity evaluates
    false.
11. Every code/system-derived Evidence, Plan, and Verification result MUST
    carry an Execution Context (§7); a discrepancy between two
    Observations' Execution Contexts MUST be ruled out before they are
    treated as a genuine Contradiction.
12. The event log MUST be treated as authoritative only for what it
    successfully recorded; any Action with an external effect MUST be
    subject to reconciliation, and an ambiguous outcome MUST be recorded as
    `ActionOutcomeUnknown`, never optimistically resolved.
13. Events MUST be append-only and immutable; correction is achieved only
    via superseding events, never edits or deletions.
14. Append authorization MUST be enforced per event type; `Human*`-class
    events MUST be appendable only via a verified-human-originated path.
15. No two Roles MAY hold ownership or an active lease over the same
    Plan/PlanStep/Hypothesis simultaneously (§14).
16. A Belief authored by a foreign Role MUST retain its provenance chain
    and MUST NOT be consumed with the same default trust as a Role's own
    directly-derived Belief.
17. The Reasoning Engine MUST read only the current materialized
    projection at the start of each cycle and MUST NOT retain or reuse a
    cross-cycle cached copy without re-validation.
18. Knowledge facts MUST carry derivation class (`observed`/`inferred`/
    `declared`), provenance, confidence, and temporal validity; competing
    claims about the same subject MUST be recorded explicitly, never
    silently resolved by discarding one side.
19. The active materialized working set MUST remain bounded to
    currently-open state; resolved items MUST drop out of it while
    remaining in the durable log.
20. Nothing outside the durable event log may be treated as authoritative
    by any reader of Engineering State.

## 19. Normative terminology

This document uses MUST/MUST NOT to denote absolute requirements whose
violation constitutes non-conformance, and SHOULD/SHOULD NOT to denote
strong recommendations that MAY be deviated from only with a documented,
deliberate justification recorded at the point of deviation. Where this
document does not use MUST/MUST NOT/SHOULD/SHOULD NOT for a described
behavior, that behavior is descriptive context, not a conformance
requirement.

## 20. Architectural decision summary

| Decision | Rationale | Rejected alternative |
|---|---|---|
| Event log + materialized projection (hybrid), not pure event sourcing or periodic snapshots | Pure ES makes every read require replay or ad hoc caching with no domain-modeling discipline; periodic snapshots answer a performance question, not a domain question | Pure event sourcing with no materialized layer; time/count-based snapshotting |
| Confidence + uncertainty + evidence-sufficiency + qualitative status, not a single scalar | A single derived number collapses conflicting, incomplete, or multi-source epistemic states into indistinguishable values | Confidence as a single deterministic function of Evidence |
| Knowledge carries its own epistemic model (derivation class, confidence, temporal validity, competing claims) | Treating Knowledge as a flat authority hides real disagreement between code, docs, tests, and runtime behavior | Knowledge as a single-tier ground truth |
| Execution Context (revision + resolved deps + declared env/flags + timestamp), not bare repository revision | Repository revision alone cannot explain divergent outcomes at the same commit | Repository revision as sole temporal anchor |
| Four-way failure taxonomy (Expected/Anomaly/Blocked/Uncertain-Outcome/Contradiction), not a single Contradiction path | Routing infrastructure noise and permission denials through Belief revision corrupts reasoning quality | Every failure treated as a Contradiction triggering Replan |
| Plan as a DAG with nested/conditional/exploratory steps, not a flat ordered list | Real engineering work branches, nests, and explores before committing; a flat list cannot represent this | Flat, single-path Plan |
| Decision required by reversibility × external-visibility, not by "every consequential action" | Recording a Decision for every tool call produces unusable log volume and dilutes real signal | Decision required for any agent action deemed "consequential" |
| Five-way human-authority split (Intent/Approval/Policy/Execution-Authorization/Safety-Validity), continuously reevaluated | An inviolable "only a human can supersede a human decision" rule prevents safe halting when an approved plan becomes unsafe | Human Approval treated as a standing, context-free authorization until explicitly revoked |
| origin_class as a structural, unspoofable Evidence field, independent of source_trust | Trust-tiering alone can still misclassify a sufficiently plausible injected instruction; a structural type barrier cannot be argued around | Relying on source_trust grading alone to defend against prompt injection |
| Leases/ownership/cross-Role trust grading for multi-agent operation | Shared append-only state alone does not prevent duplicate investigation, competing plan ownership, or uncritical cross-agent trust | Assuming append-only safety is sufficient for multi-agent correctness |
| Materialized working set bounded to currently-open state; citation-scoped Evidence retrieval | An unbounded "read everything gathered so far" pattern degrades at long task duration; structural citation-scoping is cheaper and more precise than unscoped search | Unscoped semantic/similarity search over full task history as the retrieval mechanism |
| Four distinct replay guarantees (State/Reasoning/Execution/World-state), not one "exact reconstruction" claim | These require genuinely different pinned inputs and cannot be honestly promised uniformly | A single unconditional "exact reconstruction" guarantee |

---

# FINAL CONTRACT

The following rules are binding on any implementation claiming conformance
with this specification.

1. Engineering State owns only this task's reasoning trace (Evidence,
   Observations, Beliefs, Hypotheses, Assumptions, Predictions, Decisions,
   Plan, human events); it never owns durable facts about the software
   system — those belong to Knowledge.
2. A Belief becomes a Knowledge fact only through explicit, corroborated
   promotion by a Knowledge-writing Capability — never directly, never
   implicitly, never by confidence alone.
3. Every Evidence item MUST carry provenance, origin_class, source_trust,
   and (where system-derived) an Execution Context, and MUST be immutable
   once recorded; correction is by supersession only.
4. origin_class MUST be assigned structurally by the producing Capability
   and MUST make it impossible for repository-hosted content to be
   classified as `human_directive`; `Human*`-class events MUST be
   appendable only via a verified-human-originated path.
5. A Belief's confidence, uncertainty, evidence-sufficiency, and
   qualitative status MUST always be derived together from its current
   Evidence set, never independently stored or hand-set, and MUST never be
   presented without their derivation method.
6. A Decision is required only for Actions with external/state-changing
   effect or that foreclose a genuine alternative; trivial, read-only,
   deterministic, or fully-internal-and-reversible Actions MUST NOT
   require one.
7. A Plan is a DAG, content-hashed and immutable once approved; any
   post-approval drift reopens approval for the affected delta only.
8. Every Observation MUST be classified as Expected, Anomaly, Blocked,
   Uncertain-Outcome, or Contradiction before consumption; only
   Contradiction may trigger Belief revision, PlanStep invalidation, and
   Replan.
9. Execution Authorization MUST be recomputed immediately before every
   risk-relevant Action from Human Approval (pinned, historical) AND
   Policy (current) AND Safety Validity (current); it is never read
   statically off a historical approval.
10. Execution MUST halt and escalate to a human — never proceed, never
    self-reauthorize — the instant Safety Validity fails, without altering
    the immutable historical Approval record.
11. The event log is append-only and immutable; it is the sole write path
    and the sole source of authoritative truth about this task's
    reasoning trace.
12. The event log's completeness relative to external reality is never
    assumed; externally-effecting Actions require reconciliation, and
    ambiguous outcomes MUST be recorded as `ActionOutcomeUnknown`.
13. State replay (materialized state at any historical time, from the
    event log alone) is unconditionally guaranteed; Reasoning replay,
    Execution replay, and World-state reconstruction are distinct,
    conditional guarantees and MUST never be conflated with State replay.
14. In multi-agent operation, ownership and leases MUST prevent two Roles
    from concurrently owning the same Plan/PlanStep/Hypothesis, and a
    foreign Role's Belief MUST retain its provenance chain rather than
    being consumed at default trust.
15. The materialized, actively-read Engineering State MUST remain bounded
    to currently-open items; resolved items persist only in the durable
    log, and Evidence retrieval MUST be citation-scoped, not unscoped
    search.
16. Nothing outside the durable, append-only event log may be treated as
    authoritative by any reader — cached or independently-mutated derived
    state is a contract violation the moment it is trusted over a fresh
    fold of the log.
