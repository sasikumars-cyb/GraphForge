# GraphForge — Reasoning Engine Architecture Contract

**Status:** NORMATIVE. Accepted. This document is a canonical architecture
contract, not a proposal, not a discussion, and not one design option among
several.

> **This document is the canonical normative specification for the
> GraphForge Reasoning Engine.**
>
> Any implementation, agent behavior, architectural decision, or future
> design proposal concerning the Reasoning Engine, the Control Plane, the
> Reasoning Plane, ActionProposal, Capability, Composed Capability,
> candidate generation, capability composition, reasoning cycles,
> autonomy, or action selection **MUST conform to this contract.**
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

## 0. Relationship to the Engineering State contract and the rest of GraphForge's documentation

**This document does not duplicate
[`ENGINEERING_STATE_ARCHITECTURE.md`](ENGINEERING_STATE_ARCHITECTURE.md)
and must never be edited to do so.** Engineering State remains canonical
and authoritative for: state, Evidence, Beliefs, Hypotheses, Assumptions,
Predictions, Decisions, Plans, events, temporal state, and human authority.
This document is canonical and authoritative for: reasoning, ActionProposal,
action selection, and reasoning cycles. The Control Plane defined here is
canonical and authoritative for: authorization, policy enforcement, and
execution eligibility. Where this document references a primitive already
defined in the Engineering State contract (Belief, Hypothesis, Evidence,
Decision, Plan, PlanStep, Human Approval, Execution Authorization,
Contradiction, Observation classification), that primitive's definition is
never restated here beyond what's needed for context — the Engineering
State contract's definition governs.

**Authority hierarchy** (unchanged from, and consistent with,
`ENGINEERING_STATE_ARCHITECTURE.md` §0 — this document slots in as a peer
canonical contract at the same tier, not above or below it):

```text
ENGINEERING_STATE_ARCHITECTURE.md  ◄──►  REASONING_ENGINE_ARCHITECTURE.md
  (canonical for state/knowledge)          (canonical for reasoning/action-
                                             proposal/action-selection)
        — neither may override the other; both are canonical, each for
          its own explicitly-scoped domain; the Reasoning Engine's every
          read/write into Engineering State is bound by the Engineering
          State contract's own invariants without exception
        ↓
docs/graphforge/*.md (ARCHITECTURE.md, AGENT_FRAMEWORK.md, PRODUCT_VISION.md,
ROADMAP.md, API_CONTRACTS.md, ...) — target-design documents for the rest
of the system; where AGENT_FRAMEWORK.md's per-agent Plan/Select/Execute/
Observe/Decide loop describes reasoning-shaped concepts, this contract wins
on any conflict (see §22 below)
        ↓
docs/adr/*.md, docs/rfcs/*.md — point-in-time decision/implementation
records, read as historical unless explicitly reconciled
        ↓
docs/handbook/*.md, docs/presentation/*.md — defense/summary material,
never authoritative on its own
        ↓
Implementation (backend/app/, frontend/src/)
        ↓
Tests
```

## Preventing architectural drift

Identical discipline to `ENGINEERING_STATE_ARCHITECTURE.md`'s own section of
this name, restated here rather than referenced, because it applies
independently to this contract: if implementation, a new ADR, a new design
document, or an agent's own reasoning appears to conflict with this
contract, (1) identify the conflict explicitly, naming the invariant and
the file; (2) do not silently reinterpret the contract to make the conflict
disappear; (3) do not weaken an invariant because the current
implementation is simpler without it, the schema is inconvenient, an API
doesn't support it, a model can't currently reason about it, migration is
hard, tests are hard to write, or it would be faster to skip; (4) name the
architectural decision required to resolve it; (5) update this contract
only through that explicit decision. **Architecture comes first.**

---

# The Contract

## 1. Purpose

The Reasoning Engine is the single process that, given the current
materialized Engineering State plus what Knowledge, Capabilities, and
Policy currently make available, decides what happens next in pursuit of a
Goal — and outputs exactly one of a small, closed set of state-changing
moves per cycle: gather more Evidence, form or revise a Hypothesis, commit
to a Decision, advance or revise the Plan, propose an Action, or escalate
to a human. It is the minimum conceptual system that turns Engineering
State + Knowledge + Capabilities + Policy into the next *justified*
engineering action — "justified" meaning traceable back to specific
Evidence/Beliefs/Policy already present in Engineering State, never
asserted by fiat.

