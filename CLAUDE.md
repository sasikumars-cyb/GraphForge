# Repository instructions for Claude

Start with [`docs/handbook/16_REALITY_CHECK.md`](docs/handbook/16_REALITY_CHECK.md)
for what's actually implemented vs. partial vs. deferred, and
[`README.md`](README.md#documentation) for the documentation map. Don't take
any single doc's framing as current status by itself — this repo's own
convention is "trust the code, then fix the discrepancy."

## Mandatory: Engineering State architecture governance

**Before making any architectural decision, proposal, or code change
concerning any of the following, you MUST read
[`docs/graphforge/ENGINEERING_STATE_ARCHITECTURE.md`](docs/graphforge/ENGINEERING_STATE_ARCHITECTURE.md)
in full:**

- Engineering State or Reasoning State
- Beliefs, Hypotheses, Assumptions, Predictions, or Evidence
- the Knowledge / Engineering State boundary
- Plans or PlanSteps
- Decisions or Decision provenance
- human approval, authorization, or Execution Authorization
- multi-agent coordination over shared state
- event sourcing, event logs, or state replay/reconstruction
- confidence, uncertainty, or any other epistemic-state modeling

That document is **normative** for everything it covers. It is not a
proposal, not one option among several, and not something to reinterpret
from memory or from a partial read. If you have discussed Engineering State
architecture earlier in a conversation, re-read the file anyway before
acting — do not rely on a summary or a recollection of it.

**If code or another document conflicts with that contract, the contract
wins.** Do not silently weaken an invariant in that document to match
existing implementation, an existing schema, an existing API, model
limitations, migration difficulty, or time pressure. If you find a real
conflict, name it explicitly (which invariant, which file) rather than
quietly resolving it either direction — see that document's own "Preventing
architectural drift" section for the required response. Changing the
contract itself requires an explicit architectural decision, recorded in
that document, not a drive-by edit.

Two known, already-flagged terminology collisions with earlier
implemented work (RFC-001's `Belief`/`Hypothesis`/`Evidence` models, and ADR
0018's Knowledge Engine pipeline) are documented in that file's §0 — treat
them as open, not resolved, unless a later architectural decision says
otherwise.

## Mandatory: Reasoning Engine architecture governance

**Before making any architectural decision, proposal, or code change
concerning any of the following, you MUST read
[`docs/graphforge/REASONING_ENGINE_ARCHITECTURE.md`](docs/graphforge/REASONING_ENGINE_ARCHITECTURE.md)
in full:**

- the Reasoning Engine, the Reasoning Plane, or the Control Plane
- ActionProposal, Capability, or Composed Capability
- candidate generation or capability composition
- reasoning cycles, replanning, or action selection
- autonomy levels as they affect reasoning/execution

That document is **normative** for everything it covers, exactly like the
Engineering State contract above — do not reinterpret it from memory, a
summary, or a partial read; re-read it fresh even if discussed earlier in
the same conversation. **Existing implementation does not override it.**
If implementation conflicts with it, identify the specific architectural
conflict (which invariant, which file) rather than weakening the contract
to match the code — the same "Preventing architectural drift" discipline
applies, and is restated in that document's own §0/§21 for this contract
specifically.

The Reasoning Engine contract builds directly on top of the Engineering
State contract and never relaxes any of its invariants — reading one
without the other will produce an incomplete, and likely wrong,
understanding of either.

## Mandatory: Capability & Control Plane architecture governance

**Before making any architectural decision, proposal, or code change
concerning any of the following, you MUST read
[`docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md`](docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md)
in full:**

- Capability, Primitive Capability, or Composed Capability
- Tool, Action, or ActionProposal
- the Control Plane, Authorization, Authorization Grants, or Compensation
  Reservations
- Policy evaluation or Policy governance/authority
- capability composition, capability registration, or capability promotion
- execution authorization
- Prediction admissibility or artifact identity
- Independent Verification's structural boundary or authorization
- Observation classification
- Goal Satisfied / Goal Delivered / Goal Accepted
- Workspace lifecycle or reversibility/compensation/rollback

That document is **normative**. Existing implementation does NOT override
it; future implementation must conform to it. If implementation conflicts
with it, identify the specific architectural conflict (which invariant,
which file) instead of weakening the contract.

**Terminology warning — read §0.1 of that contract before using the word
"Capability" anywhere in this repository.** Four *pre-existing, unrelated*
meanings of "Capability" already exist in the codebase
(`context_pipeline/reasoning/capabilities.py` = information requirements;
`ai/providers/registry.py` = LLM provider feature flags;
`knowledge/registry.py` = connection transport features;
`agents/planning/classifier.py` = solution-domain features), none of which
is the contract's meaning. Determine which is meant from module context;
do not assume.

## The three canonical contracts

`ENGINEERING_STATE_ARCHITECTURE.md`, `REASONING_ENGINE_ARCHITECTURE.md`,
and `CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md` are peers — none overrides
another, each is authoritative only for its own scoped domain, and the
latter two never relax an invariant of the first. **Read the relevant
contract in full before acting on it. If a decision crosses domain
boundaries, read all three.**

**No session may weaken a frozen invariant in any of the three contracts
because:** the current implementation is simpler without it, existing code
already does something else, a prior assumption in this conversation said
otherwise, a particular model prefers a different shape, or it would be
faster or more efficient to skip it. None of these is a valid reason to
change architecture. If code, a document, or your own prior reasoning
conflicts with a frozen contract, **report the conflict explicitly** (name
the invariant, name the file) rather than silently resolving it in either
direction. Changing a contract requires an explicit architectural decision,
recorded in that contract, never a drive-by edit.
