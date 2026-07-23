# SASIKUMAR_PACKAGE.md — Day 0 Implementation Package

Source of truth: `TEAM_EXECUTION_PLAN.md` (as revised by `TEAM_EXECUTION_PLAN_CHANGELOG.md`),
`CAPTAIN_GUIDE.md`. This package extracts what you need to start coding immediately — it does not
introduce anything new. If anything here appears to conflict with `TEAM_EXECUTION_PLAN.md`, that
document wins; flag the discrepancy, don't silently resolve it your own way.

## Mission

Freeze the one contract everyone else builds against, ship the three small modules that are
trivial once it's frozen, then get out of the way — keep trunk green, keep everyone unblocked,
ship the demo. You write ~18% of the day's code and review ~30% of it. That ratio is deliberate,
not a suggestion to fill spare time with more implementation.

## Responsibilities

- Own PW-1 (Agent Contract), PW-1a (Registry + Selector), PW-3 (Review Agent Adapter + PR
  resolver) — all three, done by ~hour 4.75, then stop writing implementation code entirely.
- Fix the Day-0 CI/branch blocker before anyone touches code.
- Review PW-2, PW-4, PW-6, PW-7 and give final sign-off on every merge.
- Adjudicate every Protected File escalation.
- Merge every PR to trunk — you are the only merger.
- Run both integration checkpoints personally, including the manual cross-workstream walkthrough.
- Write and rehearse the demo script; capture the backup recording.

## Owned Modules / Owned Files

| Module | Files | Effort |
|---|---|---|
| PW-1: Agent Contract | `backend/app/agents/_contract.py`, `backend/app/agents/__init__.py` (package marker only) | 1.5–2.5h |
| PW-1a: Registry + Selector | `backend/app/orchestrator/registry.py`, `backend/app/orchestrator/selector.py` | 0.75h |
| PW-3: Review Agent Adapter + PR resolver | `backend/app/agents/review_adapter.py` | 1.75–2h |

## Files to Avoid

- `app/analysis/*`, `app/graph/*`, `app/indexer/*`, `app/integrations/*`, `app/ai/agent/*` —
  read-only reference only, frozen this hackathon. **Never modify `app/ai/agent/*`** — that's the
  entire point of PW-3 being an adapter, not a migration.
- `app/orchestrator/run_coordinator.py`, `app/models/run.py`, `app/models/agent_step.py` — Vinod's.
- `app/agents/planning/*` — Rajan's.
- `frontend/src/**` — Nilesh's, except final PW-7 review.
- `docker/docker-compose*.yml`'s `name:` field and any `POSTGRES_*`/`NEO4J_*` credential —
  **permanently protected**, renaming these orphans the live dev volumes holding real seeded demo
  data (real GitHub connection, 4 indexed repos, seeded PR rows). Do not touch, ever, this
  hackathon, for any reason.

## Dependencies

