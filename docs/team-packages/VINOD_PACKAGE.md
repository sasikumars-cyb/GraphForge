# VINOD_PACKAGE.md — Day 0 Implementation Package

Source of truth: `TEAM_EXECUTION_PLAN.md` Section 3 (PW-2), Section 5, Section 6. This package
extracts what you need to start coding the moment PW-1 merges — it introduces nothing new.

## Mission

Build the one piece of this hackathon that genuinely needs deep architectural ownership:
`RunCoordinator` — the code that executes a selected agent and persists the run. Everything
around it (contract, registration, adapters) is deliberately not yours; this is a narrowed scope
compared to earlier drafts of this plan, specifically so you can go deep on the one thing that
actually needs it.

## Architecture Responsibilities

- `RunCoordinator.execute(subject, goal) -> Run`: look up the agent via `Registry`/`Selector`
  (Sasikumar's PW-1a, already frozen and implemented by the time you need it), call
  `agent.run(context)`, capture the `AgentOutput`, persist `Run`+`AgentStep`, return.
- `RunContext` is **not** a separate module — it's a plain dict attribute on `RunCoordinator`
  itself. This was a live judgment call in earlier drafts; it's now formally decided: for a
  single-process, in-memory, one-run-at-a-time hackathon build, a dedicated file buys nothing.
  Don't create `run_context.py`.
- Zero special-casing per agent inside `RunCoordinator`'s core loop — if you find yourself writing
  `if agent_id == "review": ...`, that's a sign the contract (Sasikumar's, not yours to change
  silently) has a gap. Flag it, don't route around it.

## Owned Modules / Owned Folders

- `backend/app/orchestrator/run_coordinator.py`
- `backend/app/models/run.py`
- `backend/app/models/agent_step.py`
- The Alembic migration for both new models

## Owned APIs

None directly — `RunCoordinator.execute()` is a Python interface, not an HTTP endpoint. Ani's PW-6
router calls into it. You review PW-6 to confirm it's calling your interface correctly.

## Expected Sequence of Implementation