It is **not** an LLM wrapper, a prompt template, an Agent, a Workflow, a
collection of specialized agents, a model provider, a tool executor, a
planner in isolation, or a reviewer in isolation. It may use all of these
as components; it is defined by the decision procedure that sequences and
bounds their use, not by any one of them.

## 2. Non-responsibilities

The Reasoning Engine does **not** own:

- **Policy enforcement.** It consults Policy; it never defines, weakens, or
  overrides a Policy rule.
- **Authorization.** It never grants itself, or any ActionProposal it
  generates, Execution Authorization. Authorization is computed
  exclusively by the Control Plane (§4), always freshly, per
  `ENGINEERING_STATE_ARCHITECTURE.md` §13.
- **Execution.** It never directly performs an external or state-changing
  effect. Execution happens only through an authorized Capability
  invocation dispatched by the Control Plane.
- **Capability implementation.** It never implements *how* a Capability
  performs a query, a build, or a call — that is the Capability's own
  concern.
- **Verification.** It never marks its own or any Role's work as correct.
  Independent Verification (§12) is structurally separate and MUST NOT be
  implemented as an extension of the generating Reasoning Plane.
- **Durable state authority.** It does not own the event log, does not
  independently mutate Engineering State, and does not retain hidden
  cross-cycle memory of its own — see §3.

## 3. Engineering State boundary

The Reasoning Engine consumes and updates Engineering State **only**
through the event/state mechanisms `ENGINEERING_STATE_ARCHITECTURE.md`
already defines. Concretely:

- It MUST read only the current materialized Engineering State projection
  at the start of each cycle (Engineering State contract invariant 17) —
  never a cached or independently-held copy from a prior cycle.
- Every state change it causes MUST be an appended event, per the
  Engineering State contract's §8 event model — `HypothesisCreated`,
  `DecisionMade`, `PlanRevised`, `ActionProposed`, `ReplanTriggered`,
  `HumanApprovalRequested`, and so on. The Reasoning Engine is the
  principal author of these event types, but it appends them through the
  same event model everything else in the system uses — it has no private
  write path.
- It MUST NOT bypass any invariant in that contract — including, without
  limitation, Evidence immutability, Belief confidence being derived not
  stored, origin_class enforcement, Observation classification before
  consumption, and Execution Authorization being recomputed fresh. This
  contract adds a reasoning-and-action-proposal layer *on top of*
  Engineering State; it never substitutes for or relaxes any part of it.

## 4. Control Plane

The Control Plane is the deterministic half of the Reasoning Engine. It
answers exactly one question — **"may this be done?"** — and answers it
identically regardless of where a candidate action came from (pre-registered
Capability selection or a freshly composed ActionProposal, per §10). Its
responsibilities:

- **Candidate validation** — the single gate every candidate action, from
  either source, must pass in full before it may execute.
- **Capability coverage** — every atomic step referenced by a candidate
  MUST map to a registered primitive Capability (§7); a step with no
  covering primitive is a distinct **capability-gap** outcome (§11), not an
  ordinary rejection.
- **Parameters** — well-formed, within each referenced Capability's
  declared bounds.
- **Scope** — the candidate's target MUST match the currently-approved
  Plan's declared Scope (Engineering State contract §11); anything outside
  it is rejected, not silently narrowed to fit.
- **Policy** — authorized at the current autonomy Level (§14).
- **Authorization** — Execution Authorization recomputed fresh immediately
  before execution, per Engineering State contract §13 — Human Approval
  (pinned, historical) AND Policy (current) AND Safety Validity (current).
  Never read statically off a prior approval.
- **Constraints** — active human- or Policy-declared Constraints checked.
- **Budget** — fits the remaining ledger.
- **Execution Context** — preconditions currently hold (Engineering State
  contract §7).
- **Risk / reversibility / external visibility** — jointly determine
  whether a Decision record is required (Engineering State contract §12)
  and whether this candidate's shape has been validated before (§9).
- **Ownership / leases** — in multi-agent operation, no candidate may
  proceed against a PlanStep/Hypothesis another Role already owns or holds
  an active lease on (Engineering State contract §14).
- **Safety Validity** — continuously re-evaluated; a candidate that was
  valid a cycle ago is re-checked, not assumed still valid.

The Control Plane never asks "is this a good idea" — only "is this
legal." Value judgment belongs exclusively to the Reasoning Plane.

## 5. Reasoning Plane

