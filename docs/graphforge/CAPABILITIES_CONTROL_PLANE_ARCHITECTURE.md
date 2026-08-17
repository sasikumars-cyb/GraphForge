# GraphForge — Capability & Control Plane Architecture Contract

**Status:** NORMATIVE. Accepted, **as amended by the Integrated Frontier
Architecture Amendment (§23)**. This document is a canonical architecture
contract, not a proposal, not a discussion, and not one design option among
several.

> **This document is the canonical normative specification for GraphForge
> Capabilities, the Control Plane, Execution Authorization, Independent
> Verification's structural boundary, Observation classification, Goal
> satisfaction, Workspace lifecycle, and Policy governance.**
>
> Any implementation, agent behavior, architectural decision, or future
> design proposal concerning Capability, Primitive Capability, Composed
> Capability, Tool, Action, ActionProposal, the Control Plane,
> Authorization, Authorization Grants, Compensation Reservations, Policy
> evaluation or governance, capability composition, execution
> authorization, capability registration, or capability promotion **MUST
> conform to this contract.**
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

## 0. Relationship to the other canonical contracts

Three peer canonical contracts exist. **None overrides another; each is
authoritative only for its own explicitly-scoped domain**, and this document
never restates a primitive the other two already define:

```text
ENGINEERING_STATE_ARCHITECTURE.md          [FROZEN — unchanged by this amendment]
    canonical for: state, Evidence, Observation, Belief, Hypothesis,
    Assumption, Prediction, Decision, Plan/PlanStep, events, temporal
    state (Execution Context), human authority, state reconstruction
            ◄──────────────►
REASONING_ENGINE_ARCHITECTURE.md           [FROZEN — unchanged by this amendment]
    canonical for: reasoning, the Reasoning Plane, ActionProposal as a
    reasoning output, action selection, reasoning cycles, autonomy
            ◄──────────────►
CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md   ← this document [AMENDED]
    canonical for: the Capability model, Capability registration and
    promotion, the Control Plane's validation and authorization pipeline,
    Authorization Grants, Prediction admissibility, artifact identity,
    Independent Verification's structural boundary, Observation
    classification, Goal satisfaction, compensation and recovery,
    Workspace lifecycle, and Policy governance
            ↓
docs/graphforge/*.md — target-design documents; where AGENT_FRAMEWORK.md
or ARCHITECTURE.md describes tool/capability/authorization concepts, this
contract wins on any conflict
            ↓
docs/adr/*.md, docs/rfcs/*.md — historical unless explicitly reconciled
            ↓
docs/handbook/*.md, docs/presentation/*.md — never authoritative alone
            ↓
Implementation (backend/app/, frontend/src/)  →  Tests
```

Every Control Plane action defined here writes to Engineering State
exclusively through that contract's event model, and MUST NOT bypass any of
its invariants.

### 0.1 Known terminology collisions — flagged, NOT silently resolved

**The word "Capability" already has four distinct, unrelated meanings in
this repository, none of which is this contract's meaning.** This is the
single largest comprehension hazard in this document and is recorded here
rather than resolved by unilateral renaming:

| Existing usage | What it actually means there | Relationship to this contract |
|---|---|---|
| `app/context_pipeline/reasoning/capabilities.py` — `Capability`, `CAPABILITIES` | An **information requirement** Context Discovery needs satisfied (architecture, deployment topology, permissions) — a knowledge-gap category with weighted confidence signals | **Not** a world-facing channel. Unrelated concept sharing a name. |
| `app/ai/providers/registry.py` — `Capability` (e.g. `VISION`, `REASONING`) | LLM **provider feature flags** | Unrelated. |
| `app/knowledge/registry.py` — `ProviderCapability` | What an external knowledge **connection** supports at transport level | Adjacent but narrower; not this contract's Capability. |
| `app/agents/planning/classifier.py` — "capabilities" | **Solution-domain features** detected from a problem statement (product capabilities) | Unrelated. |

None of these is renamed, deprecated, or reinterpreted by this document.
Any future reconciliation requires its own explicit architectural decision.
Until then, **a reader encountering "Capability" in this repository MUST
determine which of the five meanings applies from its module context.**

**`app/tools/` (`ITool`, `ToolRegistry`, `ToolSpec`, `ToolInput`,
`ToolResult`)** is the closest existing analogue to this contract's **Tool**
layer (§1) — an implementation registry, not a Capability contract. It is
**non-conforming as a Capability model** and MUST NOT be treated as already
satisfying §3. `ToolHealth.PERMISSION_DENIED`/`AUTH_FAILED` are **not** a
violation of §4: reporting that an *external system* refused a call is an
Observation, not a Tool authorizing itself.

**Refinement of `REASONING_ENGINE_ARCHITECTURE.md` §6.** That contract lists
an `authorization state` as a field on the ActionProposal, and describes a
single proposal-level "Execution Authorization computed fresh." That
description is **refined, not contradicted**, by §7 of this document:
authorization is a **separate, per-Action, single-use, TTL-bounded Grant**,
never a field on the proposal and never proposal-scoped for a multi-Action
composition. Where the two readings differ, **this document governs for the
mechanics of authorization**, and the frozen contract's own invariants are
strengthened rather than weakened by the refinement.

## Preventing architectural drift

If implementation, a new ADR, a new design document, or an agent's own
reasoning appears to conflict with this contract: (1) identify the conflict
explicitly, naming the invariant and the file; (2) do not silently
reinterpret the contract to make the conflict disappear; (3) do not weaken
an invariant because the current implementation is simpler without it, the
schema is inconvenient, an API doesn't support it, a model can't currently
reason about it, migration is hard, tests are hard to write, or it would be
faster to skip; (4) name the architectural decision required; (5) update
this contract only through that explicit decision. **Architecture comes
first.**

---

# The Contract

## 1. Primitives — precise boundaries

- **Capability** — a registered, versioned **declaration** of one kind of
  effect or observation the system is able to perform. It is a *type*,
  existing at registration time, independent of any use. It declares what is
  possible and what permission that possibility requires; it never declares
  that any particular use is permitted.
- **Primitive Capability** — a Capability whose effect is irreducible within
  this architecture: a genuine channel of contact with the world. It cannot
  be expressed as a composition of other registered Capabilities. **The set
  is closed** (Reasoning Engine contract §7).
- **Composed Capability** — a promoted, named, reusable recipe over
  already-registered Capabilities, produced only through the governed
  process in §10. Selectable like a primitive; internally a fixed,
  previously-validated sequence. **It never introduces a new world-channel** —
  composition changes reach, never surface area.
- **Tool** — the concrete implementation fulfilling a Capability's contract.
  **Capability ≠ Tool.** The Capability is the interface and the unit of
  Policy reference; the Tool is one swappable implementation of it. A
  Capability's identity MUST survive its Tool being replaced.
- **Action** — one fully-parameterized instance of a Capability invocation.
  Capability = possibility; Action = one instance of it.
- **ActionProposal** — inert structured data from the Reasoning Plane naming
  one or more Actions, possibly a novel composition. Defined normatively in
  Reasoning Engine contract §6; not restated here.
- **Execution** — running an **authorized** Action against a target. The
  only point in the entire chain where the world changes.
- **Workspace** — an isolated, bounded environment that a Capability may
  require as its operating context. Not a Capability; a *target context* a
  Capability's isolation requirement may demand. Its lifecycle is defined in
  §19.

## 2. Four things that must never be collapsed

| Question | Object | Owner |
|---|---|---|
| What does the agent want to do? | ActionProposal | Reasoning Plane |
| What is the system capable of doing? | Capability (registered) | Capability Registry |
| What is the system allowed to do? | Authorization Grant (§7) | Control Plane |
| What did the system actually do? | Observation | Execution → Engineering State |

