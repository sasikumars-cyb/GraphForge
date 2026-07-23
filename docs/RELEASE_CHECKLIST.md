# RELEASE_CHECKLIST.md — GraphForge

Run through the relevant section at each stage. Nothing here is optional — if a box can't be
checked, the item under it isn't done, regardless of how close it looks.

## Development Checklist

- [ ] Read the relevant `docs/graphforge/*` section before writing code
- [ ] Confirmed the folder/file is yours per `TEAM_IMPLEMENTATION_PLAN.md` §4's ownership table
- [ ] Not touching a Protected File (`app/analysis/*`, `app/graph/*`, `app/indexer/*`,
      `app/integrations/*`, existing frontend pages, `docker/docker-compose*.yml`'s `name:` field
      and credentials) without a Captain conversation first
- [ ] Checked `frontend/src/components/` or existing backend interfaces for something to extend
      before writing anything new
- [ ] No new dependency added without Captain sign-off

## Code Review Checklist

- [ ] Exactly one named reviewer assigned, per the ownership table
- [ ] Diff checked for invented API shapes not matching `API_CONTRACTS.md`
- [ ] Diff checked for unrequested renames of shared models/fields
- [ ] Diff checked for new dependencies not already in `pyproject.toml`/`package.json`
- [ ] Diff checked for new UI colors/components/spacing not in `UI_GUIDELINES.md`
- [ ] Every confidence score in agent output has at least one non-empty `Evidence` entry
- [ ] No swallowed exceptions / silently-defaulted error paths
- [ ] Relevant doc updated in the same PR if this change makes a doc inaccurate
- [ ] Branch rebased on current trunk, no conflict markers visible to the reviewer

## Testing Checklist

- [ ] New code has tests matching the existing convention (real Postgres/Neo4j for integration
      tests, `httpx.MockTransport` for the exact external HTTP boundary — never a mocked DB session)
- [ ] Happy path covered
- [ ] Documented error/precondition cases covered (from `API_CONTRACTS.md` or `AGENT_FRAMEWORK.md`)
- [ ] One adversarial case covered (empty input, missing dependency, upstream failure)
- [ ] Full existing test suite still passes (268 backend baseline, 49 frontend baseline — a new
      failure count above these means something regressed)
- [ ] For a new agent: registered and appears in `GET /agents`
- [ ] For a new endpoint: matches `API_CONTRACTS.md` exactly (status codes, field names)

## Integration Checklist

Run at both Integration Checkpoints in `TEAM_IMPLEMENTATION_PLAN.md` §5.

- [ ] Every workstream owner confirms their branch is ready (Definition of Ready, §6)
- [ ] Captain merges each branch individually, checking `main` stays green after every merge
- [ ] Full regression suite run against merged `main`, not just the individual branch
- [ ] Manual cross-workstream walkthrough: trigger the Review Agent on a real PR, trigger the
      Planning Agent with a free-text goal, confirm both appear correctly on the Agents page
- [ ] `alembic check` reports no drift (catches the missing-model-import class of bug before it
      reaches the next migration)
- [ ] No Protected File was touched by any merged branch

## Demo Checklist

(Full detail: `TEAM_IMPLEMENTATION_PLAN.md` §13)

- [ ] Primary demo PR verified real and unchanged since the last rehearsal
- [ ] Backup PR verified as a fallback
- [ ] Screen recording of the full demo path captured and playable offline
- [ ] Full regression suite green as of the final rehearsal
- [ ] Agents page shows both agents' run history correctly, live, right before the demo starts
- [ ] Every team member knows their standby role (Captain narrates/drives; Senior Engineer,
      Developer 1, Developer 2 on standby for technical questions; Senior QA watches for live issues)
- [ ] Demo rehearsed twice — once by the Captain, once by someone who didn't write the script

## Presentation Checklist

- [ ] The story is stated in one breath: "This is ChangeGuard — one agent reviewing PRs. This is
      GraphForge — one graph, one orchestrator, multiple agents." (verbatim demo story:
      `TEAM_IMPLEMENTATION_PLAN.md` §13)
- [ ] Visual proof, not just narration: the Agents page showing two distinct agents' runs is
      the single most important screen — confirm it's the centerpiece, not an afterthought
- [ ] Confidence/evidence is visibly shown on-screen at least once (proves "evidence over
      assertion" isn't just a doc claim)
- [ ] Timing rehearsed against whatever slot length the hackathon actually gives — cut narration,
      never cut the live demo, if time is short
- [ ] A one-sentence answer ready for "what's next" (point to `ROADMAP.md` Phase 2/3 — Jira
      integration, Requirement/Architecture agents, GraphWriter)

## Hackathon Submission Checklist

- [ ] Repository is on the correct, committed baseline (`IMPLEMENTATION_BASELINE.md`'s checklist
      fully green — including the branch-name/CI fix)
- [ ] `README.md` accurately describes current capabilities (flag if it's drifted stale — it
      already has one known-stale "Status" paragraph predating the AI/agent work; don't let this
      submission add a second one)
- [ ] All required documentation present: `PRODUCT_VISION.md`, `ARCHITECTURE.md`,
      `UI_GUIDELINES.md`, `API_CONTRACTS.md`, `AGENT_FRAMEWORK.md`, `ROADMAP.md`,
      `GRAPHFORGE_TRANSFORMATION_PLAN.md`, `TEAM_IMPLEMENTATION_PLAN.md`,
      `FINAL_ARCHITECTURE_REVIEW.md`, `IMPLEMENTATION_BASELINE.md`, `DEVELOPER_ONBOARDING.md`,
      `FEATURE_BACKLOG.md`, this checklist, `CAPTAIN_GUIDE.md`
- [ ] No secrets committed (`.env`, API keys) — spot-check `git log -p` on any file touching
      `core/config.py` or `.env*` if unsure
- [ ] Demo recording backup exists in a location every team member can access without depending on
      one person's laptop

## Post-Demo Checklist

- [ ] Bug list from `GF-019`'s continuous QA pass triaged: which are real product bugs vs. known,
      accepted hackathon-scope limitations (see `IMPLEMENTATION_BASELINE.md`'s Known Limitations)
- [ ] Decide, as a team, which `docs/graphforge/ROADMAP.md` phase work (if any) continues
      post-hackathon — don't let this decision default silently to "nothing"
- [ ] If continuing: schedule the Redis-backed `RunContext` migration before any multi-process
      deployment (the in-memory substitution was explicitly temporary — see `ARCHITECTURE.md`'s
      addendum)
- [ ] If continuing: fix `test_connect_returns_503_when_not_configured` (small, isolated,
      documented in `IMPLEMENTATION_BASELINE.md`)
- [ ] Retro: which merge-conflict predictions in `TEAM_IMPLEMENTATION_PLAN.md` §11/Part 4 of
      `FINAL_ARCHITECTURE_REVIEW.md` actually happened, and which prevention strategies worked —
      feed this back into the next hackathon's planning documents rather than starting from scratch