1. **Before you can start**: wait for Sasikumar's "PW-1 merged — go" message. `git pull origin
   main` immediately before cutting your branch — don't branch from a stale local `main`.
2. **Hour ~2–4**: `Run`/`AgentStep` models + the Alembic migration. **Add both models to
   `backend/alembic/env.py`'s import list** — this exact bug (a model missing from that file) has
   already happened once in this codebase for `PullRequestAIAnalysis`; don't repeat it. Ship this
   as its own staged sub-PR, don't wait to bundle it with `RunCoordinator`.
3. **Hour ~4–6**: `RunCoordinator` itself, against Sasikumar's already-merged `Registry`/`Selector`
   (PW-1a). Ship as a second staged PR.
4. **Target**: both PRs reviewed and merged by Checkpoint 1 (hour 5.5–6.5).

## Acceptance Criteria

- Selecting `Goal=review_pr` and `Goal=plan_freeform` both correctly route through
  `Registry`/`Selector` and produce a persisted `Run`+`AgentStep`.
- `alembic check` reports no drift.
- Zero regression in the existing Review Agent test suite (you don't touch `app/ai/agent/*` at
  all — the adapter that calls it is Sasikumar's PW-3, not yours).

## Files to Avoid

- `app/analysis/*`, `app/graph/*`, `app/ai/agent/*` — read-only reference only.
- `app/orchestrator/registry.py`, `app/orchestrator/selector.py` — Sasikumar's (PW-1a). You consume
  these, you don't implement or edit them.
- `app/agents/review_adapter.py` — Sasikumar's (PW-3).
- `app/agents/planning/*` — Rajan's.
- `app/agents/_contract.py` — frozen after PW-1 merges; if you think it needs to change, that's a
  Sasikumar conversation, not a silent edit.

## Integration Points

- **Upstream**: PW-1's frozen `RunCoordinator` signature (Sasikumar), PW-1a's `Registry`/`Selector`
  implementation (Sasikumar) — both should be merged before you need to integration-test end-to-end,
  though you can start writing `RunCoordinator` against the frozen signature alone.
- **Downstream**: PW-6 (`agent-runs` router, Ani) calls `RunCoordinator.execute()` directly. PW-3
  (Review Agent adapter, Sasikumar) and PW-4 (Planning Agent, Rajan) both register with
  `Registry` and get selected via your `RunCoordinator`'s call to `Selector`.

## Definition of Done

- [ ] `Run`/`AgentStep` models exist, migration applied, `alembic check` clean
- [ ] `RunCoordinator.execute()` implements the frozen PW-1 signature exactly
- [ ] Both `review_pr` and `plan_freeform` goals route correctly once both agents register
- [ ] No special-casing per `agent_id` in the core loop
- [ ] Reviewed and merged by Sasikumar

## Testing Checklist

- Real Postgres in integration tests — never a mocked DB session (existing repo convention, no
  exception for new code).
- Test `RunCoordinator.execute()` against a fake/stub agent implementing PW-1's `Protocol`
  directly — don't require the real Review/Planning agents to exist to test the coordinator's own
  logic.
- Test the failure path: what happens when `Selector.select()` can't find a registered agent for
  a `Goal`? Should raise a clear error, not silently no-op.
- Confirm `alembic upgrade head` and `alembic check` both run clean against a fresh database.

## Example PR Titles

- `feat: add Run and AgentStep models + migration`
- `feat: implement RunCoordinator`

## Example Commit Messages

```
feat: add Run/AgentStep models and migration

New Postgres models for the Agent Orchestrator's run-tracking layer.
Registered in alembic/env.py's autogenerate-discovery imports (this
exact class of bug — a missing model import — has bitten this repo
once before, for PullRequestAIAnalysis).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

```
feat: implement RunCoordinator

Executes a selected agent via Registry/Selector, persists Run and
AgentStep rows. RunContext is a plain dict on the coordinator itself,
not a separate module - see TEAM_EXECUTION_PLAN.md Section 12.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

## AI Prompt Template — Implementation

```
Context: GraphForge Agent Orchestrator, FastAPI + async SQLAlchemy + Alembic. Here is the frozen
contract I'm implementing against: [paste _contract.py in full, once Sasikumar merges it]. Here is
the existing model pattern to follow: [paste an existing model, e.g. pull_request_ai_analysis.py].
Here is alembic/env.py's current import list: [paste it].

Task: Implement Run and AgentStep models + a migration. Add both to alembic/env.py's imports -
this exact bug (a model missing from that list) has already happened once in this codebase.
RunContext is a plain dict attribute on RunCoordinator, not a separate file.

Output: models + migration + RunCoordinator.execute(subject, goal) implementing the frozen
signature, calling Registry.select via Selector, persisting the run, with tests using the
existing real-Postgres integration convention.
```

## AI Prompt Template — Debugging

```
Context: RunCoordinator.execute() is [describe the failure - wrong agent selected / Run not
persisted / alembic drift]. Here is the code: [paste run_coordinator.py]. Here is the frozen
contract: [paste _contract.py]. Here is Registry/Selector's implementation: [paste registry.py,
selector.py].

Task: Find the root cause. Do not modify registry.py/selector.py (Sasikumar's) or the contract
itself - if the bug is actually in one of those, stop and flag it as a contract-gap conversation,
don't patch around it silently in your own file.

Output: root cause + minimal fix scoped to run_coordinator.py/models only.
```

## AI Prompt Template — Code Review

```
Context: Reviewing a PR for [PW-1a Registry/Selector / PW-4 Planning Agent / PW-6 agent-runs
router]. Here's the diff: [paste it]. Here's the frozen contract it must implement: [paste
_contract.py].

Task: Check that it correctly implements the frozen signature with no deviation, that it doesn't
require a change to _contract.py or my RunCoordinator's core loop (if it does, that's a red flag
per TEAM_EXECUTION_PLAN.md Section 8's "how to detect broken architecture"), and that error paths
are surfaced, not swallowed.

Output: concrete findings with file:line references.
```

## Daily Completion Checklist

- [ ] `git pull origin main` before branching — confirmed PW-1 is actually merged, not just "looks done"
- [ ] Staged sub-PRs, not one big end-of-day PR: models+migration first, `RunCoordinator` second
- [ ] Both PRs reviewed by Sasikumar same-day
- [ ] `alembic check` run before requesting review, every time
- [ ] Once merged: available to review PW-1a, PW-4, PW-6 as named reviewer

## Implementation Safety

**Protected files**: `app/analysis/*`, `app/graph/*`, `app/ai/agent/*`, `app/orchestrator/
registry.py`/`selector.py` (Sasikumar's), `app/agents/review_adapter.py` (Sasikumar's),
`app/agents/planning/*` (Rajan's), `docker/docker-compose*.yml`.

**Shared contracts**: `_contract.py` (Sasikumar's, frozen — you consume it, you don't edit it),
`API_CONTRACTS.md`'s `Run`/`AgentStep` shapes as documented in the `GET /agent-runs/{id}` example.

**Architecture rules**: no special-casing per agent in the core loop. `RunContext` stays a plain
dict, not a module. Deterministic-before-probabilistic still applies — `RunCoordinator` orchestrates,
it never computes a fact itself.

**API rules**: not your workstream directly, but `RunCoordinator`'s return shape must match what
`API_CONTRACTS.md`'s `GET /agent-runs/{id}` documents, since PW-6 wraps it verbatim.

**UI rules**: not applicable to your workstream.

**Forbidden shortcuts**: skipping the `alembic/env.py` registration "because it's just a migration
file" — this exact shortcut has already caused a real bug once in this repo. Bundling
`RunCoordinator` and the models into one giant PR instead of the staged sub-PRs Finding 7 asked for.

**Common mistakes**: creating `run_context.py` out of habit (it's explicitly not wanted this
hackathon); implementing `Registry`/`Selector` yourself instead of waiting for Sasikumar's PW-1a;
touching `app/ai/agent/*` "just to understand it better" — read it via `git show`/your editor, but
your branch's diff should never include it.
