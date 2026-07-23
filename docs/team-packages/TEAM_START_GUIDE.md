# TEAM_START_GUIDE.md — Sasikumar's Day-0 Script

This is the script Sasikumar follows when the team begins implementation. Every hour and rule
below comes from `TEAM_EXECUTION_PLAN.md` (as revised by `TEAM_EXECUTION_PLAN_CHANGELOG.md`) — this
document doesn't decide anything new, it sequences what's already decided into "what do I actually
say and do, in order."

## What Happens on Day 0?

**Hour 0:00–0:15** — Sasikumar fixes the one verified, real infrastructure blocker: rename the
branch `master` → `main`, then push a trivial commit and confirm `.github/workflows/ci.yml`
actually fires. This has never worked on this repository before this action.

```bash
git branch -m master main
git push -u origin main
# push one trivial commit, watch the Actions tab go green
```

**Hour 0:15–0:30** — Team reads `TEAM_EXECUTION_PLAN.md` Section 3 (the workstream table) together,
out loud. Sasikumar states explicitly, out loud, and waits for acknowledgment from everyone:
*"Nobody cuts a PW-1a/2/3/4/6/7 branch until I post the message 'PW-1 merged — go.'"* This is not
a formality — Finding 8 exists specifically because this exact mistake (branching from a stale
local `main` before PW-1 lands) is a real, likely failure mode under time pressure.

**Hour 0:30–1:00** — Three people start immediately, because nothing blocks them:
- Sasikumar branches `ws/1-agent-contract` and starts PW-1.
- Ani branches `ws/5-freetext-resolver` and starts PW-5 (against a draft `Subject` shape —
  `API_CONTRACTS.md` already documents it fully, no need to wait).
- Nilesh branches `ws/7-agents-page-mocks` and starts PW-7 against mocked API responses.

Vinod and Rajan do not branch yet. They read their own packages
(`VINOD_PACKAGE.md`, `RAJAN_PACKAGE.md`) and wait for the "go" message.

## Who Starts First?

Sasikumar, on PW-1 — this is the one genuinely serial dependency in the entire plan. Everyone
else's real work is gated on it (Ani's PW-5 and Nilesh's PW-7 are gated only on
*documented shapes*, not on PW-1 merging, so they start in parallel immediately).

## When Do People Branch?

- **Immediately** (hour 0–0:30): Sasikumar (PW-1), Ani (PW-5), Nilesh (PW-7).
- **The moment Sasikumar posts "PW-1 merged — go"** (target: hour ~2): Sasikumar branches again
  for PW-1a, then PW-3. Vinod branches for PW-2. Rajan branches for PW-4.
  Everyone runs `git pull origin main` immediately before cutting their branch — not "some time
  after" the go message, immediately, to avoid branching from a `main` pulled before PW-1 actually
  merged.

```bash
git checkout main && git pull origin main
git checkout -b ws/2-orchestrator-runcoordinator   # example for Vinod
```