None for PW-1 (it's what everyone else waits on). PW-1a and PW-3 both depend only on your own
PW-1 having merged — nothing external blocks you after that.

## Public Interfaces You Define

```python
# backend/app/agents/_contract.py
@dataclass(frozen=True)
class AgentManifest:
    agent_id: str
    purpose: str
    goals: set[str]
    # ... per AGENT_FRAMEWORK.md's Agent Manifest section

class Evidence(BaseModel):
    kind: Literal["graph_traversal", "tool_call", "graph_fact", "llm_reasoning"]
    reference: str
    summary: str

class Confidence(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""

class Subject(BaseModel):
    subject_id: str
    subject_type: str
    graph_node_ids: list[str] = Field(default_factory=list)
    display_name: str = ""

class AgentOutput(BaseModel):
    agent_id: str
    subject_id: str
    confidence: Confidence
    evidence: list[Evidence]
    output_ref: str | None = None

class IAgent(Protocol):
    async def run(self, context: "AgentContext") -> AgentOutput: ...

# Frozen signatures ONLY — no bodies, these are implemented in PW-1a/PW-2
class Registry(Protocol):
    def register(self, manifest: AgentManifest, agent: IAgent) -> None: ...

class Selector(Protocol):
    def select(self, goal: str) -> str: ...  # returns agent_id

class RunCoordinator(Protocol):
    async def execute(self, subject: Subject, goal: str) -> "Run": ...
```

Match field names exactly to `API_CONTRACTS.md`'s DTOs (`Subject`, `Evidence`, `Confidence` —
copy the JSON shapes verbatim, don't improvise field names).

## Implementation Order

1. **Day 0, hour 0–0.25**: Rename branch `master` → `main`. Confirm `.github/workflows/ci.yml`
   fires on a trivial test push. This has never worked on this repo until you do this.
2. **Hour 0.25–2**: PW-1. Write `_contract.py` in full, including the frozen `Registry`/
   `Selector`/`RunCoordinator` signatures. Merge. Post "PW-1 merged — go" in the shared channel.
3. **Hour 2–2.75**: PW-1a. `registry.py` (dict-backed store), `selector.py` (if/elif on `goal`).
4. **Hour 2.75–4.75**: PW-3. Wrap `InvestigationAgent.investigate()` — **not**
   `AIAnalysisService.analyze()`, see Architecture Rules below — plus the PR-reference resolver.
5. **Hour 4.75 onward**: Review-only. Nothing further goes on your branch.

## Deliverables

- `backend/app/agents/_contract.py`, `backend/app/agents/__init__.py`
- `backend/app/orchestrator/registry.py`, `backend/app/orchestrator/selector.py`
- `backend/app/agents/review_adapter.py`

## Acceptance Criteria / Exit Criteria

- Vinod and Rajan both confirm in writing they can build against PW-1 without
  changes.
- `GET /agents` (once PW-6 exists) lists the Review Agent, registered via PW-1a's `Registry`.
- Triggering the Review Agent via the Orchestrator against a **real, existing PR id** produces
  output identical to today's direct `.../investigate` call.
- Existing test suite (268 backend tests) passes unmodified against everything you touched.

## Definition of Done

- [ ] `_contract.py` frozen, both consumers confirmed
- [ ] `registry.py`/`selector.py` correctly route both `review_pr` and `plan_freeform` once both
      agents register
- [ ] `review_adapter.py` produces identical output to the existing `.../investigate` endpoint for
      a real PR
- [ ] `app/ai/agent/*` has zero diff
- [ ] All three PRs reviewed by Vinod, merged, CI green

## PR Review Responsibilities

You are the named reviewer for PW-2, PW-4, PW-6, and PW-7 (final sign-off), plus any Protected
File escalation from anyone. Specifically check, every time:

- Does the diff touch `app/ai/agent/*` for any reason other than PW-3's read-only reference use?
  Reject on sight.
- Does the diff invent a `ToolRegistry` shared between the Review Agent and Planning Agent? Reject
  — this plan deliberately does not share tool-execution mechanics between the two agents.
- Does the diff match the exact `API_CONTRACTS.md` JSON shape (PW-6/PW-7 specifically)?
- For PW-4: does it include at least one `Evidence` entry with `kind="graph_traversal"` or
  `kind="tool_call"`? A Planning Agent with only `kind="llm_reasoning"` evidence fails review —
  it's an ungrounded chatbot, not proof the architecture generalizes.

## Integration Checklist

Run at both checkpoints:

- [ ] Every workstream owner confirms their branch is ready
- [ ] Merge each branch individually, confirm trunk stays green after each
- [ ] Run the full regression suite (268 backend + 49 frontend baseline)
- [ ] `alembic check` reports no drift
- [ ] Manual walkthrough: `GET /agents` lists both agents; triggering `review_pr` against a real PR
      and `plan_freeform` against free text both produce a persisted `Run`+`AgentStep`
- [ ] At Checkpoint 2 specifically: the Agents page shows both runs live, including one triggered
      through the new PR-reference input

## Daily Checklist

- [ ] Morning: post "PW-1 merged — go" the moment it's true, not before
- [ ] Review every open PR same-day, before it goes stale
- [ ] Confirm nobody has cut a PW-1a/2/3/4/6/7 branch before your "go" message
- [ ] End of day: one-paragraph log of what merged, what's blocked, any doc changes you made and why

## Common Mistakes

- Writing more than PW-1/PW-1a/PW-3. If you notice yourself starting a fourth module, stop — that's
  the exact bottleneck pattern this plan was redesigned to avoid.
- Letting a PR merge that touches `app/ai/agent/*`, even "just to fix a typo."
- Approving a Planning Agent PR whose evidence is entirely `llm_reasoning`.
- Batching all review to the checkpoint hour instead of reviewing PW-2's staged sub-PRs as they land.
- Forgetting to `git pull origin main` yourself before assuming your merge target is current.

## Commands to Verify Integration

```bash
# Confirm CI actually fires (Day 0, before anything else)
git branch -m master main
git push -u origin main
# push a trivial commit, watch the Actions tab

# Before merging any PW-2/PW-6 PR
cd backend && uv run alembic check

# Full regression, at every checkpoint
cd backend && uv run ruff check . && uv run black --check . && uv run mypy app && uv run pytest -q
cd frontend && npx tsc --noEmit && npx oxlint && npx prettier --check . && npx vitest run

# Manual walkthrough (once PW-6 exists)
curl -s http://localhost:8000/api/v1/agents | jq
curl -s -X POST http://localhost:8000/api/v1/agent-runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"subject_reference": "pr:<real-pr-uuid>", "goal": "review_pr"}'
```

## Merge Checklist

- [ ] Branch rebased on current `main`, no conflict markers
- [ ] Named reviewer approved
- [ ] CI green on the rebased branch
- [ ] Squash-merge (one commit per logical change)
- [ ] Tag `checkpoint-1`/`checkpoint-2`/`demo-final` at the relevant milestones

## Example PR Titles

- `feat: freeze AgentManifest/AgentOutput/Evidence contract + Orchestrator interface signatures`
- `feat: implement Registry and Selector for Agent Orchestrator`
- `feat: add Review Agent adapter with PR-reference resolver`

## Example Commit Messages

```
feat: freeze Agent Contract (manifest, output, evidence, confidence, subject)

Defines AgentManifest, AgentOutput/Evidence/Confidence, Subject, and the
frozen Registry/Selector/RunCoordinator method signatures every other
module builds against. Interface only, no implementation bodies.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

```
feat: implement Orchestrator Registry and Selector

Dict-backed agent registration and a rule-based Goal-to-agent-id
selector, per AGENT_FRAMEWORK.md's "How the Orchestrator Chooses
Agents" section. Implements PW-1's frozen signatures.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

## AI Prompt Template — Implementation

```
Context: I'm implementing the Agent Contract for GraphForge, the single frozen interface every
other new module (Orchestrator, Review Agent adapter, Planning Agent, agent-runs router) builds
against this hackathon. Here is AGENT_FRAMEWORK.md's Agent Manifest / Output Schema / Confidence
& Evidence sections: [paste them]. Here is API_CONTRACTS.md's exact Subject/Evidence/Confidence
JSON: [paste it].

Task: Define AgentManifest (dataclass), AgentOutput/Evidence/Confidence/Subject (Pydantic,
field names matching the JSON exactly), a one-method Protocol for what an agent exposes, and
the frozen (signature-only, no bodies) Registry/Selector/RunCoordinator interfaces.

Constraints: this file is read by every other engineer today — no speculative fields, no
partial implementations, nothing that isn't needed by a workstream in TEAM_EXECUTION_PLAN.md
Section 3.

Output: the contract module + a short docstring per class explaining what it's for.
```

## AI Prompt Template — Debugging

```
Context: PW-3 (Review Agent Adapter) is producing output that doesn't match what the existing
`POST /pull-requests/{id}/investigate` endpoint returns for the same PR. Here is the adapter
code: [paste review_adapter.py]. Here is InvestigationAgent.investigate()'s actual return shape:
[paste it]. Here is the AgentOutput contract it must translate into: [paste _contract.py].

Task: Find the exact field(s) where the translation is lossy or wrong. Do not change
InvestigationAgent itself — the bug is in the adapter's translation, not the wrapped code.

Output: the specific mismatch + a minimal fix to review_adapter.py only.
```

## AI Prompt Template — Code Review

```
Context: Here is a PR diff for [PW-2/PW-4/PW-6/PW-7]: [paste it]. Here is the exact contract/
DoD it must satisfy: [paste the relevant Section 3 workstream from TEAM_EXECUTION_PLAN.md].

Task: Check specifically for: (1) any touch to app/ai/agent/* beyond read-only reference,
(2) a shared ToolRegistry between Review and Planning agents, (3) invented API shapes not
matching API_CONTRACTS.md, (4) for PW-4 specifically: at least one graph_traversal/tool_call
Evidence entry, not only llm_reasoning, (5) missing tests, (6) swallowed exceptions.

Output: a list of concrete findings with file:line references, or "no findings" if genuinely clean.
```

## Implementation Safety

**Protected files**: `app/analysis/*`, `app/graph/*`, `app/indexer/*`, `app/integrations/*`,
`app/ai/agent/*`, every existing frontend page, `docker/docker-compose*.yml`'s `name:` field and
Postgres/Neo4j credentials, `.env`/`.env.example`'s live credential values.

**Shared contracts**: `_contract.py` (yours — frozen after PW-1 merges; any change after that is a
conversation with the whole team, not a silent edit), `API_CONTRACTS.md`'s documented JSON shapes.

**Architecture rules**: deterministic-before-probabilistic (unchanged existing principle) —
nothing in your three modules should compute a fact an existing deterministic engine could
compute instead. No shared `ToolRegistry`. No Review Agent migration.

**API rules**: `agent-runs`/`agents` endpoints match `API_CONTRACTS.md` exactly — you don't own
these routes (Ani does, PW-6) but you review them against this contract.

**UI rules**: not your workstream, but you're PW-7's final reviewer — check against
`UI_GUIDELINES.md`'s color table before approving.

**Forbidden shortcuts**: skipping the PR-reference resolver "for now" (this is the fix for the
single most important gap the last review found — without it, the demo cannot show a real PR
through the Orchestrator). Approving a PR because time is short rather than because it's correct.

**Common mistakes**: see above.
