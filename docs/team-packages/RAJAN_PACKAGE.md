# RAJAN_PACKAGE.md — Day 0 Implementation Package

Source of truth: `TEAM_EXECUTION_PLAN.md` Section 3 (PW-4), Section 5, `AGENT_FRAMEWORK.md`. This
package extracts what you need to start coding the moment PW-1 merges (and PW-5 is available).

## Mission

Build the second agent — the actual proof the architecture generalizes to something that isn't
the Review Agent, **grounded in the same Knowledge Graph, not just an LLM call**. You are
deliberately the "naive user" of a freshly-frozen contract: if you hit friction building against
it, that's exactly the signal the team needs early, not a problem to quietly work around.

## Features to Build

The Planning Agent (`app/agents/planning/`): manifest, prompt template, its own minimal
tool-calling loop, output schema. **Do not** share a `ToolRegistry` with the Review Agent — the
Review Agent's tools were built for one agent, and forcing your tools into the same abstraction
under time pressure is premature generalization for a sample size of two. Write your own,
minimal, tool-calling code.

**The one requirement that isn't optional**: at least one `Evidence` entry in your output must be
`kind="graph_traversal"` or `kind="tool_call"` — not only `kind="llm_reasoning"`. Without this,
the Planning Agent is provably just a chatbot wrapper, which directly undermines
`PRODUCT_VISION.md`'s explicit "GraphForge is NOT another chatbot" claim, in front of the exact
audience that claim exists for. Reuse existing deterministic graph-read tools from
`app/analysis`/`app/graph` where sensible — don't reimplement graph traversal yourself.

## Owned Backend APIs

None directly (no new HTTP endpoint) — your agent is invoked through the Orchestrator
(`RunCoordinator.execute()`), which Ani's `agent-runs` router exposes over HTTP.

## Owned Frontend Components

None — Nilesh owns the Agents page that displays your output. You don't touch frontend code.

## Owned Files

- `backend/app/agents/planning/` (all files: `manifest.py`, prompt template, tool-calling code,
  output schema)

## Implementation Sequence