**Intent is not a separate persisted primitive.** ActionProposal already
carries what would otherwise be lost; reifying Intent separately would
duplicate it. What MUST NOT happen is treating a *formalized* proposal as
still-informal: once created, its content is fixed, and revision produces a
new proposal, never a silent edit.

**Authorization MUST NOT be modeled as a field on the ActionProposal or the
Action.** A proposal's *content* is stable, but authorization's *validity* is
not — Safety Validity can fail between validation and dispatch. A boolean
`authorized: true` on the proposal would make a lapsed grant look
permanently valid, which is exactly what Engineering State contract §13
exists to prevent.

**There MUST NOT be a single mutable status field that transitions one
record through "wanted → requested → permitted → done."** Each state in §8
is evidenced by a distinct record with its own timestamp and its own
independent invalidation conditions.

## 3. The Primitive Capability contract

Every Primitive Capability MUST declare all of the following. Several are
**ceilings or floors** narrowed per-invocation by the Action — never fixed
concrete values duplicated at each call site.

| Field | Requirement | Why it is fundamentally required |
|---|---|---|
| **Identity** | MUST | The unit Policy rules, promotion records, Grants, and Evidence provenance reference. MUST survive Tool replacement. |
| **Version** | MUST | Engineering State contract §9 requires invoked Capability versions be pinned for Reasoning replay to be attemptable at all. |
| **Input schema** | MUST | Parameter validation (§6) has no content without it. |
| **Output schema** | MUST | Defines the shape of the Observation this Capability produces — what Verification interprets, and what Prediction falsifiability (§13) is checked against. |
| **Applicability** | MUST | What lets candidate generation filter to a specific open Unknown. |
| **Parameter constraints** | MUST, distinct from schema | Schema = shape/type; constraints = legality. Different pipeline stages consume each. |
| **Target type** | MUST | Scope validation cannot check a target against Plan Scope without knowing what kind of thing is targeted. |
| **Scope ceiling** | MUST — as a ceiling only | The maximum boundary this Capability could ever touch. The *concrete* scope of one invocation is declared on the Action and MUST fall within this ceiling. |
| **Reversibility class** | MUST — exactly one of `reversible` / `compensatable` / `irreversible` (§18) | Determines recovery obligations, Compensation Reservation requirements, and the strength of the authorization gate. |
| **Named compensating Capability** | MUST, **if and only if** class is `compensatable` | "Compensatable" is meaningless without naming the compensator; naming it at registration makes compensation pre-reviewed rather than improvised under duress. |
| **Risk class** | MUST — as a floor | A composition MAY self-assess *higher* risk; it MUST NOT assess lower than the constituent floor. |
| **External visibility** | MUST — static, closed vocabulary | The exact test Engineering State contract §12 uses for mandatory Decision recording. MUST NOT be per-invocation self-assessed. |
| **Cost class** | MUST | Feeds Reasoning-Plane valuation and Budget validation. |
| **Required authorization** (declared, never evaluated) | MUST | §4 — the central boundary. |
| **Preconditions** (factual) | MUST | State conditions distinct from permission. |
| **Side-effect declaration** | MUST — closed vocabulary: `read-only` / `workspace-write` / `external-read` / `external-write` | Scope validation and the security boundary structurally depend on this being machine-checkable, not prose. |
| **Execution Context requirements** | MUST | Which dimensions of Execution Context (Engineering State contract §7) this Capability's correctness depends on. |
| **Isolation requirements** | MUST | Fresh Workspace / shared / none. The direct input to the Workspace boundary (§19). |
| **Artifact production declaration** | MUST, if the Capability produces a consumable artifact | Declares that this Capability produces an artifact and MUST record its identity per §14. |
| **Registration provenance** | MUST | Who/what registered it and when — load-bearing for Composed Capabilities, whose promotion must itself be auditable. |
| **Timeout** | MUST | The concrete mechanism by which an ambiguous outcome becomes *recognizable* as ambiguous. Without it, `ActionOutcomeUnknown` has no trigger. |
| **Postcondition shape** | MAY — informational only | The **binding** postcondition belongs to the PlanStep; baking one into a general-purpose Capability would force every invocation into a single fixed meaning. |
| **Resource limits** | MUST declare; MUST NOT self-enforce | Enforcement belongs to Control Plane/Policy — a component MUST NOT be the sole judge of its own limits. |

### 3.1 Reversibility taxonomy

| Class | Meaning | Recovery obligation |
|---|---|---|
| **`reversible`** | Effect undone by discarding state; no external observer | Discard via custodial capability (§18.3) |
| **`compensatable`** | Cannot be undone, but a declared *forward* Capability offsets it | Named compensator (above) + Compensation Reservation (§18.2) |
| **`irreversible`** | No compensating action restores the prior world state | Forward recovery or human intervention only; stronger gate (§18.4) |

**A compensating Capability MUST NOT itself be of class `compensatable`.**
It MUST be `reversible` or `irreversible`-and-terminal. Compensation chains
are depth-1 by construction; recursive compensation is prohibited because it
permits infinite regress and unbounded Reservation chains.

## 4. A Capability MUST NOT own authorization

A Capability declares exactly one permission-related thing:
**`required_authorization`** — a static reference to which Policy
dimension(s), Constraint class(es), and human-approval class(es) this *kind*
of action needs. It is fixed at registration and never re-evaluated by the
Capability itself.

**The Control Plane is the sole evaluator of whether that requirement is
currently satisfied.** The boundary MUST be structural, not disciplinary: a
Tool MUST have no code path that computes, asserts, or infers its own
authorization. A Tool receives a dispatch instruction only after the Control
Plane has independently confirmed authorization and issued a single-use
Grant (§7). It MUST NOT ask "am I allowed," because asking implies a
determination it has no standing to make or to trust the result of.

**Neither the component that proposes an Action nor the component that would
execute it may compute or assert its own authorization.**

Reporting that an *external system* denied a call is not a violation — that
is an Observation, classified per §16.

## 5. Control Plane responsibilities

| Concern | Owner | Note |
|---|---|---|
| Policy rule **evaluation** | Control Plane | Consults Policy; MUST NOT author, amend, or weaken a Policy rule (§20). |
| Scope, Constraints, Budget | Control Plane | Checked against approved Plan Scope and the current ledger. |
| Authorization | Control Plane, exclusively | §4, §7. |
| Safety Validity | Control Plane | Authoritative **only** at the final gate (§6). |
| Preconditions | Control Plane | Factual/state checks. MAY invoke a read-only Capability to verify. |
| Prediction admissibility | Control Plane | Deterministic syntactic falsifiability check (§13). |
| Artifact identity precondition | Control Plane | §14, checked at Action Eligible. |
| Risk / Reversibility / External visibility | Control Plane | Classification that **routes** — not itself pass/fail. |
| Execution Context | Control Plane | Current context vs. Capability requirements **and** vs. what the Plan assumed. |
| Ownership / leases | Control Plane | Engineering State contract §14. |
| Capability coverage | Control Plane | Produces the distinct Capability-Gap outcome (§11). |
| Parameter validation | Control Plane | Against declared schema and constraints. |
| Stale approval detection | Control Plane — **not a separate mechanism** | It *is* Safety Validity re-evaluation. |
| Novelty classification | Control Plane | Whether this shape was previously validated and promoted (§10). |
| Grant issuance and consumption | Control Plane | §7. |
| **Observation classification** | Control Plane | §16 — deterministic, reproducible, fixed order. |
| **Goal Satisfied evaluation** | Control Plane | §17 — deterministic predicate. |
| **Verifier selection** | Control Plane, per Policy | §15 — never the generating Role. |
| **Workspace lifecycle** | Control Plane | §19. |
| **Compensation Reservation issuance** | Control Plane | §18.2 — never on request. |

