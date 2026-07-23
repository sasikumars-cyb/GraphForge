# FEATURE_BACKLOG.md — GraphForge

Implementation-ready tickets, derived directly from `TEAM_IMPLEMENTATION_PLAN.md`'s workstreams
and `IMPLEMENTATION_BASELINE.md`'s checklist. Every ticket maps to exactly one workstream owner —
no ticket requires two people to both write code in the same PR. Suggested branch names follow
`TEAM_IMPLEMENTATION_PLAN.md` §6's `ws/<n>-<short-name>` convention.

Priority: **P0** = blocks other work, do first. **P1** = core hackathon scope. **P2** = stretch,
only if time remains.

---

## GF-001 — Day-0: Baseline commit + branch/CI fix

**Description**: Commit the current working tree (WS0 rebrand + this preparation pass's doc/bugfix
edits) as the hackathon baseline. Rename the local branch from `master` to `main` (or update
`.github/workflows/ci.yml`'s trigger to match `master` — renaming to `main` is preferred since
every planning document already assumes it). Confirm CI actually fires on the next push.
**Owner**: Captain
**Priority**: P0 — blocks every other ticket; nobody branches until this is done
**Dependencies**: None
**Acceptance Criteria**:
- `git status --short` is empty on the trunk branch
- The trunk branch name matches what CI and every doc assumes
- A trivial push triggers a green CI run (confirms the mismatch is actually fixed, not just renamed)
**Definition of Done**: Team notified that the trunk is ready to branch from
**Estimated effort**: 15 minutes
**Suggested branch name**: N/A — direct action on trunk, not a feature branch

---

## GF-002 — (Reference) WS0: Rebrand sweep

**Description**: Already complete — recorded here for backlog traceability only. Every
"Must Rename Immediately" occurrence from `FINAL_ARCHITECTURE_REVIEW.md` Part 2 updated across
frontend, backend, and both READMEs; all matching test assertions updated in the same pass.
**Owner**: (completed — was Senior QA per `TEAM_IMPLEMENTATION_PLAN.md` WS0)
**Priority**: P0 (was)
**Dependencies**: None
**Acceptance Criteria**: ✅ Met — verified live in browser, 49/49 frontend + 17/17 relevant backend
tests passing.
**Definition of Done**: ✅ Met
**Estimated effort**: (actual: ~1 session)
**Suggested branch name**: N/A — already merged

---

## GF-003 — Agent Framework: `BaseAgent` + `AgentManifest` contract (draft)

**Description**: Define `AgentManifest` (agent_id, purpose, accepted subject types, goals, cost
class, max_graph_hops, output_schema) and `BaseAgent`'s abstract execution contract, per
`AGENT_FRAMEWORK.md` § Agent Manifest. Publish this as a draft the moment it's stable enough for
GF-012 to code against — do not wait for the full implementation.
**Owner**: Senior Engineer
**Priority**: P0 — GF-012 and GF-015 both depend on this shape
**Dependencies**: None
**Acceptance Criteria**:
- `AgentManifest` and `BaseAgent` exist in `backend/app/agents/_framework/`
- Developer 1 confirms (in writing, e.g. a PR comment) they can build the Planning Agent stub
  against the draft
**Definition of Done**: Merged, with at least a stub `AgentOutput`/`Evidence` schema
**Estimated effort**: 2 hours (draft), 4 hours (full, see GF-004)
**Suggested branch name**: `ws/1-agent-framework-core`

---

## GF-004 — Agent Framework: `ToolRegistry` + retry policy extraction

