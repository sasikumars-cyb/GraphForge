# ANI_PACKAGE.md — Day 0 Implementation Package

Source of truth: `TEAM_EXECUTION_PLAN.md` Section 3 (PW-5, PW-6), Section 5, `RELEASE_CHECKLIST.md`.
This package extracts what you need to start coding at hour 0 — you have real implementation
scope from minute one, not just a testing role that starts once there's something to test.

## Mission

Ship two genuinely low-risk backend pieces early — the FreeText Entry Resolver and the
`agent-runs` API router — then own regression and demo validation for the rest of the day. Your
coding scope was chosen deliberately for low architectural risk, not as busywork: both pieces are
safe to build without deep Orchestrator context, which frees Vinod for the one piece
that actually needs it.

## Coding Responsibilities

- **PW-5, first**: `app/context/resolvers/freetext.py` — a pure function, no DB, no HTTP. Start
  at hour 0, against a draft `Subject` shape (you don't need to wait for PW-1 to merge to start
  writing this — `Subject`'s shape is simple and documented in `API_CONTRACTS.md`; confirm and
  adjust once PW-1 actually lands).
- **PW-6, second**: `app/api/v1/routers/agent_runs.py` + the one-line registration in
  `app/api/v1/routers/__init__.py`. Start the moment PW-1's frozen `RunCoordinator` signature is
  available (~hour 2) — build and test against **mocks**, don't wait for Vinod's
  real implementation. Rewire to the real `RunCoordinator` once PW-2 merges (~hour 5.5–6.5).

## Testing Responsibilities

- Run the full existing regression suite (268 backend, 49 frontend) after every merge to `main`,
  not just at checkpoints — same-day, every merge cycle.
- File bugs with severity (Blocker/Major/Minor per `TEAM_IMPLEMENTATION_PLAN.md` §12) the moment
  you find them, not batched at end of day.
- Prompt Validation pass on the Planning Agent once PW-4 has a stub: confirm every confidence
  score has at least one non-empty `Evidence` entry, and **specifically** that at least one entry
  per input is `kind="graph_traversal"` or `kind="tool_call"` — not only `llm_reasoning`. A
  Planning Agent that only reasons over free text with zero graph grounding fails this check.
- Own demo script rehearsal (with Sasikumar) and the backup screen recording.

## Owned Modules

- `backend/app/context/` (PW-5)
- `backend/app/api/v1/routers/agent_runs.py` (PW-6)

## Owned Routes

- `POST /api/v1/agent-runs`
- `GET /api/v1/agent-runs/{id}`
- `GET /api/v1/agent-runs`
- `GET /api/v1/agents`

Match `API_CONTRACTS.md`'s exact shapes:

```json
// POST /api/v1/agent-runs request
{ "subject_reference": "pr:a1b2c3d4", "goal": "review_pr", "model": "gpt-5" }

// POST /api/v1/agent-runs response (202 Accepted)
{ "run_id": "run-uuid", "status": "queued",
  "subject": { "subject_id": "pr:a1b2c3d4", "subject_type": "pull_request", "...": "..." },
  "agents_selected": ["review"] }

// GET /api/v1/agent-runs/{run_id} response
{ "run_id": "run-uuid", "goal": "review_pr", "status": "completed",
  "started_at": "...", "completed_at": "...",
  "steps": [ { "step_id": "...", "agent_id": "review", "status": "completed",
               "confidence": { "score": 0.88, "reasoning": "..." },
               "evidence": [ { "kind": "graph_traversal", "reference": "...", "summary": "..." } ],
               "output_ref": "ai-analysis:a1b2c3d4" } ] }
```

## Owned Files

- `backend/app/context/resolvers/freetext.py`
- `backend/app/api/v1/routers/agent_runs.py`
- One line in `backend/app/api/v1/routers/__init__.py` (registration — announce before merging,
  this file is shared)

## Acceptance Criteria

- **PW-5**: resolves at least 5 varied example inputs to valid `Subject`s, unit tested with zero I/O.
- **PW-6**: matches `API_CONTRACTS.md` exactly (status codes, field names, pagination envelope);
  tested happy path + each documented error status, following `ai_analysis.py`'s existing test
  conventions.

## Demo Validation Checklist

- [ ] Free-text goal → Planning Agent run visible, with graph-grounded evidence
- [ ] Real PR → Review Agent run visible, via the new PR-trigger input on the Agents page
- [ ] Both runs appear together on one `GET /agent-runs` call (unfiltered), proving shared history
- [ ] Demo script rehearsed twice — once by Sasikumar, once by you (or vice versa), specifically to
      catch an assumption the author baked in unconsciously
- [ ] Backup screen recording captured and playable offline
- [ ] Primary demo PR verified real and unchanged since the last rehearsal; backup PR verified too

## Regression Checklist