**Explicitly NOT the Control Plane's:** candidate *generation* and value
*judgment* (both exclusively Reasoning Plane), Policy *rule-authoring*
(§20), and Capability *implementation* (the Tool layer).

## 6. The authorization pipeline

Proposal-level checks and per-Action authorization are **two distinct
phases**. The first establishes a property of the artifact; the second
issues time-bounded permission for one Action.

```text
ActionProposal
      ▼
Structural Validation          — well-formed object
      ▼
Capability Coverage ─────────► CAPABILITY-GAP  (§11 — a distinct terminal
      ▼                          outcome; escalates as "register a new
Parameter Validation             Capability"; never a Policy denial,
      ▼                          never worked around by substitution)
Scope Validation
      ▼
PREDICTION ADMISSIBILITY       — §13: every Action's Prediction MUST name a
      ▼                          target observable present in the referenced
                                 Capability's declared output schema, carry a
                                 mechanically evaluable falsification
                                 condition, define its evaluation procedure,
                                 and bind its Execution Context. A Prediction
                                 that cannot be mechanically evaluated FAILS
                                 CONFORMANCE. The necessary-condition
                                 relationship to the PlanStep postcondition
                                 is declared here and recorded for
                                 longitudinal calibration.
      ▼
Novelty Classification         — previously validated/promoted shape?
      ▼                          recorded here; ENFORCED only at the gate
Policy Validation              — preliminary; grants NOTHING
      ▼
Constraint Validation
      ▼
Execution Context Validation
      ▼
Risk / Reversibility / External-Visibility Classification   (routing)
      ▼
Budget Validation
      ▼
Ownership / Lease Validation
      ▼
╔══════════════════════════════════════════════════════════╗
║  PROPOSAL CONFORMANT (§8)                                 ║
║  A property of the artifact. Grants nothing. Permits      ║
║  nothing. Necessary, never sufficient.                    ║
╚══════════════════════════════════════════════════════════╝
      │
      ▼
  ┌────────── per-Action dispatch loop, in composition order ──────────┐
  │                                                                     │
  │  ACTION ELIGIBLE check (§8) — for THIS Action, right now:           │
  │     preconditions still hold given prior steps' Observations        │
  │     AND ARTIFACT IDENTITY PRECONDITION satisfied (§14) — the        │
  │         declared expected artifact identity matches the recorded    │
  │         identity of the instance actually in effect                 │
  │     AND Execution Context requirements currently met                │
  │     AND budget still available   AND lease still held               │
  │     AND no prior Action in this composition halted                  │
  │        ▼                                                             │
  │  ╔═══════════════ FINAL GATE — atomic, last possible moment ══════╗ │
  │  ║   Human Approval (pinned, historical)                          ║ │
  │  ║   AND Policy (RE-confirmed now, not reused from above)         ║ │
  │  ║   AND Safety Validity (evaluated NOW — the ONLY place it is    ║ │
  │  ║       authoritative)                                            ║ │
  │  ║   AND if Novel: human confirmation actually obtained            ║ │
  │  ║   AND if class is `compensatable`: a valid Compensation         ║ │
  │  ║       Reservation is issued alongside (§18.2)                   ║ │
  │  ╚════════════════════════════════════════════════════════════════╝ │
  │        ▼                                                             │
  │  AUTHORIZATION GRANT issued (§7): names THIS Action, bounded TTL,   │
  │  single-use, unconsumed                                             │
  │        ▼                                                             │
  │  Grant consumed → EXECUTION STARTED → Capability/Tool dispatched    │
  │        ▼                                                             │
  │  EXECUTION COMPLETED  |  ActionOutcomeUnknown  (distinct, §8)       │
  │        ▼                                                             │
  │  OBSERVATION RECORDED → classified per §16                          │
  │        ▼                                                             │
  │  Contradiction / Blocked / ActionOutcomeUnknown  →  HALT the        │
  │  composition (§9). Remaining Actions MUST NOT inherit anything.     │
  └─────────────────────────────────────────────────────────────────────┘
      │
      ▼
Denied at any point → routed to the SPECIFIC failing reason
(capability-gap / scope-violation / prediction-inadmissible /
policy-denial / constraint-violation / budget-exhausted /
stale-safety-validity / lease-conflict / awaiting-human /
artifact-identity-mismatch / precondition-invalidated-by-prior-step) —
NEVER a generic failure.
```

**Safety Validity MUST NOT be evaluated authoritatively anywhere earlier
than the final gate.** Checking it mid-pipeline and then running further
stages (some of which take real time, and some of which may invoke
read-only Capabilities for precondition checking) reopens exactly the
staleness window Engineering State contract §13 exists to close.

## 7. Authorization Grant

An Authorization Grant is a first-class object, separate from both the
ActionProposal and the Action. It MUST:

- **reference exactly one Action** — never a proposal, never a composition,
  never a Capability in the abstract;
- carry a **bounded TTL**, after which it is expired and unusable;
- be **consumed on dispatch**, and thereby be permanently unusable again;
- be **independently evaluated** at the final gate from current Human
  Approval, current Policy, current Safety Validity, and novelty
  confirmation;
- **never be reused** for another Action, including a retry of the same
  Action — a retry is a new Action attempt and requires a new Grant;
- **never be inherited** by later Actions in a composition.

A Grant is permission to *attempt*, never a prediction or guarantee of
success (§8).

### 7.1 Persistence — durable Engineering State events

**Authorization Grants are durable Engineering State events. The Control
Plane is their sole author.** This is forced, not chosen: Engineering State
invariant 5 guarantees State replay unconditionally and invariant 20 says
nothing outside the durable log is authoritative. A Grant living only in
Control Plane memory would make "why was this authorized at 14:32"
unanswerable, falsifying that guarantee for the single most consequential
fact in the system.

Event classes: `AuthorizationGranted`, `AuthorizationConsumed`,
`AuthorizationDenied`, `AuthorizationInvalidated`.

**Denials MUST be recorded.** Reconstructing why something *did not* happen
is as forensically necessary as why it did, and §6's "route to the specific
failing reason" requires a durable home.

**Expiry is derived** from issuance time plus TTL and MUST NOT be separately
persisted as an event — consistent with the standing "derived, never stored"
discipline.

**Append authorization:** only the Control Plane may append
`Authorization*`-class events — a new append-authorization class alongside
Engineering State contract §8's existing `Human*` rule.

**Recorded payload, sufficient for forensic reconstruction:** grant
identity; Action identity; Capability identity + version; Policy Version
identity; Human Approval reference (content hash) where applicable; Safety
Validity result **and its evaluated inputs**; Execution Context; issuance
time; TTL; novelty classification; and the specific decision inputs.
Consumption and outcome link forward via the Action's stable identifier.

**Authority boundary:** the Control Plane is the sole author and evaluator;
Engineering State is the sole durable home. The Control Plane MAY hold an
ephemeral working copy for dispatch, but it is derived and never
authoritative.

> **Authorization is permission, never evidence.** An Authorization record
> MUST NOT be cited as Evidence supporting any Belief about the world.

## 8. The state ladder — none implying the next

Each state below is a distinct, separately-evidenced condition. **The
earlier state is a necessary precondition for the later one; the earlier
state NEVER implies the later one.** No state may be inferred from another's
presence or absence, and no single mutable field may represent this
progression (§2).