**Description**: Extract `ToolRegistry` and the confidence-triggered retry policy from the
existing `planner.py`'s `should_retry_after_low_confidence`, generalizing them to be usable by any
agent, not just Review.
**Owner**: Senior Engineer
**Priority**: P0
**Dependencies**: GF-003
**Acceptance Criteria**: `ToolRegistry` and retry policy are agent-agnostic; existing Review Agent
behavior is unchanged when using them (verified by GF-005's regression pass)
**Definition of Done**: Merged, unit-tested independent of any specific agent
**Estimated effort**: 2 hours
**Suggested branch name**: `ws/1-agent-framework-core` (same branch as GF-003, sequential commits)

---

## GF-005 — Migrate Review Agent to `app/agents/review/`

**Description**: Move `app/ai/agent/{investigation_agent,planner,tools,models,codeowners}.py` to
`app/agents/review/`, wrapping the existing logic in `BaseAgent`/`AgentManifest` with **zero
behavior change** — this is an extraction, not a rewrite.
**Owner**: Senior Engineer
**Priority**: P0
**Dependencies**: GF-003, GF-004
**Acceptance Criteria**: Every existing test in `tests/integration/test_investigation_agent.py`,
`tests/unit/ai/test_agent_planner.py`, etc. passes unmodified against the new location (imports
updated, assertions untouched)
**Definition of Done**: Old `app/ai/agent/` package removed, no dangling imports, full regression
suite green
**Estimated effort**: 3 hours
**Suggested branch name**: `ws/1-review-agent-migration`

---

## GF-006 — Frontend: Prompt template documentation review

**Description**: Not applicable this hackathon (documentation-only ticket, already delivered in
`DEVELOPER_ONBOARDING.md` and `TEAM_IMPLEMENTATION_PLAN.md` §8). Listed here only so the numbering
stays contiguous with the workstream table — no action needed.
**Owner**: N/A
**Priority**: N/A
**Dependencies**: N/A
**Acceptance Criteria**: N/A
**Definition of Done**: N/A
**Estimated effort**: N/A
**Suggested branch name**: N/A

---

## GF-007 — Orchestrator: Registry + rule-based Selector

**Description**: `app/orchestrator/registry.py` (agent registration by manifest) and
`app/orchestrator/selector.py` (a simple `Goal → [agent_id]` rule table — an `if/elif` is
correct for two Goals this hackathon, per `TEAM_IMPLEMENTATION_PLAN.md` M2's guidance; do not
over-build this).
**Owner**: Senior Engineer
**Priority**: P0
**Dependencies**: GF-005 (needs a real agent to register against)
**Acceptance Criteria**: `GET /agents` (once GF-010 exists) lists the Review Agent correctly;
Selector correctly maps `Goal=review_pr` to the Review Agent
**Definition of Done**: Merged, unit-tested with at least two Goal→agent mappings once GF-013
registers the Planning Agent
**Estimated effort**: 2 hours
**Suggested branch name**: `ws/2-orchestrator-core`

---

## GF-008 — Orchestrator: `Run`/`AgentStep` models + migration

**Description**: New Postgres models `Run` and `AgentStep` (per `ARCHITECTURE.md` § Domain Model),
plus the Alembic migration. **Must include the new models in `backend/alembic/env.py`'s import
list** — this exact class of bug (a model missing from that list) was just found and fixed for
`PullRequestAIAnalysis`; don't repeat it.
**Owner**: Senior Engineer
**Priority**: P0
**Dependencies**: None (can be built in parallel with GF-007)
**Acceptance Criteria**: `alembic check` reports no drift after the migration; `Run`/`AgentStep`
rows can be created and queried
**Definition of Done**: Migration applied to the dev DB, `alembic/env.py` updated, verified via
`alembic check`
**Estimated effort**: 1.5 hours
**Suggested branch name**: `ws/2-orchestrator-models`

---

## GF-009 — Orchestrator: RunCoordinator + in-memory `RunContext`

**Description**: `app/orchestrator/run_coordinator.py` (sequencing/dispatch) and
`app/orchestrator/run_context.py` (in-memory Shared Memory — **not Redis**, per the addendum in
`ARCHITECTURE.md` § Shared Memory; this is a deliberate, temporary hackathon substitution).
**Owner**: Senior Engineer
**Priority**: P0
**Dependencies**: GF-007, GF-008
**Acceptance Criteria**: A `Run` executing the Review Agent produces a persisted `AgentStep` with
confidence + evidence, matching what the existing `.../investigate` endpoint already returns
**Definition of Done**: Merged; regression-tested against the existing Review Agent test suite
**Estimated effort**: 3 hours
**Suggested branch name**: `ws/2-orchestrator-runcoordinator`

---

## GF-010 — Orchestrator: `agent-runs` API

**Description**: `POST /api/v1/agent-runs`, `GET /api/v1/agent-runs/{id}`,
`GET /api/v1/agent-runs`, `GET /api/v1/agents` — exact shapes per `API_CONTRACTS.md` § Agent
Orchestrator API.
**Owner**: Senior Engineer
**Priority**: P0
**Dependencies**: GF-009
**Acceptance Criteria**: Every endpoint matches `API_CONTRACTS.md` exactly (status codes, field
names, pagination envelope); Developer 2 (GF-016) confirms the contract is buildable against
**Definition of Done**: Merged, tested (happy path + each documented error status)
**Estimated effort**: 2 hours
**Suggested branch name**: `ws/2-orchestrator-api`

---

## GF-011 — Migrate `ai_analysis.py` endpoints to Orchestrator delegation

**Description**: `POST .../ai-analysis`, `.../investigate`, `.../publish-review` internally call
the Orchestrator with `Goal=review_pr` pinned to the Review Agent. **External contract must not
change** — same paths, same request/response shapes.
**Owner**: Senior Engineer
**Priority**: P0
**Dependencies**: GF-009, GF-010
**Acceptance Criteria**: Every existing test in `tests/integration/test_ai_analysis_api.py` passes
unmodified; the endpoints now also produce a queryable `Run`
**Definition of Done**: Single, small, isolated PR; **reviewed by the Captain personally** (this
file has the highest historical churn in the repo — see `TEAM_IMPLEMENTATION_PLAN.md` §4)
**Estimated effort**: 2 hours
**Suggested branch name**: `ws/2-ai-analysis-delegation`

---

## GF-012 — Planning Agent: FreeText Entry Resolver

**Description**: `app/context/resolvers/freetext.py` — resolves a free-text goal string into a
`Subject` for the Planning Agent to act on, standing in for the (out-of-scope) Jira/Confluence
resolvers.
**Owner**: Developer 1
**Priority**: P0
**Dependencies**: GF-003 (manifest shape, not full framework)
**Acceptance Criteria**: A free-text string like "plan the work to add Slack notifications to
release events" resolves to a valid `Subject` with `subject_type="freeform"`
**Definition of Done**: Merged, unit-tested with at least 3 distinct example inputs
**Estimated effort**: 1.5 hours
**Suggested branch name**: `ws/3-planning-agent-resolver`

---

## GF-013 — Planning Agent: manifest + stub

**Description**: `app/agents/planning/manifest.py` + a stub implementation that registers with
the Orchestrator (GF-007) and returns a minimal but real `AgentOutput` — enough to prove
registration and selection work end-to-end before the full prompt/tool implementation lands.
**Owner**: Developer 1
**Priority**: P0
**Dependencies**: GF-003, GF-012
**Acceptance Criteria**: `GET /agents` lists the Planning Agent; `Goal=plan_freeform` correctly
selects it (per `AGENT_FRAMEWORK.md`'s addendum defining this Goal)
**Definition of Done**: Registered and selectable, even if the output is minimal
**Estimated effort**: 2 hours
**Suggested branch name**: `ws/3-planning-agent-stub`

---

## GF-014 — Planning Agent: full implementation

**Description**: Real prompt template, tool set (reusing existing deterministic graph-read tools
from `app/analysis`/`app/graph` where sensible — do not duplicate them), and output schema,
producing genuine confidence-and-evidence-backed plans for a free-text goal.
**Owner**: Developer 1
**Priority**: P1
**Dependencies**: GF-013
**Acceptance Criteria**: QA's Prompt Validation (`TEAM_IMPLEMENTATION_PLAN.md` §12) passes on 3+
distinct real inputs — every confidence score has at least one non-empty `Evidence` entry, no
hallucinated claims
**Definition of Done**: Merged, tested (happy path, low-confidence retry, one failing-tool case)
**Estimated effort**: 3 hours
**Suggested branch name**: `ws/3-planning-agent-full`

---

## GF-015 — Frontend: `agentRuns.ts` API client + `agent.ts` types

**Description**: `frontend/src/lib/api/agentRuns.ts` and `frontend/src/types/agent.ts` — do not
extend the already-large `analysis.ts`/`analysis.ts` types file; these are new files.
**Owner**: Developer 2
**Priority**: P0
**Dependencies**: `API_CONTRACTS.md`'s contract (can start immediately, before GF-010 lands, by
building against the documented shape)
**Acceptance Criteria**: Types match `API_CONTRACTS.md` exactly; API client functions mirror the
existing `runAiAnalysis`/`investigatePullRequest` shape in `lib/api/analysis.ts`
**Definition of Done**: Merged, usable by GF-017 against mocked responses
**Estimated effort**: 1.5 hours
**Suggested branch name**: `ws/4-agents-api-client`

---

## GF-016 — Frontend: `AgentCard`/`ConfidenceBadge`/`EvidencePanel` components

**Description**: New components under `frontend/src/components/agents/`, composing existing
`Card`/`StatusBadge` primitives — no new colors, spacing, or visual primitives per
`UI_GUIDELINES.md`.
**Owner**: Developer 2
**Priority**: P0
**Dependencies**: GF-015
**Acceptance Criteria**: Every agent-produced claim rendered shows its confidence and links to its
evidence (per `UI_GUIDELINES.md` Consistency Rule 4)
**Definition of Done**: Merged with `.test.tsx` files following the existing component test
convention
**Estimated effort**: 2.5 hours
**Suggested branch name**: `ws/4-agents-components`

---

## GF-017 — Frontend: `AgentsPage` (against mocks) + nav wiring

**Description**: `frontend/src/pages/AgentsPage.tsx`, reusing `ReasoningLogPanel` for run detail.
Build against `vi.spyOn`-mocked `agentRuns.ts` responses first — don't block on the backend.
One-line additions to `nav-items.ts`/`router.tsx` (Developer 2's exclusive files this hackathon).
**Owner**: Developer 2
**Priority**: P0
**Dependencies**: GF-015, GF-016
**Acceptance Criteria**: Page renders a run list + detail view using mocked data; nav entry works
**Definition of Done**: Merged with tests using mocked API responses
**Estimated effort**: 2.5 hours
**Suggested branch name**: `ws/4-agents-page`

---

## GF-018 — Frontend: Wire `AgentsPage` to real API

**Description**: Swap the mocked `agentRuns.ts` calls for the real endpoint at the integration
checkpoint, once GF-010 is merged.
**Owner**: Developer 2
**Priority**: P1
**Dependencies**: GF-010, GF-017
**Acceptance Criteria**: A real run from each agent (Review, Planning) renders correctly end-to-end
**Definition of Done**: Merged; manual verification against the real backend, not just mocks
**Estimated effort**: 1 hour
**Suggested branch name**: `ws/4-agents-page-live`

---

## GF-019 — QA: Regression checklist + continuous execution

**Description**: Written regression checklist (`RELEASE_CHECKLIST.md`'s Testing Checklist),
executed after every merge to trunk — not batched at the end.
**Owner**: Senior QA
**Priority**: P0
**Dependencies**: Runs continuously alongside GF-003 through GF-018
**Acceptance Criteria**: The pre-existing 268-test baseline stays green throughout; any regression
is filed as a bug within one merge of it happening, not discovered later
**Definition of Done**: A running bug list with severities (Blocker/Major/Minor per
`TEAM_IMPLEMENTATION_PLAN.md` §12), continuously updated
**Estimated effort**: Continuous (not a single block of time)
**Suggested branch name**: N/A — QA runs the suite, files bugs against the relevant workstream's branch

---

## GF-020 — QA: Prompt Validation for Planning Agent

**Description**: Manually review at least 3 distinct free-text goal inputs to the Planning Agent,
confirming genuine evidence backs every confidence score (see GF-014's acceptance criteria).
**Owner**: Senior QA
**Priority**: P1
**Dependencies**: GF-014
**Acceptance Criteria**: Zero instances of a confidence score with empty evidence
**Definition of Done**: Findings documented, any failures filed as bugs against GF-014
**Estimated effort**: 1 hour
**Suggested branch name**: N/A

---

## GF-021 — Demo script + rehearsal

**Description**: Write and rehearse the demo path from `TEAM_IMPLEMENTATION_PLAN.md` §13 twice —
once by the Captain, once by someone else, to catch unconscious assumptions. Capture a backup
screen recording.
**Owner**: Captain + Senior QA
**Priority**: P0
**Dependencies**: GF-011, GF-014, GF-018 (needs the full demo path working)
**Acceptance Criteria**: `RELEASE_CHECKLIST.md`'s Demo Checklist fully checked; backup recording
exists and is playable offline
**Definition of Done**: Two full rehearsals completed, backup confirmed
**Estimated effort**: 2 hours
**Suggested branch name**: N/A

---

## Backlog Summary

| Priority | Ticket count | Total estimated effort |
|---|---|---|
| P0 | 16 (GF-001, 003–013, 015–017, 019, 021) | ~28 hours (parallelized across 5 engineers) |
| P1 | 4 (GF-014, 018, 020, plus stretch review) | ~7 hours |
| P2 | 0 (no stretch tickets defined — see `TEAM_IMPLEMENTATION_PLAN.md` §16 Rule 10: under-scope, don't over-build) |

This backlog intentionally has zero P2/stretch tickets. Per `TEAM_IMPLEMENTATION_PLAN.md` §14's
risk register (last row), if the team is ahead of schedule, the right move is hardening and
rehearsal time (GF-021), not new scope.