- [ ] Full 268-test backend baseline green after every merge
- [ ] Full 49-test frontend baseline green after every merge
- [ ] `alembic check` clean after any migration-touching merge
- [ ] No Protected File touched by any merged branch
- [ ] Bug list triaged continuously, not batched — severity assigned the moment a bug is found

## Definition of Done

- [ ] PW-5 merged, 5+ example inputs tested
- [ ] PW-6 merged, matches `API_CONTRACTS.md` exactly, rewired from mocks to real `RunCoordinator`
- [ ] Regression suite green throughout the day, bug list continuously triaged
- [ ] Demo rehearsed twice, backup confirmed

## Example PR Titles

- `feat: add FreeText Entry Resolver`
- `feat: add agent-runs API router`
- `feat: rewire agent-runs router from mocks to real RunCoordinator`

## Example Commit Messages

```
feat: add FreeText Entry Resolver

Resolves a free-text goal string into a Subject for the Planning
Agent - a pure function, no DB, no HTTP, standing in for the
out-of-scope Jira/Confluence resolvers.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

```
feat: add agent-runs API router

POST/GET /agent-runs, GET /agents per API_CONTRACTS.md. Built and
tested against a mocked RunCoordinator; real wiring lands in a
follow-up once PW-2 merges.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

## AI Prompt Template — Implementation

```
Context: GraphForge, FastAPI. Here is the exact contract from API_CONTRACTS.md for these
endpoints: [paste the Agent Orchestrator API section verbatim, including example JSON]. Here is
the existing router pattern to follow: [paste ai_analysis.py]. Here is the frozen RunCoordinator
signature I'm calling: [paste _contract.py's RunCoordinator Protocol].

Task: Implement exactly this contract - do not add fields, do not change field names, do not
change the status codes listed. Build against a mocked RunCoordinator first (I'll swap to the
real one once it exists).

Output: router code + Pydantic schemas + tests for happy path and each listed error status,
using the existing real-Postgres integration test convention.
```

## AI Prompt Template — Debugging

```
Context: agent-runs router test is failing after I rewired it from a mock RunCoordinator to the
real one. Here's the router: [paste agent_runs.py]. Here's the real RunCoordinator's actual
signature/behavior: [paste run_coordinator.py]. Here's the failing test + error: [paste both].

Task: Determine whether the mismatch is in my router's assumptions or a genuine deviation in the
real RunCoordinator from the frozen contract. If the latter, this is a contract-gap escalation to
Sasikumar, not something to silently patch around.

Output: root cause + fix scoped to agent_runs.py, or a clear statement that this is a contract
issue to escalate.
```

## AI Prompt Template — Code Review (test-coverage focus)

```
Context: Here is a PR diff: [paste it]. My job is checking test coverage specifically, not
implementation logic (that's the named reviewer's job).

Task: Confirm it has tests matching this repo's existing convention (real Postgres/Neo4j for
integration tests, mocked only at the exact external HTTP boundary), covering happy path, the
documented error/precondition cases, and one adversarial case. Flag anything untested.

Output: a list of missing test cases, or "coverage looks complete" if genuinely so.
```

## Daily Completion Checklist

- [ ] PW-5 merged by ~hour 2
- [ ] PW-6 mocked-complete by ~hour 4.5, real-rewired by ~hour 7–7.5
- [ ] Regression suite run after every single merge you observe on `main`
- [ ] Bug list updated continuously, severities assigned
- [ ] Demo rehearsal scheduled and confirmed with Sasikumar

## Implementation Safety

**Protected files**: `app/analysis/*`, `app/graph/*`, `app/ai/agent/*`, `app/orchestrator/*`
(read/consume, don't edit), `app/agents/*` (all owned by Sasikumar/Rajan),
`docker/docker-compose*.yml`.

**Shared contracts**: `_contract.py`'s `Subject` shape (PW-5), `RunCoordinator`'s frozen signature
(PW-6) — both Sasikumar's, both read-only for you. `routers/__init__.py`'s one registration line —
announce before touching.

**Architecture rules**: PW-5 is a pure function — no DB, no HTTP, ever. PW-6 is a thin HTTP layer
over `RunCoordinator` — it doesn't compute anything itself, it delegates.

**API rules**: match `API_CONTRACTS.md` exactly. Never invent a field name, status code, or
pagination shape not documented there.

**UI rules**: not your workstream.

**Forbidden shortcuts**: skipping the mocked-first build for PW-6 and just waiting idle for the
real `RunCoordinator` — that recreates exactly the "wasted engineer" problem this plan was
redesigned to avoid. Batching regression runs to once a day instead of every merge.

**Common mistakes**: implementing logic in the router that belongs in `RunCoordinator` (the
router should be thin); forgetting the one-line registration in `routers/__init__.py`; testing
PW-6 only against the mock and never actually confirming the real rewire works end-to-end before
Checkpoint 2.