- **Ani branches again for PW-6** once PW-1's frozen `RunCoordinator` signature exists
  (same "go" message — PW-6 builds against the frozen signature and mocks immediately, no need to
  wait for Vinod's actual implementation).

## When Do They Merge?

Small, frequent PRs — 1–3 per workstream over the day, never one giant end-of-day PR per person.
**PW-2 specifically ships as staged sub-PRs**: a `Run`/`AgentStep` models+migration PR first
(~hour 4), then `RunCoordinator` itself (~hour 5.5–6). This means most of PW-2's review happens
incrementally, before Checkpoint 1 arrives — the checkpoint is integration verification, not a
backlog of first-pass reviews landing all at once.

**Only Sasikumar merges to trunk.** No exceptions, no delegation, even under time pressure.

## When Is Checkpoint 1?

**Hour 5.5–6.5.** Exit bar: both agents registered and selectable via `GET /agents`, **and a real,
existing PR resolves through the Review Agent adapter** — not a hardcoded test object. If this
isn't hit by hour 7, that's the trigger for the pre-agreed scope cut (see "What if someone gets
blocked," below) — not a silent slip past it.

Sasikumar's checklist at this checkpoint:
```bash
cd backend && uv run alembic check
cd backend && uv run pytest -q   # confirm 268-baseline still green
curl -s http://localhost:8000/api/v1/agents | jq
# manually confirm both agent_id="review" and agent_id="planning" (or whatever ids were chosen) are listed
```

## When Is Checkpoint 2?

**Hour 9–10.** Exit bar: `agent-runs` router rewired from mocks to the real `RunCoordinator`,
Nilesh's Agents page live-wired (both trigger inputs working against the real API), full
268+49-test regression pass green. **This is demo freeze** — no new scope after this point.
Rajan's Planning Agent only needs its stub-with-graph-grounded-evidence bar met here, not
the full 3+-input validation — that continues into Hardening, non-blocking.

## When Does Ani Begin Validation?

**Immediately, at hour 0** — not once there's something to test. Ani is coding PW-5 from
hour 0 and running the existing regression suite continuously from the very first merge onward,
throughout the entire day. Prompt Validation on the Planning Agent (confirming genuine,
graph-grounded evidence, not just non-empty evidence) happens as soon as PW-4 has a working
example, continuing through Hardening.

## When Is Demo Freeze?

The moment Checkpoint 2 goes green (target: hour 9–10). Everything after that is bug fixes
(prioritized by severity), the Planning Agent's fuller validation, and demo rehearsal — no new
scope, no new features, no "just one more thing."

## What Should Happen If Someone Finishes Early?

- **Sasikumar** (PW-1/PW-1a/PW-3 done before hour 4.75): shift immediately into review mode — don't
  start a fourth module. Use the extra time to review PW-2's staged sub-PRs as they land, not to
  batch review at the checkpoint.
- **Ani** (PW-5/PW-6 done ahead of schedule): pull regression testing forward — run the full
  suite against whatever's merged so far, even before a checkpoint, and start drafting the demo
  script early rather than waiting for Hardening.
- **Nilesh** (PW-7 against mocks done well before PW-6 is real): don't idle waiting for the
  real endpoint — polish loading/empty/error states, write additional component tests, or start
  drafting the demo-path walkthrough from the frontend's perspective.
- **Rajan** (Planning Agent stub done well ahead of Checkpoint 2): move straight into the
  full 3+-input validation early — there's no reason to wait for Hardening if the stub bar is
  already met comfortably.
- **General rule**: extra time buys *more validation and rehearsal*, never new workstreams. This
  plan intentionally has zero P2/stretch tickets — see `FEATURE_BACKLOG.md`'s Backlog Summary.

## What Should Happen If Someone Gets Blocked?

1. **Post in the shared channel immediately** — don't sit on a blocker hoping to resolve it alone
   for an hour. Sasikumar's 17% "helping teammates" time allocation exists specifically for this.
2. **If the blocker is a contract gap** (e.g., Rajan finds `_contract.py` doesn't fit the
   Planning Agent's needs): this is the single highest-priority interrupt of the day. Sasikumar
   drops whatever review is in progress and resolves it — a contract gap discovered at hour 3 is
   cheap; the same gap discovered at hour 8 after three people have built on the wrong assumption
   is expensive.
3. **If Checkpoint 1 is going to slip past hour 7**: pre-agreed scope cut, not a negotiation in the
   moment — the Planning Agent ships as stub-only (already-planned Definition of Done split), full
   prompt work becomes non-blocking Hardening work. Nobody needs to improvise this decision under
   pressure; it's already made.
4. **If a workstream is stuck on a tooling/environment issue** (not a contract or design question):
   Sasikumar helps directly if it's fast, otherwise pairs the blocked person with whoever has the
   most relevant context — Vinod for anything Orchestrator-adjacent, Sasikumar for anything
   contract-adjacent.

## What Should Happen If a PR Conflicts With Another Engineer?

1. **Check `TEAM_EXECUTION_PLAN.md` Section 7 first** — the Top 30 file list names an owner and a
   reviewer for almost every file that could plausibly conflict. If the conflicting file is on that
   list, the ownership answer is already decided; don't re-litigate it.
2. **The PR author resolves the conflict** by rebasing onto current `main` before requesting
   review — reviewers should never see conflict markers. `git pull origin main`, rebase, re-test,
   re-push.
3. **If the conflict is on a Shared Ownership file** (`nav-items.ts`/`router.tsx`,
   `routers/__init__.py`, `pyproject.toml`/`package.json`): the two people involved should have
   announced *before* touching it, per each package's own instructions. If they didn't, that's a
   quick verbal fix — coordinate who merges first, the other rebases.
4. **If the same file conflicts twice in one day**: that's a signal the ownership map has a real
   gap, not just bad luck. Sasikumar updates `TEAM_EXECUTION_PLAN.md` Section 7 same-day to close it,
   rather than letting the same collision happen a third time.
5. **Never resolve a conflict by discarding either person's work silently** — if you're not sure
   which side of a conflict is correct, ask the other engineer directly before merging your
   resolution.

---

## Quick Reference — Package Index

| Role | Package | Owns | Starts |
|---|---|---|---|
| Sasikumar | `SASIKUMAR_PACKAGE.md` | PW-1, PW-1a, PW-3 | Hour 0 |
| Vinod | `VINOD_PACKAGE.md` | PW-2 | Hour ~2 (on "go") |
| Ani | `ANI_PACKAGE.md` | PW-5, PW-6 | Hour 0 (PW-5), Hour ~2 (PW-6) |
| Rajan | `RAJAN_PACKAGE.md` | PW-4 | Hour ~2 (on "go") |
| Nilesh | `NILESH_PACKAGE.md` | PW-7 | Hour 0 |

Every package is self-contained — mission, files, dependencies, prompt templates, checklists,
exit criteria. Nobody should need to ask a clarifying question to begin; if you find yourself
needing one, that's itself worth flagging to Sasikumar, since it likely means something in this
guide or your package needs a same-day correction, not a one-off answer that only you get to hear.