The Reasoning Plane is the probabilistic, judgment-exercising half of the
Reasoning Engine. It answers exactly one question — **"what should be
done?"** — operating only within whatever the Control Plane has already
determined is legally eligible. Its responsibilities:

- **Identify Unknowns** — determine which open PlanStep precondition or
  Assumption lacks a Hypothesis/Belief at or above its risk-appropriate
  confidence-and-sufficiency bar.
- **Evaluate Hypotheses** — interpret Evidence to form, update, or reject
  competing explanations (Engineering State contract §3/§5).
- **Gather Evidence** — select and invoke a Capability whose declared
  applicability plausibly closes a specific open Unknown.
- **Generate ActionProposals** — compose one or more primitive
  Capabilities, including novel sequences and parameterizations never
  pre-registered, into a structured proposal (§6).
- **Compose Capabilities** — assemble multi-step sequences within a single
  proposal (§8), not merely pick one pre-registered candidate.
- **Evaluate, as separate typed dimensions, never fused into one score**:
  expected information gain, cost, risk, reversibility, dependency impact,
  and probability of actually resolving the current Unknown.
- **Select what to do next** among the Control-Plane-filtered candidate
  set, or select none if nothing surviving is worth its cost this cycle.
- **Determine when more evidence is required** — a structural check: any
  PlanStep precondition still below its confidence-and-sufficiency bar
  blocks that step from Act.
- **Determine when to ask a human** — every investigation option for an
  open Unknown is exhausted with confidence/sufficiency still unmet, or
  Execution Authorization fails for a pending Act.
- **Determine when to stop** — Plan completion criteria independently
  Verified, budget exhausted (recorded honestly as such, never narrated as
  "nothing more to try"), or Policy permanently denies further
  authorization with no escalation path remaining.

The Reasoning Plane never authorizes its own output. Its output is always
a proposal (§6) or a selection among Control-Plane-validated candidates —
never an executed effect.

## 6. ActionProposal

**Purpose.** The single vehicle through which the Reasoning Plane expresses
"this is what I think should happen next," whether that's a direct
selection of a pre-registered Capability or a genuinely novel composition.

**Structure.** An ActionProposal MUST carry:
- a reference to the target Unknown/PlanStep it addresses,
- one or more referenced primitive Capabilities and their parameters, in
  a declared composition/sequence,
- an expected outcome, recorded as a Prediction (Engineering State
  contract §3),
- a declared scope (which components/files/systems it touches),
- a self-assessed risk and reversibility classification,
- a declared external-visibility classification,
- its provenance (which Role/model/cycle generated it),
- a validation state (`unvalidated` / `validated` / `rejected` /
  `capability-gap`),
- an authorization state (`unauthorized` / `authorized` / `expired`),
  computed exclusively by the Control Plane, never set by the proposal's
  author.

**Lifecycle.** `proposed` → Control Plane validation (§4) → `validated` or
`rejected`/`capability-gap` → (if validated) Execution Authorization
computed fresh → `authorized` or remains `unauthorized`/`expired` → (if
authorized) dispatched to a Capability (§7) → the resulting Observation is
classified per Engineering State contract §10, closing this proposal's
lifecycle.

> **Cross-reference — authorization mechanics refined (not contradicted).**
> The `authorization state` field and the single proposal-level "Execution
> Authorization computed fresh" described above are refined by
> [`CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md`](CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md)
> §7–§9: authorization is a **separate, per-Action, single-use, TTL-bounded
> Grant**, never a field on the proposal, and a multi-Action composition
> never receives one authorization for its lifetime. That contract governs
> the mechanics of authorization; every invariant below is strengthened,
> never weakened, by the refinement.

**Explicit rule: an ActionProposal MUST NOT contain execution authority.**
No field on an ActionProposal, no confidence value it carries, no stated
rationale, and no prior success of a similarly-shaped proposal may cause it
to execute without passing through the full Control Plane validation and
fresh Execution Authorization computation, every time.

## 7. Primitive Capability

A primitive Capability is a registered, atomic channel of contact with the
world — reading a file, running a build, executing a test, querying the
graph, calling a specific external API, modifying a specific kind of
resource. Each primitive Capability declares: its applicability (what kind
of Unknown/Action it can serve), its parameters, its cost class, and its
risk classification.