| # | State | What it means | What it explicitly does NOT mean |
|---|---|---|---|
| 1 | **Proposal Conformant** | The ActionProposal, as an artifact, satisfies every proposal-time check in §6, including Prediction admissibility. | **NOT** approved. **NOT** authorized. **NOT** executable. **NOT** safe to execute. It grants nothing whatsoever. |
| 2 | **Action Eligible** | One specific Action is, at this moment, unblocked: preconditions hold, artifact identity matches, Execution Context met, budget remains, lease held, no prior Action halted. | **NOT** permission. Eligibility means "nothing structurally blocks it," never "you may do it." |
| 3 | **Action Authorized** | A valid, unexpired, unconsumed Grant (§7) exists naming this exact Action. **The only state permitting dispatch.** | **NOT** a property of the proposal or Action. **NOT** a prediction of success. **NOT** transferable. |
| 4 | **Execution Started** | The Grant was consumed and the Tool was dispatched. | **NOT** that any effect occurred. |
| 5 | **Execution Completed** | The invocation terminated with a **determinate** outcome. | **NOT** success. `ActionOutcomeUnknown` is explicitly **not** Execution Completed — a distinct terminal state of indeterminacy requiring reconciliation. |
| 6 | **Observation Recorded** | The raw result was recorded immutably with provenance and Execution Context, and classified per §16. | **NOT** implied by Execution Completed — recording can itself fail. **NOT** verification. |
| 7 | **Independently Verified** | A structurally independent Verification process (§15) confirmed the PlanStep's pinned postcondition, bound to artifact identity (§14). | **NOT** implied by a successful-looking Observation. **NOT** implied by `Expected`. |
| 7b | **VerificationBlocked** | Verification could not obtain authorization to run, or could not be performed. | **Neither Verified nor Failed.** Blocks Goal Satisfied (§17) and MUST escalate. |
| 8 | **Goal Completion Claimed** | The Reasoning Plane believes the Goal's completion criteria are met. A **Belief** (Engineering State contract §6). | **NOT** a state transition. **NOT** authoritative. **Has no authority whatsoever.** |
| 9 | **Goal Satisfied** | The §17 predicate holds, evaluated by the Control Plane. | **NOT** established by successful Actions, absence of failure, `Expected` classifications, or the Reasoning Plane's own claim. |

**Goal Delivered** and **Goal Accepted** are defined in §17 and are
**independent states, not later entries in this ladder.**

### 8.1 The non-implications, stated normatively

- **Authorization ≠ Execution Success.** A Grant permits an attempt. It
  predicts nothing.
- **Execution Success ≠ Goal Completion.** An Action succeeding on its own
  terms says nothing about whether a postcondition or completion criterion
  holds.
- **`Expected` ≠ Postcondition Satisfied.** See §16.3.
- **Goal Completion Claimed ≠ Independent Verification.** A completion claim
  from the process that did the work is a Belief, never a verification.

### 8.2 Terminology rulings

**"Proposal Conformant" replaces "admitted."** *Admitted* implies entry into
a privileged set and therefore carries a permission connotation that is
precisely wrong; *structurally validated* understates the check set, which
includes Capability coverage, scope, Prediction admissibility, and
constraints; *validated* alone is already overloaded in this repository and
collides with Independent Verification. Conformance is a standard
engineering property of an artifact measured against a declared contract,
and in no standard usage does conformance imply permission.

The words **approved, authorized, executable, permitted, cleared,** and
**safe to execute** MUST NOT be used as synonyms for Proposal Conformant.

**Conformance is evaluated against a state snapshot.** Everything
time-sensitive is re-checked at Action Eligible and the final gate.

## 9. Composition, halting, and partial execution

- A multi-Action ActionProposal MUST NOT receive one authorization for its
  lifetime. Each Action requires its own Grant (§7).
- On any Action classified **Contradiction**, **Blocked**, or
  **ActionOutcomeUnknown**, the composition MUST halt. Remaining Actions
  MUST NOT proceed and MUST NOT inherit Proposal Conformance as standing
  permission.
- A halted composition with completed prior Actions is a **partial
  execution**. It MUST route to the recovery model in §18 and to Replan
  (Reasoning Engine contract §13) carrying what actually happened — never a
  bare failure status.
- `ActionOutcomeUnknown` MUST additionally block any subsequent Action whose
  preconditions depend on the unknown outcome, until reconciliation resolves
  it.

## 10. Capability registration and promotion governance

- **Registration of a Primitive Capability is an out-of-band engineering
  act**, performed by humans through whatever governed process Policy
  defines. No reasoning cycle, no ActionProposal, and no Capability-Gap
  escalation may register a Primitive Capability automatically.
- **Promotion to Composed Capability** follows Reasoning Engine contract §9
  without modification: it MUST originate from a validated, successfully
  executed ActionProposal; MUST be independently validated on each
  occurrence until promotion completes; MUST accumulate corroborated,
  independently-observed successful outcomes; MUST pass Policy's promotion
  criteria; and MUST NOT be self-promoted by the Reasoning Plane.
- **Promotion changes the autonomy default, never the validation standard.**
- A Composed Capability MUST NOT expand the union of side-effects, scope
  ceilings, external visibility, or reversibility class severity of its
  constituent Capabilities.

## 11. Capability-Gap

A Capability-Gap is a **distinct terminal outcome**, not a rejection: no
registered Primitive or Composed Capability covers a step the proposal
requires. It MUST:

- be recorded distinctly from Policy denial, scope violation, budget
  exhaustion, and lease conflict;
- escalate as a request to register a new Capability — an out-of-band human
  or engineering action (§10);
- **never** be worked around by substituting a different Capability,
  degrading the proposal, or approximating the missing step.

## 12. Security boundary

- **Repository content** MUST NOT be capable of producing
  `human_directive`-class Evidence or any `Human*`-class event, enforced
  structurally by `origin_class` at the point of Observation.
- **Model output**, including an ActionProposal itself, is always data with
  provenance and confidence — never self-authorizing. The Control Plane's
  checklist has **no persuasion input**.
- **A Capability's invocation MUST NOT grant broader access than its own
  registration declares.**
- **Policy rules are never mutable** by anything this contract governs (§20).
- **Grants are not credentials the Reasoning Plane holds.** A Grant is
  issued by, and consumed within, the Control Plane's dispatch path. The
  Reasoning Plane MUST NOT be able to obtain, store, replay, or present one.
- The chain of custody for authority runs exclusively through the
  verified-human-originated event path and Policy's own rule evaluation.

## 13. Prediction admissibility

Every Action in an ActionProposal carries a Prediction (Engineering State
contract §3). A Prediction MUST:

1. **identify a target observable** that is present in the referenced
   Capability's declared output schema (§3);
2. **contain a mechanically evaluable falsification condition** — the
   explicit statement of what result makes the Prediction FALSE;
3. **define its evaluation procedure**, decidable against that output;
4. **bind to the relevant Execution Context**, so a context mismatch is not
   misread as falsification;
5. **be a necessary condition of its PlanStep's postcondition** — if the
   Prediction is falsified, the postcondition cannot hold.

**Requirements 1–4 are checked deterministically by the Control Plane at
Proposal Conformance (§6).** A Prediction that cannot be mechanically
evaluated against the Capability's declared output schema **fails
Conformance**; the proposal never becomes Conformant.

**Requirement 5 is a declared architectural property.** It is semantic and
therefore not deterministically checkable; it is recorded at proposal time
and **measured longitudinally for calibration**: a Role whose Predictions
consistently evaluate TRUE while the corresponding postconditions
consistently fail Verification has demonstrably miscalibrated Predictions,
and Policy MAY down-rank, escalate, or restrict that Role.

> **A Reasoning Plane MUST NOT be able to evade Contradiction and Replan by
> writing technically falsifiable but semantically irrelevant Predictions.**
> Syntactic inadmissibility is blocked at the gate; semantic irrelevance
> produces a measurable calibration signature.