1. **Before you can start**: wait for Sasikumar's "PW-1 merged — go" message; `git pull origin
   main` first.
2. **Hour ~2**: manifest + registerable stub. Register with `Registry` (Sasikumar's PW-1a) —
   confirm you can register and be selected by `Goal=plan_freeform` before building anything else.
   Ship this as an early PR.
3. **Hour ~2–5.5**: the standalone tool-calling logic — reuse existing deterministic graph-read
   tools where they genuinely help; write your own minimal loop, don't force-fit the Review
   Agent's `tools.py` shape.
4. **Checkpoint-2 bar (hour ~5.5–6.5)**: at least one fully evidence-backed example, including a
   `graph_traversal`/`tool_call` entry — this is the demo-freeze bar, and it's the part that must
   not slip.
5. **Continuing into Hardening (non-blocking)**: validate against 3+ distinct free-text inputs
   (the original, fuller Prompt Validation bar) — this does not gate Checkpoint 2, so don't rush it
   at the expense of the graph-grounding requirement above.

## Acceptance Criteria

- Registers with the Orchestrator; zero changes required to `_contract.py` or `RunCoordinator`'s
  core loop — the cleanest possible signal the contract is genuinely reusable, not accidentally
  Review-Agent-specific.
- Produces at least one genuine `AgentOutput` with non-empty `Evidence`, **including at least one
  `graph_traversal`/`tool_call` entry**, for one distinct free-text input (demo-freeze bar).
- Full validation: 3+ distinct inputs, each with genuine (not hallucinated) evidence — continues
  into Hardening, non-blocking to Checkpoint 2.

## Definition of Done

- [ ] Manifest registered, selectable by `Goal=plan_freeform`
- [ ] Own tool-calling loop, no shared `ToolRegistry` with the Review Agent
- [ ] At least one graph-grounded `Evidence` entry per output — verified, not assumed
- [ ] Zero changes required to `_contract.py`, `registry.py`, `selector.py`, or
      `run_coordinator.py`'s core loop
- [ ] Ani's Prompt Validation pass confirms genuine evidence across 3+ inputs (Hardening-phase,
      non-blocking)

## UI Consistency Checklist

Not directly applicable — you don't build UI. But your `AgentOutput`'s content (the
`executive_summary`-equivalent text, `Evidence.summary` strings) is what Nilesh's
`EvidencePanel` renders — write these as clean, complete sentences a user will actually read on
screen, not debug-log-style fragments.

## Files to Avoid

- `app/agents/review_adapter.py`, `app/ai/agent/*` — Sasikumar's / frozen, read-only reference only.
- `app/orchestrator/*` — Sasikumar's (PW-1a) / Vinod's (PW-2). You consume `Registry`, you
  don't implement or edit it.
- `app/agents/_contract.py` — frozen after PW-1 merges. If you think it needs a change, that's the
  most important thing you can flag today — say so immediately, don't silently work around it.

## Dependencies

- PW-1 (Sasikumar's frozen contract) — you need the `Protocol`, `AgentManifest`, `AgentOutput`
  shapes before writing anything. This is the only dependency that's genuinely blocking.
- PW-5 (FreeText Entry Resolver, Ani) — you need this to turn a free-text goal into a
  `Subject` your agent acts on. **This dependency is removable, not required**: `Subject`'s shape
  is fully documented in `API_CONTRACTS.md` (`subject_id`, `subject_type`, `graph_node_ids`,
  `display_name`) — construct a hardcoded `Subject` locally for development and testing, and swap
  in the real `resolve()` call once PW-5 merges (~hour 1.5–2, likely before you need it anyway).
  Don't block your own start on someone else's PR landing when the shape is already frozen.

## Public Interfaces

You implement PW-1's `Protocol` — no new interface of your own:

```python
class IAgent(Protocol):
    async def run(self, context: AgentContext) -> AgentOutput: ...
```

## Example PR Titles

- `feat: add Planning Agent manifest + registration stub`
- `feat: implement Planning Agent tool-calling loop with graph-grounded evidence`
- `feat: polish Planning Agent prompt and output quality`

## Example Commit Messages

```
feat: add Planning Agent manifest and registration stub

Registers with the Orchestrator under goal=plan_freeform. Minimal
stub output for now - full tool-calling loop follows in a
subsequent commit.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

```
feat: implement Planning Agent tool-calling loop

Own minimal tool-calling code, not shared with the Review Agent's
ToolRegistry (deliberate - see TEAM_EXECUTION_PLAN.md Section 1).
Reuses existing deterministic graph-read tools from app/analysis
where sensible. Produces at least one graph_traversal Evidence
entry per output, per the tightened Definition of Done.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

## AI Prompt Template — Implementation

```
Context: GraphForge Agent Framework. Here are AGENT_FRAMEWORK.md's Agent Contract / Execution
Flow / Confidence & Evidence sections: [paste them]. Here is the frozen contract I implement:
[paste _contract.py, once Sasikumar merges it]. Here is the Review Agent's tool-use loop shape to
follow the STRUCTURE of, not the domain logic or the tool implementations themselves: [paste
investigation_agent.py's planner loop, for shape reference only].

Task: Implement the Planning Agent: manifest, prompt template, its own tool set (NOT shared with
the Review Agent's ToolRegistry), output schema. Reuse existing deterministic graph-read tools
from app/analysis/app/graph where genuinely useful - don't reimplement graph traversal. Every
confidence score needs at least one Evidence entry; at least one entry per output must be
kind="graph_traversal" or kind="tool_call", not only kind="llm_reasoning".

Output: agent module + manifest registration + tests covering: happy path with real graph-grounded
evidence, a low-confidence retry case, one failing-tool case.
```

## AI Prompt Template — Debugging

```
Context: Planning Agent's output has [describe the issue - low confidence, empty evidence, wrong
Subject resolution]. Here is my agent code: [paste it]. Here is the frozen contract: [paste
_contract.py]. Here is the FreeText resolver's output shape: [paste freetext.py's Subject
construction].

Task: Diagnose whether the issue is in my agent's own logic, or a mismatch with the frozen
contract/resolver upstream. If it's the latter, this needs to be flagged as a contract gap, not
silently patched in my own code.

Output: root cause + fix scoped to my planning/ files only, or a clear statement that this needs
escalation.
```

## AI Prompt Template — Code Review

```
Context: Reviewing my own Planning Agent PR before requesting review: [paste the diff].

Task: Check specifically: (1) does every AgentOutput have non-empty Evidence, (2) does at least
one Evidence entry per output have kind="graph_traversal" or kind="tool_call" (not only
llm_reasoning), (3) is there any accidental dependency on a shared ToolRegistry, (4) does
anything here require a change to _contract.py or the Orchestrator's core loop (a red flag per
TEAM_EXECUTION_PLAN.md Section 8), (5) are there tests for the failure/retry paths.

Output: a list of findings, self-corrected before Vinod's review.
```

## Daily Completion Checklist

- [ ] `git pull origin main` before branching, confirmed PW-1 is actually merged
- [ ] Manifest + stub registered and selectable by hour ~2–3
- [ ] At least one graph-grounded evidence example working by Checkpoint 1/2 window (hour
      5.5–6.5) — this is the bar that must not slip
- [ ] Full 3+-input validation continues into Hardening, doesn't block the checkpoint
- [ ] Every PR names which `AGENT_FRAMEWORK.md` section and which Section 3 workstream (PW-4) it
      implements

## Implementation Safety

**Protected files**: `app/ai/agent/*`, `app/agents/review_adapter.py`, `app/orchestrator/*` —
read/consume only, never edit.

**Shared contracts**: `_contract.py` (frozen, Sasikumar's — report gaps, don't patch around them),
`Subject`'s shape from PW-5.

**Architecture rules**: no shared `ToolRegistry` with the Review Agent. Deterministic-before-
probabilistic — reuse `app/analysis`'s existing engines for anything computable exactly, don't ask
the LLM to compute a fact a deterministic tool already computes.

**API rules**: not your workstream directly — your output flows through `RunCoordinator`/PW-6's
router, which owns the HTTP shape.

**UI rules**: not applicable — but write your output text (summaries, evidence descriptions) as
genuinely readable prose, since it renders directly in Nilesh's UI.

**Forbidden shortcuts**: shipping with only `llm_reasoning` evidence "to save time" — this is the
single most consequential shortcut in this package; it fails review and undermines the whole
demo's thesis. Silently building a shared `ToolRegistry` because it "feels more consistent."

**Common mistakes**: waiting for the full Orchestrator (PW-2) to be done before starting — you
only need PW-1's contract and PW-5's resolver, both available early; treating the "3+ inputs"
validation as blocking Checkpoint 2 when it's explicitly non-blocking per your Definition of Done.