**The set of primitive Capabilities is closed and MUST remain closed.**
Reasoning cannot invent a new primitive world channel — a Capability that
does not exist cannot be granted by proposal, by confidence, or by
argument. This is not a limitation of intelligence; it is the boundary
that keeps intelligence's freedom to *combine* channels from becoming an
uncontrolled freedom to *acquire* channels. A candidate whose composition
requires a step with no covering primitive Capability is a capability-gap
(§11), never quietly worked around.

## 8. Capability Composition

A single ActionProposal MAY reference and sequence multiple primitive
Capabilities to express a composed action — reading code, then modifying a
temporary Workspace, then building, then testing, then comparing results is
one ActionProposal, not four independent candidates evaluated in isolation.
Composition is where the openness this contract requires actually lives:
the set of primitive Capabilities is closed, but the set of sequences,
parameterizations, and combinations of them is not, and MUST NOT be
artificially capped by requiring every useful composition to have been
pre-registered before it can be proposed.

**Experiment is explicitly NOT a separate primitive.** A "novel
experiment" — e.g., toggling a configuration, reproducing a failure, and
comparing outcomes across two variants — is an ActionProposal whose
composition happens to follow a modify→observe→compare shape. It requires
no new primitive, no new type, and no special-cased handling distinct from
any other multi-step ActionProposal.

## 9. Composed Capability

**Promotion mechanism.** A Composed Capability is a *promoted*,
reusable ActionProposal shape. It MUST:
- originate from an ActionProposal that was validated by the Control
  Plane and successfully executed at least once,
- be independently validated on each occurrence (validation is never
  skipped because a shape has succeeded before, until promotion actually
  completes),
- accumulate corroborated successful outcomes — plural, independently
  observed, mirroring the same corroboration discipline the Engineering
  State contract requires for Belief→Knowledge promotion (§2 of that
  contract),
- pass whatever promotion criteria Policy defines (minimum corroboration
  count, minimum diversity of invoking Role/context, absence of any
  associated Contradiction or reverted outcome),
- **never be self-promoted by the Reasoning Plane.** Promotion is a
  Control-Plane-governed, deterministic process — the Reasoning Plane may
  propose a shape repeatedly and it may succeed repeatedly, but the act of
  registering it as a standing Composed Capability is a Control Plane
  decision, structurally identical in spirit to how a Belief only becomes
  Knowledge through a designated promotion process, never by the reasoning
  layer's own assertion.

**Effect on autonomy.** Once promoted, a Composed Capability re-enters the
closed set the Control Plane can enumerate directly (§10.A) and is
selectable at the task's ordinary autonomy Level, without the elevated
human-confirmation default §14 requires for never-before-validated shapes.
This is the mechanism that keeps the Control Plane's validation standard
constant while the *cost* of novelty amortizes over time.

## 10. Candidate generation

Every reasoning cycle's candidate set is assembled from exactly two
sources, both of which MUST enter the **same** Control Plane validation
path with no exceptions:

- **A. Existing registered/promoted capabilities** — the closed set of
  primitive Capabilities (§7) plus any Composed Capabilities promoted so
  far (§9), filtered to those applicable to the current Unknown.
- **B. Novel ActionProposals composed by reasoning** — freshly assembled
  compositions the Reasoning Plane constructs when nothing in (A) fits
  (§8).

**There MUST NOT be a weaker validation path for source B.** Every item in
the checklist in §4 applies identically to a pre-registered candidate and a
freshly composed one. The only difference §14 permits is the autonomy
default applied *after* validation succeeds — never a relaxation of
validation itself.

## 11. Authorization — the exact boundary

```text
Reasoning:  "What should we do?"      — generates and values ActionProposals
Control:    "May we do it?"           — validates, computes Execution
                                          Authorization fresh, gates on
                                          autonomy Level and novelty
Capability: "Execute it."             — performs the authorized effect,
                                          producing an Observation
```

Capability-coverage failure (§7) is a distinct outcome, separate from
ordinary Policy/scope/budget rejection: it means no registered primitive
covers a step the proposal requires, and it escalates as a request to
register a new Capability — an out-of-band, human/engineering action, not
something any cycle of this loop can grant itself, ever.

## 12. Verification