Not required as separate fields, and MUST NOT be added as such: *timeframe*
(subsumed by the Capability's mandatory timeout), *confidence/uncertainty*
(properties of the Belief that motivated the Action, not of the observable),
*provenance* (already universally required), *expected value/range* (one
form of falsification condition, not an independent field).

## 14. Artifact identity

**Definition.** Artifact identity is the **content digest of the specific
produced instance**, recorded at production time, **bound to the Execution
Context of its production and of its verification**.

- An artifact-producing Capability MUST record the produced instance's
  artifact identity in its Observation.
- An Action **consuming** a prior artifact MUST declare the **expected
  artifact identity** as a precondition.
- **Action Eligibility (§6, §8 state 2) MUST verify that identity.** A
  mismatch yields `artifact-identity-mismatch` — not Eligible.
- **Consumers MUST NOT rebuild-and-compare** to establish identity. They
  MUST consume the specific verified artifact instance. Rebuilding is not a
  substitute, because a nondeterministic build produces different bytes from
  identical inputs and would falsely fail.
- **Mutable references MUST NOT serve as artifact identity** — including
  container image tags, branch names, `latest`, and floating version
  specifiers. Immutable digests are required.
- An artifact depending on a **mutable external resource** cannot be fully
  content-identified. It MUST be re-verified at consumption time, or its
  dependency pinned/snapshotted. This limit is stated honestly rather than
  papered over.

> **Verification binds to artifact identity, not merely to a PlanStep. If
> the artifact changes, prior verification MUST NOT carry forward.**

## 15. Independent Verification — the structural boundary

Independence is **causal non-derivation**, not "a different function,"
"a different class," or necessarily "a different model."

### 15.1 The contamination boundary

> **The verifier MAY consume anything that is a fact, or a deterministic
> function of facts. It MUST NOT consume anything that is a Belief,
> Hypothesis, confidence value, rationale, narrative, interpretation, or
> self-assessment produced by the generating Role.**

This is stated as a principle rather than an enumeration, because
enumerations are gamed by whatever is not on the list.

| Verifier MAY consume | Verifier MUST NOT consume |
|---|---|
| Facts | Beliefs |
| Raw Observations | Hypotheses |
| Deterministic functions of facts (including §16 classifications) | Confidence values |
| The **pinned, approval-time** PlanStep postcondition | Rationale or narrative |
| Execution Context | Interpretation |
| The artifacts under verification | Self-assessment or claimed success |

Reading an artifact the generating Role produced is **legitimate and
necessary** — you cannot verify work without inspecting the work. The
artifact is a world fact. Contamination would be accepting the generator's
*description* of the artifact instead of the artifact itself.

### 15.2 Structural requirements

- **Disjoint evidence derivation** — Verification MUST re-derive its
  evidence through its own Capability invocations.
- **Separate evidence namespace** — verifier-produced Evidence is attributed
  to the verifier Role identity, so disjointness is auditable rather than
  merely asserted.
- **Distinct Role with its own Policy binding.**
- **The generator MUST NOT select, configure, parameterize, or supply
  context to influence its verifier.** **Verifier selection is a Control
  Plane / Policy decision.**
- **The postcondition is pinned at Plan approval and content-hashed.** The
  generator cannot restate, soften, or reinterpret it after seeing the
  result.

A **different model or provider** is NOT the definition of independence and
MUST NOT be treated as sufficient. It reduces correlated model failure and
Policy MAY require it for high-risk or irreversible classes. **Adversarial
verification** (verifier tasked to falsify rather than confirm) is likewise
Policy-escalatable for high-risk classes, not a universal requirement.

**Stated limit, honestly:** verification independence does **not** solve
poisoned-source problems. A verifier reading the same compromised repository
content as the generator may reach the same wrong conclusion. The mitigation
is `origin_class`, `source_trust`, and corroboration — the residual risk
Engineering State contract §15 already records.

### 15.3 Verification's own authorization

Verification goes through the **same Control Plane and the same
authorization mechanism** as everything else. **No verification bypass
exists.**

- Policy MUST define a **Verification authorization profile that is not
  derived from the task's autonomy Level**, defaulting to read-only plus
  test-execution in an isolated Workspace.
- **Verification's read-only evidence gathering MUST NOT be revocable by the
  same conditions that halt the generating task.** A Safety Validity failure
  that stops the task's external writes MUST NOT blind the verifier —
  otherwise the system can never diagnose why it halted.
- **If Verification cannot obtain authorization, the PlanStep enters
  `VerificationBlocked`** (§8 state 7b) — neither Verified nor Failed. It
  blocks Goal Satisfied (§17) and MUST escalate.

## 16. Observation classification

### 16.1 Three stages, never conflated

| Stage | Established by | Nature |
|---|---|---|
| **Raw Observation** | Execution environment | Immutable fact. No judgment. |
| **Classification** | **Control Plane, deterministically** | Reproducible function. No interpretation. |
| **Interpretation** | Reasoning Plane | What it *means* for Beliefs. Cannot alter classification. |

**The Control Plane owns Classification.** It MUST be a **deterministic,
reproducible** function: the same inputs MUST yield the same class, and all
inputs MUST be recorded so any classification can be independently re-run
and challenged. That reproducibility, not the owner's identity, is what
makes manipulation detectable.

### 16.2 Fixed evaluation order

The order is normative — a different order yields a different class for the
same Observation:

1. Authorization / dispatch / grant failure → **Blocked**
2. Outcome indeterminate (timeout, ambiguous external write) →
   **ActionOutcomeUnknown**
3. Infrastructure / transport failure signature → **Anomaly**
4. Execution Context mismatch vs. Plan assumption → *not* Contradiction;
   route to context re-check
5. Pinned Prediction evaluated: TRUE → **Expected**; FALSE →
   **Contradiction**; inconclusive → **Uncertain-Outcome**
6. Nothing matched → **Uncertain-Outcome**

Steps 1–3 precede Prediction evaluation because an Action that never really
ran cannot falsify anything.

> **`Expected` requires an affirmative match. Inconclusive evaluation MUST
> NOT become `Expected`.**

### 16.3 The `Expected` firewall

> **`Expected` MUST NOT be cited as evidence toward postcondition
> satisfaction or Goal satisfaction. Only Independent Verification
> establishes a postcondition.**

Three scopes, never conflated: `Expected` (one Action did what was
predicted) → Postcondition Verified (one PlanStep achieved its purpose,
established by §15) → Goal Satisfied (the §17 predicate).

An `Expected` Observation whose postcondition later fails Verification is
not a contradiction of the Prediction — it means the Prediction was a poor
proxy, which is precisely the §13 calibration signal.

**The Reasoning Plane MUST NOT** re-classify an Observation, request
re-classification absent a new Observation, or retry an Action classified
`Contradiction` as though it were an `Anomaly` in order to continue
execution.

## 17. Goal satisfaction

**Goal Completion Claimed** is a Belief produced by the Reasoning Plane. It
**has no authority.** The Reasoning Plane MUST NOT be able to establish Goal
Satisfied under any framing.

**Goal Satisfied** is established **solely by the Control Plane**, as a
deterministic, reproducible predicate over Engineering State.

> **Goal Satisfied ⟺ all four hold:**
>
> 1. **Every Completion criterion of the approved Plan is Independently
>    Verified** (§15).
> 2. **Each verification binds to the artifact identity currently in
>    effect** (§14) — no post-verification artifact drift.
> 3. **No unresolved Contradiction exists against any Completion
>    criterion.**
> 4. **No unresolved `ActionOutcomeUnknown` exists on which any Completion
>    criterion depends.**

Deliberately excluded as subsumed by (1): absence of `VerificationBlocked`
(a blocked verification means the criterion is not Verified); absence of
pending required Actions (their postconditions would not be Verified); final
Execution Context reconciliation (folded into 4).

### 17.1 Three independent states

| State | Means | Established by |
|---|---|---|
| **Goal Satisfied** | The predicate above holds — the work is *correct* | Control Plane, deterministic |
| **Goal Delivered** | The intended external effects are confirmed present in the world | Verification of external state |
| **Goal Accepted** | A human has explicitly accepted the result | Human, exclusively, via the verified-human path |

> **These MUST NOT be modeled as a linear enum or a progression.** Goal
> Delivered may precede Goal Satisfied (something wrong was delivered). Goal
> Accepted structurally requires neither, though Policy MAY require
> Satisfied-before-Accepted for high-risk classes. They are different facts.

## 18. Reversibility, compensation, and recovery

### 18.1 Recovery by class

| Class | Recovery path |
|---|---|
| `reversible` | Discard state via custodial capability (§18.3) |
| `compensatable` | Execute the declared compensating Capability, under its own fresh Grant |
| `irreversible` | Forward recovery or human intervention only |

### 18.2 Compensation Reservation

A Compensation Reservation:

- **preserves eligibility** — it constrains what Policy may *become*, not
  what the Control Plane may *decide*;
- **NEVER authorizes**, and **NEVER creates a future Grant**;
- is **created only by the Control Plane**, never on request, never
  proposable, never speculative;
- is created **only at the moment a `compensatable` Action's Grant is
  issued**;
- applies to **exactly one `(Role, compensating Capability, target)`
  tuple** — never a class, never a wildcard;
- is **total-lifetime bounded** (not merely per-renewal); renewals are
  bounded in count and each is recorded;
- **consumes budget** at issuance and at each renewal;
- **MUST NOT reserve external resources** — it is an internal Policy-evolution
  constraint only, and MUST NOT hold locks, pin branches, or reserve
  external quota;
- is auto-released on: compensation executed; the compensated effect
  independently confirmed settled; expiry; explicit human release;
  emergency Policy.

**What it binds:**

| Denial type | Example | Bound by the Reservation? |
|---|---|---|
| **Categorical** — the compensating Capability is removed from the Role's permitted set, or the permitting Policy rule class is retracted | Routine policy tightening mid-composition | **Yes — blocked during the window** |
| **Situational** — this compensation is unsafe or impossible *now* | Safety Validity false, emergency stop, target corruption, resource loss, lease loss, budget exhaustion | **No — never blocked** |

> **A Compensation Reservation guarantees that recovery remains reachable
> through the ordinary authorization path. It does NOT guarantee successful
> compensation.** It converts "silently stuck" into "loudly stuck."

**The compensating Action still requires its own fresh, single-use,
TTL-bounded Authorization Grant.** Emergency Policy MAY invalidate a
Reservation. When situational denial fires, the system halts, records an
explicit unresolved-state event, and escalates with the full trace.

**A compensating Capability MUST NOT itself be `compensatable`** (§3.1).

### 18.3 Custodial capabilities

A minimal class — destroy one's own Workspace, release one's own lease,
record a reconciliation Observation — that **Policy MUST always keep
authorizable for a Role's own resources, at tenant scope, independent of
task state.** Without it, a Policy tightening permanently strands resources
and prevents the system from telling the truth about its own state.

### 18.4 Irreversible Actions

- MUST require explicit human confirmation **at the moment of the Action**,
  not merely at Plan approval, because no recovery path exists.
- SHOULD be ordered as late as the Plan DAG permits.
- When neither rollback nor compensation is possible, the system MUST record
  an explicit unresolved-state event, halt, and escalate with the full
  trace. It MUST NOT continue as though recovered, and MUST NOT mark the
  Plan cleanly failed. "Failed and clean" and "failed and dirty" are
  different facts.

## 19. Workspace lifecycle

**The Control Plane owns Workspace lifecycle** — creation, leasing,
diagnostic hold, and destruction. The Reasoning Plane MAY only propose;
proposals route through the Control Plane. The thing being isolated MUST NOT
control its own isolation.

A Workspace **has** an identity, a bound Execution Context, and a bounded,
renewable lease. A Workspace **is non-authoritative**.

> **Nothing in a Workspace is ever a source of truth. Any fact about
> Workspace contents that reasoning depends on MUST be recorded as an
> Observation in Engineering State.**

A Workspace **MAY survive across reasoning cycles**, and MUST have its
Execution Context re-validated on resume. If re-validation fails, it is
discarded and rebuilt from Engineering State and Knowledge — nothing
authoritative is lost, because nothing authoritative ever lived there.

| Composition outcome | Workspace disposition |
|---|---|
| **Success** | Destroyed after all needed artifacts are recorded as Observations |
| **Failure / halt** | **Retained under diagnostic hold** with bounded TTL, then destroyed — destroying evidence at the moment of failure is wrong |
| **Pause / awaiting human** | Retained under extended lease; Execution Context re-validated on resume |
| **Replan** | **Not** auto-reused; reuse requires Execution Context re-validation |
| **Crash** | Reclaimed by lease-TTL expiry via an orphan sweep |
| **Authorization loss** | Retained, no further writes; custodial destruction remains available (§18.3) |
| **ActionOutcomeUnknown** | Retained until reconciliation resolves |

**Precedence and safety rules:**

- **Diagnostic hold outranks ordinary lease expiry**, but remains bounded by
  its own TTL. Expiry MUST NOT destroy a Workspace under an active hold.
- **Diagnostic hold yields to data classification.** A Workspace flagged as
  containing secret-class material MUST be destroyed immediately rather than
  retained for diagnostics; only sanitized Observations are kept. A
  Workspace found to contain credentials is an **incident**: destroy and
  escalate.
- **Evidence derived from a later-discovered compromised Workspace MUST be
  marked contested and superseded — never deleted** (Engineering State
  immutability).
- **Policy MUST cap concurrent Workspaces per Role and per tenant.**
- **A Role MUST NOT be able to keep a Workspace alive indefinitely through
  lease renewal.** Total lease lifetime is bounded, and renewals consume
  budget.
- **Custodial destruction is authorized at tenant scope**, so a deleted task
  or a departed tenant cannot strand resources.

## 20. Policy authority and governance

**Policy is not a Capability, MUST NOT be registrable as a Capability, and
is changed only through an out-of-band governance channel.** Because every
effect the system can have flows through a registered Capability, and no
Capability writes Policy, Policy modification is **unreachable by
construction** — not merely forbidden by rule.

### 20.1 Policy versions

Every Policy Version requires **cryptographic identity**, a **signed
immutable version** (content hash, authoring authority, effective time,
supersedes-reference), and an **immutable audit trail**. The Control Plane
MUST be able to verify authenticity without trusting the transport.

**Every Policy evaluation binds to exactly one Policy Version identity**,
recorded in the Authorization Grant (§7.1) and in every Decision.

### 20.2 The authorization asymmetry

> **Expanding authority requires m-of-n quorum. Restricting authority
> requires a single authorized Policy Authority. Emergency Policy may only
> restrict.**

**Automation MAY transport, distribute, and deploy Policy. Automation MUST
NOT sign Policy.** Signatures originate only from human quorum keys.
Automation is transport, never authority. This is the most likely real-world
bypass and is closed explicitly.

**Separation of duties:** the Policy Authority MUST NOT be the same
authority as the Task Approver. Otherwise a human under deadline pressure
loosens Policy to approve their own work.

### 20.3 Scope and conflict

```text
system  →  tenant  →  task
```

**Narrower scopes may only restrict.** Conflicts resolve by **intersection /
most-restrictive result**. There is no widening operation, so scope conflict
is structurally impossible. **Unreadable or unresolvable Policy = deny**;
unavailable Policy is never permissive.

### 20.4 Transition

**A Policy Version change MUST invalidate all outstanding unconsumed
Authorization Grants.** Since Grants are per-Action, single-use, and
short-TTL, the blast radius is bounded and each in-flight Action is caught
at its own final gate. Policy change never edits an approval: it flows
through Safety Validity (Engineering State contract §13), so the historical
Approval remains true and simply stops being sufficient.

Emergency Policy takes effect immediately, invalidates outstanding Grants
and Reservations, is recorded with its invoking authority, and is
**time-boxed** — it expires rather than persisting silently.

### 20.5 Trusted Computing Base — stated explicitly

> **The Control Plane is the reference monitor and the assumed Trusted
> Computing Base. If the Control Plane itself is compromised, none of the
> three contracts claims to protect against that compromise.**

This is stated plainly rather than implying defense-in-depth the
architecture does not have.

## 21. Normative terminology

MUST / MUST NOT denote absolute requirements whose violation constitutes
non-conformance. SHOULD / SHOULD NOT denote strong recommendations that MAY
be deviated from only with a documented, deliberate justification recorded
at the point of deviation. MAY denotes genuine optionality. Text not using
these terms is descriptive context, not a conformance requirement.

## 22. Architectural decision summary

| Decision | Rationale | Rejected alternative |
|---|---|---|
| Authorization is a separate, per-Action, single-use, TTL-bounded Grant | A proposal's content is stable but authorization's validity is not | `authorized: true` on the ActionProposal |
| Proposal-level conformance and per-Action authorization are two phases | A five-step composition authorized once would run steps 3–5 under permission computed before step 1's Observation existed | One authorization per proposal |
| Safety Validity authoritative only at the final gate | Later stages consume real time and may invoke Capabilities, reopening the staleness window | Mid-pipeline safety validation |
| Grants are durable Engineering State events | ES inv. 5/20 would otherwise be false for the most consequential fact in the system | Control-Plane-internal ephemeral state |
| Compensation Reservation preserves eligibility, never authorizes | Authorizing a future Action is inheritance across time, violating Capabilities inv. 4–6 | A pre-issued grant, or an unconditional Policy override |
| Compensators may not be compensatable | Prevents infinite regress and unbounded Reservation chains | Unrestricted compensation chains |
| Prediction: syntactic check deterministic, necessary-condition declared and calibrated | Semantic relevance is not deterministically checkable; gaming produces a measurable signature | A semantic theorem prover, or syntactic checking alone |
| `Expected` firewalled from postcondition/Goal satisfaction | Otherwise "all actions Expected" drifts into "the goal must be fine" | Allowing Expected as supporting evidence |
| Goal Satisfied owned by the Control Plane as a four-part predicate | It is a conjunction over established facts — nothing to judge | Reasoning Plane self-declaration |
| Satisfied / Delivered / Accepted are independent | Delivered can precede Satisfied; Accepted is a human prerogative | A linear terminal enum |
| Artifact identity = instance digest + Execution Context; no rebuild-and-compare | Nondeterministic builds produce different bytes from identical inputs | Recomputing identity by rebuilding; mutable tags as identity |
| Verification contamination boundary stated as fact-vs-belief | An enumeration of banned inputs is gamed by anything not on the list | Enumerating forbidden input types |
| Quorum on Policy expansion, single signature on restriction | Defeats a single compromised account or insider without blocking safety response | Uniform Policy change control |
| Automation may transport but never sign Policy | Automated policy deployment with signing keys voids every other guarantee | Trusting automated policy pipelines |
| Workspace non-authoritative | Removes the need to make Workspace state trustworthy at all | Treating Workspace as recoverable state |
| Control Plane named as the TCB | Honest scoping beats implied defense-in-depth | Silence about the trust boundary |

## 23. Integrated Frontier Architecture Amendment

**This amendment is additive. It strengthens the Capability & Control Plane
contract. It weakens no frozen invariant.**

- **`ENGINEERING_STATE_ARCHITECTURE.md` remains frozen and unchanged.**
  Authorization Grant events are additive to an event model whose §8 defines
  event *properties*, not a closed vocabulary; append-authorization is
  already specified as per-event-type and simply gains an
  `Authorization*` class alongside the existing `Human*` class.
- **`REASONING_ENGINE_ARCHITECTURE.md` remains frozen and unchanged**, save
  for a pre-existing minimal cross-reference note at its §6 recording that
  authorization mechanics are refined here.
- **Only this document required substantive amendment.**

**Why the amendments were necessary.** A whole-system integration audit of
the three contracts found six gaps that fell *between* contracts rather than
inside any one of them — each permitting an implementation that satisfied
every written invariant while not being frontier-grade:

1. **Policy ownership and change-control** was undefined (§20). Policy is
   the most powerful object in the system; ungoverned, every other invariant
   routes through it.
2. **Independent Verification's independence was asserted but not
   mechanized, and its own Capability invocations had no authorization
   path** (§15). The layer everything defers to for correctness was the
   least specified.
3. **Authorization Grants had semantics but no durable home** (§7.1),
   breaking forensic reconstruction of *why permission existed*.
4. **Prediction falsifiability was unrequired** (§13). Vague Predictions
   structurally disabled Contradiction, and therefore Replan, and therefore
   the entire self-correction loop.
5. **Observation classification had no owner** (§16). Assigned to the
   generator, it would let a model grade its own failures.
6. **Rollback for irreversible steps and Workspace lifecycle were unowned**
   (§18, §19). The system could enter states it was not authorized to exit.

A subsequent adversarial pass against those resolutions rejected two of them
as originally written — the Compensation Reservation's unconditional
phrasing, and the verifier-independence enumeration — and discovered five
further gaps: recursive compensation, automation as a Policy signer, the
`Expected` firewall, compromised-Workspace evidence quarantine, and
Workspace resource caps. All are resolved in the text above.

## 24. Capability & Control Plane invariants

Original contract invariants, carried forward unchanged and unweakened:

1. A Capability MUST NOT compute, assert, or infer its own authorization; it
   may only declare what authorization it requires.
2. The Control Plane MUST be the sole evaluator of whether a required
   authorization is currently satisfied.
3. Authorization MUST be a separate object from the ActionProposal and the
   Action — never a field on either.
4. Every Authorization Grant MUST name exactly one Action, carry a bounded
   TTL, be consumed on dispatch, and be single-use.
5. A Grant MUST NOT be reused for another Action, reused for a retry of the
   same Action, or inherited by later Actions in a composition.
6. A multi-Action ActionProposal MUST NOT receive one authorization for its
   entire lifetime.
7. Proposal Conformance MUST NOT imply approval, authorization,
   executability, or safety to execute.
8. No state in the §8 ladder may imply the next; each MUST be separately
   evidenced, and no single mutable field may represent the progression.
9. Safety Validity MUST be authoritative only at the final authorization
   gate, never earlier.
10. Capability-Gap MUST be a distinct outcome from Policy denial, scope
    violation, budget exhaustion, and lease conflict, and MUST NOT be worked
    around by substitution or approximation.
11. The set of Primitive Capabilities MUST remain closed; no reasoning
    cycle, proposal, or escalation may register one automatically.
12. Composition MUST remain open, and MUST NOT expand the union of
    side-effects, scope ceilings, or external visibility of its constituents.
13. Promotion to Composed Capability MUST NOT be self-promoted by the
    Reasoning Plane, and MUST change only the autonomy default — never the
    validation standard.
14. Every candidate, whether pre-registered selection or novel composition,
    MUST pass the identical §6 pipeline; there MUST NOT be a weaker path.
15. Contradiction, Blocked, or ActionOutcomeUnknown MUST halt the
    composition, and remaining Actions MUST NOT inherit standing permission.
16. A denial MUST be routed to its specific failing reason, never collapsed
    into a generic failure.
17. A Capability's identity MUST survive replacement of its implementing
    Tool; Capability and Tool MUST NOT be conflated.
18. The Reasoning Plane MUST NOT be able to obtain, store, replay, or
    present an Authorization Grant.
19. Nothing in this contract may bypass or relax any invariant in
    `ENGINEERING_STATE_ARCHITECTURE.md` or
    `REASONING_ENGINE_ARCHITECTURE.md`.

## 25. Final cross-system invariants

The complete integrated invariant set. These hold across all three
contracts and MUST NOT be weakened or omitted.

1. **Policy-authoring MUST NOT be expressible or registrable as a
   Capability.**
2. **Expanding authority requires quorum; restricting requires a single
   authority. Emergency Policy may only restrict.**
3. **Automation may transport Policy but MUST NOT sign it.**
4. **A narrower Policy scope may only restrict; conflicts resolve by
   intersection. Unreadable Policy denies.**
5. **A Policy Version change invalidates all outstanding unconsumed
   Grants.**
6. **Authorization Grants are durable Engineering State events, appendable
   only by the Control Plane; denials are recorded.**
7. **An Authorization record MUST NOT be cited as Evidence supporting any
   Belief about the world.**
8. **A Compensation Reservation preserves eligibility, never authorizes; it
   binds categorical denial only and never overrides Safety Validity,
   emergency stop, or resource loss.**
9. **A compensating Capability MUST NOT itself be compensatable.**
10. **Compensation Reservations are Control-Plane-created only,
    single-tuple, total-lifetime-bounded, and MUST NOT reserve external
    resources.**
11. **A Role's custodial capabilities over its own resources MUST always
    remain authorizable, at tenant scope, independent of task state.**
12. **The verifier MAY consume facts and deterministic functions of facts;
    it MUST NOT consume any Belief, Hypothesis, confidence, rationale, or
    narrative from the generating Role.**
13. **The generator MUST NOT select, configure, parameterize, or supply
    context to its verifier; postconditions are checked in pinned,
    approval-time form.**
14. **Verification's read-only evidence gathering MUST NOT be revocable by
    the conditions that halt the generating task; if verification cannot be
    authorized, the PlanStep is `VerificationBlocked` — neither Verified nor
    Failed.**
15. **A Prediction MUST be syntactically falsifiable against the
    Capability's declared output schema, and MUST be a necessary condition
    of its PlanStep's postcondition.**
16. **`Expected` requires an affirmative match; inconclusive evaluation
    yields `Uncertain-Outcome`, never `Expected`.**
17. **`Expected` MUST NOT be cited as evidence toward postcondition or Goal
    satisfaction; only Independent Verification establishes a
    postcondition.**
18. **Observation classification is a deterministic, reproducible,
    Control-Plane-owned function with fixed evaluation order; the Reasoning
    Plane MUST NOT re-classify or retry a `Contradiction` as an `Anomaly`.**
19. **Goal Satisfied is established solely by the Control Plane's
    deterministic predicate; the Reasoning Plane can produce only Goal
    Completion Claimed, which is a Belief.**
20. **Goal Satisfied, Goal Delivered, and Goal Accepted are independent
    states, not a linear progression.**
21. **Artifact identity is the produced instance's content digest bound to
    its production and verification Execution Context; consumers MUST NOT
    rebuild-and-compare; mutable references MUST NOT serve as identity.**
22. **Verification results bind to artifact identity; if the artifact
    changes, prior verification does not carry forward.**
23. **Nothing in a Workspace is ever authoritative; any depended-upon fact
    MUST be recorded as an Observation.**
24. **Diagnostic hold outranks lease expiry but yields to data
    classification; secret-tainted Workspaces are destroyed immediately and
    escalated.**
25. **Evidence derived from a later-discovered-compromised Workspace MUST be
    marked contested and superseded, never deleted.**
26. **Concurrent Workspaces MUST be Policy-capped per Role and per tenant.**
27. **The Control Plane is the reference monitor and the assumed trust
    boundary; if it is compromised, no invariant above holds.**

---

# FINAL CONTRACT

Binding on any implementation claiming conformance.

1. A Capability declares what authorization it requires; the Control Plane
   alone decides whether that requirement is currently satisfied. No
   Capability or Tool may compute, assert, or infer its own authorization.
2. Capability ≠ Tool. A Capability's identity and version survive its
   implementing Tool being replaced.
3. Primitive Capabilities are a closed set, registered only by an
   out-of-band governed engineering act. Composition over them is open.
4. Every Primitive Capability declares, at minimum: identity, version, input
   and output schema, applicability, parameter constraints, target type,
   scope ceiling, reversibility class (with a named compensator if
   `compensatable`), risk floor, external visibility, cost class, required
   authorization, preconditions, side-effect class, Execution Context
   requirements, isolation requirements, artifact production declaration,
   registration provenance, and timeout.
5. Authorization is a separate object — never a field on an ActionProposal
   or Action. Each Grant names exactly one Action, carries a bounded TTL, is
   consumed on dispatch, is single-use, and is never reused, never reused
   for a retry, and never inherited.
6. Authorization Grants are durable Engineering State events authored solely
   by the Control Plane; denials are recorded; expiry is derived.
   Authorization is permission, never evidence.
7. Proposal Conformant ≠ Action Eligible ≠ Action Authorized ≠ Execution
   Started ≠ Execution Completed ≠ Observation Recorded ≠ Independently
   Verified ≠ Goal Completion Claimed ≠ Goal Satisfied. Each is separately
   evidenced; none implies the next.
8. Every Prediction must be mechanically falsifiable against its
   Capability's declared output schema and must be a necessary condition of
   its PlanStep's postcondition. Inadmissible Predictions fail Conformance.
9. Artifact identity is the produced instance's content digest bound to its
   Execution Context. Consumers declare and verify it, never rebuild to
   establish it. Mutable references are never identity. Verification binds
   to artifact identity.
10. The verifier consumes facts, never the generator's beliefs. The
    generator never selects or configures its verifier. Verification uses
    the same Control Plane and the same authorization mechanism; no bypass
    exists. Unauthorizable verification yields `VerificationBlocked`.
11. Observation classification is deterministic, reproducible, and
    Control-Plane-owned, with a fixed evaluation order. `Expected` is never
    evidence of postcondition or Goal satisfaction.
12. Goal Satisfied is a Control Plane predicate, never a Reasoning Plane
    claim. Satisfied, Delivered, and Accepted are independent facts.
13. Compensation Reservations preserve eligibility, never authorize, bind
    only categorical Policy denial, and never override situational safety.
    Compensators are never themselves compensatable.
14. Workspaces are non-authoritative, Control-Plane-owned, lease-bounded,
    capped per Role and tenant, and re-validated on resume. Custodial
    capabilities always remain authorizable at tenant scope.
15. Policy is unreachable through any agent-facing channel, signed and
    versioned, expandable only by quorum, restrictable by a single
    authority, never signed by automation, and narrowing-only across scopes.
16. The Control Plane is the reference monitor and the assumed Trusted
    Computing Base; its compromise is outside what these contracts claim to
    defend.
17. Nothing here bypasses or relaxes any invariant in
    `ENGINEERING_STATE_ARCHITECTURE.md` or
    `REASONING_ENGINE_ARCHITECTURE.md`.