Independent Verification, as specified in the earlier architectural review
this contract builds on, MUST NOT be implemented as an extension of the
generating Reasoning Plane — it is a structurally separate process, with
its own evidence-gathering, that checks a completed PlanStep's
postcondition rather than trusting the generating cycle's own claim of
success. The Reasoning Engine consumes Verification's result exactly as it
consumes any other Observation: classified per Engineering State contract
§10 (`Expected`/`Contradiction`/etc.), folded into Engineering State as new
Evidence, and available as input to the *next* reasoning cycle — Verify is
not a side channel Verification writes to independently of Engineering
State; its output becomes ordinary state that subsequent reasoning reads
fresh, like everything else.

## 13. Replan

Replan is **not** a separate Agent and **not** a special Workflow stage. It
is the same Reasoning Engine cycle re-entered, with a `ReplanTriggered`
event's specific reference (which Belief was contradicted, which PlanStep
is invalidated) as first-class input to the ordinary Identify-Unknowns /
Form-Hypotheses / Plan sequence. There is no separately-implemented
"replanning engine" — a special-cased replan mode would risk drifting out
of sync with the ordinary planning path's own discipline over time, which
is exactly the failure this contract avoids by insisting on one reasoning
procedure for both.

## 14. Autonomy

Autonomy Level determines, without ever requiring a separate Reasoning
Engine implementation per level:

- **Allowed capabilities** — which primitive and Composed Capabilities the
  current Level's Policy permits invoking at all.
- **Novel proposals** — a proposal shape not previously validated and
  promoted always defaults to elevated human confirmation, *regardless* of
  the task's nominal autonomy Level — novelty itself is a standing
  down-shift, not something a high autonomy Level can waive.
- **Human confirmation** — required per Level and per novelty, per the
  Control Plane's authorization computation (§4/§11).
- **Verification requirements** — never optional at any Level; only the
  escalation trigger on Verification failure changes with Level, not
  whether Verification runs.
- **External effects** — gated by Execution Authorization at every Level;
  higher Levels change how much initiative is delegated between human
  confirmations, never how much checking is skipped.
- **Budget** — a hard ledger enforced identically at every Level.
- **Escalation** — the conditions that trigger it are Policy-declared per
  Level, but the mechanism (halt, re-escalate to a human, never
  self-reauthorize) is one mechanism, unchanged across Levels.

**One Reasoning Engine, configured by Policy per Level and per Role — not
a family of separate reasoning engines for different autonomy tiers.**

## 15. Model independence

The Reasoning Engine's contract — the Control Plane/Reasoning Plane
boundary, the ActionProposal lifecycle, the cycle structure — MUST remain
independent of any particular model, provider, model version, prompt, or
inference strategy. A different model consuming the same materialized
Engineering State and producing ActionProposals through the same interface
is a Role/Capability-configuration change, never a change to this
contract. Every Belief, Hypothesis, and ActionProposal MUST record its
generating model/prompt/version as provenance (Engineering State contract
§6) specifically so this independence is auditable, not merely assumed.

## 16. Context selection

```text
Engineering State (full materialized projection)
        │
        ▼
Relevant state — the subset actually pertinent to the current cycle's
        open Unknown/PlanStep, selected via explicit citation-graph
        traversal (Engineering State contract §16), never unscoped search
        │
        ▼
Reasoning context — the relevant state assembled into whatever shape the
        invoked Capability/model interaction requires
        │
        ▼
Model
```

Context selection MUST preserve, without exception, on everything it
passes through: **provenance** (who/what produced it), **contradiction**
status (a contested Belief must not be silently presented as settled),
**temporal validity** (its Execution Context, per Engineering State
contract §7), **source trust** (origin_class and source_trust, per
Engineering State contract §4), and **budget** (context assembly itself
consumes budget and MUST be accounted against the same ledger §4 enforces
for Capability invocation).

## 17. Long-running reasoning

The Reasoning Engine MUST resume purely from Engineering State's
materialized projection — never from hidden model memory, a model's own
context window persisted across a gap, or any cache the Reasoning Plane
held that isn't itself derivable from the event log. A task paused for
hours or days resumes by re-reading current materialized state exactly as
any ordinary cycle would (Engineering State contract §9, State replay), at
whatever cycle boundary it left off. This is a direct consequence of §3 and
Engineering State contract invariant 17 — it is restated here because
long-running reasoning is the scenario where a hidden-state shortcut would
be most tempting and most dangerous.

## 18. Multi-agent reasoning

Multiple Reasoning Engine instances (Roles) interact exclusively through
Engineering State's multi-agent primitives (Engineering State contract
§14), never through a side channel this contract introduces independently:

- **Ownership** — each Plan/PlanStep/open Hypothesis has exactly one
  owning Role at a time; an ActionProposal targeting a PlanStep another
  Role owns fails Control Plane validation (§4).
- **Leases** — a Role acquires a bounded, expirable claim before
  investigating a Hypothesis another Role might also pursue, preventing
  duplicate work without permanently blocking a stalled Role's peers.
- **Causality** — cross-Role dependencies (Role B's proposal depending on
  Role A's Belief) are recorded via explicit event causal references, not
  inferred from timing.
- **Shared state** — every Role reads the same materialized Engineering
  State projection fresh each cycle (§3); no Role holds a private,
  divergent view.
- **Conflicting proposals** — two Roles proposing incompatible actions
  for the same target surface as a Contradiction (Engineering State
  contract §10/§14), resolved via a Policy-defined authority hierarchy or
  human escalation before either executes.
- **Conflicting beliefs** — a foreign Role's Belief retains its full
  provenance chain and is never consumed at the same default trust as a
  Role's own directly-derived Belief (Engineering State contract §14).
- **Delegated reasoning** — one Role MAY request another Role's
  investigation of a sub-Unknown (one of the legitimate composed-action
  shapes named in §8); the delegation itself is an ActionProposal, subject
  to the same validation and lease/ownership checks as any other.

## 19. Security

- **Repository content** cannot manufacture Human Instruction, Human
  Approval, or Policy Authorization — enforced structurally by
  `origin_class` (Engineering State contract §4/§15), which the Control
  Plane checks as part of ordinary candidate validation (§4). A Capability
  that reads repository content is structurally incapable of emitting
  `human_directive`-class Evidence regardless of that content's textual
  form, and this holds identically whether the resulting Belief drives a
  pre-registered candidate selection or a freshly composed ActionProposal.
- **Model output** (a Belief, a Hypothesis, an ActionProposal itself) is
  always data with a confidence and a provenance chain — never
  self-authorizing, per §6's explicit rule and §11's boundary. A model
  cannot escalate its own output's authority by constructing a more
  elaborate or more confident-sounding proposal; the Control Plane's
  checklist has no persuasion input.
- **ActionProposal** validation (§4) independently re-checks scope,
  origin_class of cited Evidence, and authorization for every proposal —
  including ones shaped by injected repository content — so an injection
  attempting to widen scope or fabricate authorization fails at the same
  gate a legitimate but merely mistaken proposal would fail at.
- **Capability** invocation never grants broader access than what its own
  registration declares; a Capability that reads content does not thereby
  gain the ability to write, and a Capability scoped to one Workspace does
  not gain access to another's.
- **Policy** rules are never mutable by anything this contract governs —
  Reasoning and Control both consult Policy; neither writes to it.
- **Human authorization** — the only path by which `Human*`-class events
  and pinned Approval are appendable is the verified-human-originated path
  Engineering State contract §8 already defines; no Capability, no
  ActionProposal, and no Composed-Capability promotion process may
  fabricate one.

**No untrusted content, model output, or proposal — however novel,
however corroborated by prior success — can cross into Human/Policy-level
authority.** The chain of custody for authority runs exclusively through
the verified-human-originated event path and Policy's own rule evaluation;
nothing in the Reasoning Engine's design creates a second route.

## 20. Failure and stopping

Reusing, not replacing, the Observation classification already defined in
Engineering State contract §10, extended here to the cycle's overall
outcomes:

- **Success** — the Plan's Completion criteria are independently Verified.
- **Failure** — a genuine Contradiction (§10 of that contract) invalidates
  a dependent PlanStep; handled via Replan (§13), not treated as terminal.
- **Blocked** — Execution Authorization or capability-coverage denies a
  candidate; escalates to Policy/human or to a capability-gap request
  (§11), never silently retried as though it were an Anomaly.
- **Uncertainty** — an Unknown remains below its confidence-and-sufficiency
  bar with investigation options still available; the loop continues
  gathering Evidence, it does not stop.
- **Escalation** — every investigation option is exhausted with the
  Unknown still unresolved, or Safety Validity fails for a pending Action;
  a human is asked, with the full trace of what was tried.
- **Refusal** — the Reasoning Plane MAY select no candidate this cycle
  when nothing surviving Control Plane validation is judged worth its cost
  — a legitimate, recordable outcome, not an error.
- **Budget exhaustion** — recorded explicitly as its own terminal reason,
  never narrated as "nothing more to try" when the true cause was running
  out of budget (Engineering State contract's own stop-reason discipline,
  generalized from the existing Context Discovery engine's practice).
- **Safety invalidation** — Safety Validity fails mid-execution; the loop
  halts and re-escalates per Engineering State contract §13, never
  proceeds and never self-reauthorizes.
- **Diminishing returns** — the same-Unknown retry guard: N unproductive
  cycles on the same Unknown without a confidence/sufficiency change
  forces either a downgrade of that Hypothesis's reachable confidence
  ceiling or escalation, preventing an unbounded loop on a failed
  strategy independent of the overall task budget.

## 21. Non-negotiable invariants

1. Reasoning MUST NOT grant itself authorization, under any
   circumstance, regardless of confidence, corroboration, or prior success
   of a similarly-shaped proposal.
2. An ActionProposal MUST be inert — incapable of producing any effect —
   until it has passed Control Plane validation and holds a currently
   valid, freshly-computed Execution Authorization.
3. Every candidate action, whether a pre-registered/promoted Capability
   selection or a freshly composed ActionProposal, MUST pass the identical
   Control Plane validation checklist; there MUST NOT be a second, weaker
   path for either source.
4. Novelty MUST NOT bypass Policy. A never-before-validated proposal shape
   MUST default to elevated human confirmation regardless of the task's
   nominal autonomy Level.
5. Primitive Capabilities MUST remain a closed set; the Reasoning Plane
   MUST NOT be able to invent a new world-facing channel by proposal,
   confidence, or argument.
6. Composition MUST remain open; the Control Plane MUST NOT require every
   useful sequence or parameterization of existing primitive Capabilities
   to have been pre-registered before it can be proposed.
7. Replan MUST operate through Engineering State — the same reasoning
   cycle re-entered with a `ReplanTriggered` reference as input, never a
   separate Agent or Workflow stage.
8. Verification MUST remain structurally independent of the generating
   Reasoning Plane, with its own evidence-gathering; its result feeds
   Engineering State as ordinary Observation, never a privileged
   self-report.
9. The model MUST NOT become the source of execution authority under any
   framing — not through confidence, not through elaborate justification,
   not through prior success.
10. The Control Plane MUST NOT become the intelligence bottleneck by
    freezing the closed candidate set permanently — it MUST expand only
    through the governed, corroboration-gated Composed Capability
    promotion process (§9), never by loosening validation itself.
11. No hidden cross-cycle reasoning state may become authoritative; the
    Reasoning Engine MUST read only the current materialized Engineering
    State projection at the start of every cycle.
12. Model replacement MUST NOT invalidate Engineering State — a different
    model consuming the same materialized state and producing
    ActionProposals through the same interface is a configuration change,
    never a breaking one, per §15.
13. Capability-coverage failure MUST be recorded as a distinct outcome
    from ordinary Policy/scope/budget rejection, and MUST escalate as a
    request to register a new Capability, never silently worked around by
    substituting an unrelated candidate.
14. Repository content, however textually imperative or elaborately
    corroborated by an ActionProposal's own composition, MUST NOT be
    capable of producing `human_directive`-class Evidence or any
    `Human*`-class authorization event.
15. Neither the Reasoning Plane nor the Control Plane may bypass any
    invariant in `ENGINEERING_STATE_ARCHITECTURE.md` — this contract adds
    a reasoning-and-action-proposal layer on top of that contract; it
    never substitutes for or relaxes any part of it.

---

## 22. Known adjacent documentation, not silently conflated

`docs/graphforge/AGENT_FRAMEWORK.md`'s per-agent `Plan → Select Tool →
Execute → Observe → Decide` loop is the historical predecessor concept
this contract generalizes and supersedes for anything beyond that
document's own "today" framing — see the pointer note added to that
document. `docs/graphforge/ARCHITECTURE.md`'s `Selector`/`ISelector`
resolves *which Agent (Role) runs* for a given Goal — a different,
higher level than this contract's *action selection within one Role's
reasoning cycle* (§5); the two are not the same mechanism and should not be
conflated. `app/context_pipeline/reasoning/`'s existing "candidate"
terminology (in `investigation.py`/`capabilities.py`) refers narrowly to
*candidate repository-identity matches* during entity resolution — a
different, narrower usage than this contract's *candidate actions* (§10);
both terms coexist in the repository without one superseding the other,
and neither should be read as evidence about this contract's scope.
