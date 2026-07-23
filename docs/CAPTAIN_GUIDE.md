# CAPTAIN_GUIDE.md — GraphForge

**For the team captain only.** Everyone else's role, responsibilities, and daily process are in
`TEAM_IMPLEMENTATION_PLAN.md` §2/§7 — this document is the layer above that: how you keep five
independent workstreams coherent without becoming the bottleneck.

## Daily Responsibilities

1. **Day 0, before anyone branches**: close `IMPLEMENTATION_BASELINE.md`'s two outstanding
   checklist items — commit the current working tree, and fix the branch-name/CI mismatch (see
   Git Branching Strategy below). Confirm CI actually fires with a trivial test push. Neither of
   these is optional; every other workstream is blocked on them.
2. **Morning sync (15 min)**: each person states what they're about to do and what (if anything)
   blocks them. If genuinely nobody is blocked, this takes five minutes — don't manufacture status
   theater to fill the slot.
3. **Throughout the day**: review every open PR before it goes stale (same-day, per
   `TEAM_IMPLEMENTATION_PLAN.md` §6). You are the named reviewer for `app/orchestrator/*`,
   `app/agents/_framework/*`, `docs/graphforge/*`, and the one `ai_analysis.py` migration PR — but
   also the fallback reviewer for anyone whose named reviewer is genuinely unavailable.
4. **At each Integration Checkpoint** (`TEAM_IMPLEMENTATION_PLAN.md` §5): confirm every
   workstream owner says their branch is ready, merge personally, watch the regression suite run,
   don't move to the next block until it's green.
5. **End of day**: a quick written note (even one paragraph) of what merged, what's blocked, what
   changed from the plan — this is your own daily progress log, not a status report to anyone else
   (see Daily Progress Tracking below).

## Review Checklist

Beyond the standard Code Review Checklist in `RELEASE_CHECKLIST.md`, you're checking for things
only you can catch because you hold the whole picture:

- [ ] Does this PR's folder match its stated workstream in `TEAM_IMPLEMENTATION_PLAN.md` §4? A PR
      touching files across two workstreams' folders is a sign someone found a coupling nobody
      planned for — investigate before merging, don't just wave it through.
- [ ] Does this PR contradict anything in `docs/graphforge/*`? If a PR's approach differs from
      what the architecture docs describe, that's either a doc gap (fix the doc) or a scope
      violation (reject the PR) — never let the two silently drift apart.
- [ ] Is this the *first* PR to touch a Protected File? If so, this is the escalation
      `TEAM_IMPLEMENTATION_PLAN.md` §16 Rule 8 describes — you decide, don't let it merge by default.
- [ ] Does this PR's test coverage match Senior QA's expectations? You're not re-doing QA's job,
      but a PR with zero new tests for new logic shouldn't reach you without a QA comment on it
      already.

## Integration Schedule

Two checkpoints only, per `TEAM_IMPLEMENTATION_PLAN.md` §5 — resist the urge to add more ceremony:

| Checkpoint | When (of ~24h) | What must be true to proceed |
|---|---|---|
| 1 | Hour 6–7 | Framework + Orchestrator core merged; Planning Agent stub registers; `ai_analysis.py` delegation PR merged and reviewed by you personally |
| 2 | Hour 14–16 | Everything merged to trunk; full regression pass green; you've personally walked the exact demo path end-to-end |

If a checkpoint slips more than ~2 hours, that's your signal to invoke the scope cut in
`TEAM_IMPLEMENTATION_PLAN.md` §14's risk register (last row): reduce WS3/WS4 to "stub only," don't
silently let the timeline slip into the demo prep block.

## How to Detect Merge Conflicts Early

- Watch the Top 20 file list in `FINAL_ARCHITECTURE_REVIEW.md` Part 4 — if you see two open PRs
  both touching one of those files, that's not a coincidence, intervene before either merges.
- The single highest-leverage habit: when someone posts "about to touch X" in the shared channel
  (per `TEAM_IMPLEMENTATION_PLAN.md` §11's communication expectation), actually read it and flag
  if someone else is mid-edit on the same file — don't let the norm exist without you enforcing it.
- Before merging at a checkpoint, `git diff` the merge candidate against trunk yourself, even
  briefly — conflict markers or unexpectedly broad diffs are easier to catch before merge than after.
- If the same file conflicts twice in one day, that's a sign the ownership map in
  `TEAM_IMPLEMENTATION_PLAN.md` §4 has a gap — fix the doc, don't just resolve the conflict and
  move on.

## How to Review AI-Generated Code

Apply the Review prompt template from `TEAM_IMPLEMENTATION_PLAN.md` §8 to yourself, not just as
something you hand engineers:

1. **Check for invented API shapes first.** This is the single most common AI-tool failure mode
   in this project — an agent output field, an endpoint path, a response shape that looks
   plausible but isn't in `API_CONTRACTS.md`. Cross-reference before approving.
2. **Check for unrequested renames.** AI tools "clean up" names unprompted. A rename of a shared
   model/schema/component that wasn't the stated purpose of the PR is a red flag regardless of how
   good the new name is.
3. **Check that confidence scores have evidence.** This is specific to agent code: a PR
   implementing `AgentOutput` construction with a non-zero `confidence.score` and an empty
   `evidence` list is a bug per `AGENT_FRAMEWORK.md`, not a style nit — reject it.
4. **AI-authored is not review-exempt, ever.** Don't apply a lighter review pass because a PR
   description says "mostly AI-generated" — if anything, apply the same pass slightly more
   carefully, since plausible-looking-but-wrong is exactly what these tools produce under pressure.

## How to Maintain Architecture Consistency

- You are the sole owner of `docs/graphforge/*` merges (per `TEAM_IMPLEMENTATION_PLAN.md` §4) —
  anyone can propose a doc change, only you merge it. This is what keeps the docs from drifting
  into five different people's five different mental models.
- When a workstream owner reports a gap between the docs and what they need to build (like the
  `plan_freeform` Goal gap found in `FINAL_ARCHITECTURE_REVIEW.md`), fix the doc *before* they
  build around it — a five-minute doc edit is always cheaper than reconciling a divergent
  implementation later.
- Never let "the architecture is aspirational, just build what makes sense" become the norm — if
  the architecture is wrong, that's a doc fix you make, not a silent license for everyone to
  freelance differently.

## When to Reject a Pull Request

Reject (don't just request changes) when:
- It touches a Protected File without a prior escalation conversation.
- It invents an API shape or renames a shared model without discussion.
- It has zero tests for new logic.
- It silently swallows an exception or defaults an error path instead of surfacing it.
- It's clearly AI-generated and the author can't explain a design choice in it when asked — this
  means it wasn't actually reviewed by the person submitting it, which is the one thing
  `TEAM_IMPLEMENTATION_PLAN.md` §8 rule 10 makes non-negotiable.
- It bundles unrelated changes (e.g. a "while I'm here" edit to a Protected File riding along with
  an otherwise-fine PR) — ask for it to be split, don't merge the bundle.

Request changes (don't reject) when the approach is sound but: tests are thin, a doc wasn't
updated, naming could be clearer, or it's missing an edge case QA would want covered.

## How to Prepare Demo Builds

1. Confirm the exact commit that will be demoed is tagged (see Git Branching Strategy's tagging
   section below) — never demo off a moving branch tip you haven't personally verified.
2. Run the full regression suite against that exact commit, not "whatever's on trunk right now."
3. Walk the demo path yourself, live, against that commit — not from memory of an earlier
   rehearsal against a different commit.
4. Capture the screen recording backup against this same commit.
5. Confirm the real GitHub-connected demo data (the `sasmobileplay-spec` repos, the seeded PRs)
   is in the state the demo script expects — check the specific PR the script references still
   exists and is unmodified since the last rehearsal.

## Emergency Rollback Plan

If a checkpoint merge breaks trunk:
1. **Revert the merge commit immediately** (`git revert -m 1 <merge-commit-sha>`), don't try to
   forward-fix under time pressure — a clean revert gets trunk back to green fastest.
2. Notify the workstream owner; they re-open the issue on their own branch, fix it there, re-merge
   when green.
3. If the break is discovered close to demo time and can't be reverted cleanly (e.g. other work
   has since built on it), the fallback is: demo against the last known-good tagged commit
   (see Git Branching Strategy), not the broken trunk tip. This is exactly why tagging at each
   checkpoint matters — it's your rollback target.
4. Never demo an untested fix made in the last hour. If in doubt, demo the older, verified state
   plus the backup recording for anything the older state doesn't show.

## Risk Monitoring

Watch `TEAM_IMPLEMENTATION_PLAN.md` §14's risk register actively, not passively:

- **Contract drift** (Risk #1: `BaseAgent`/`AgentManifest` changing after WS3/WS4 started building
  against it) — ask Developer 1 and Developer 2 daily whether the contract they're building
  against has moved.
- **Schedule risk** (last row) — if Integration Checkpoint 1 slips, don't wait for Checkpoint 2 to
  react; cut scope immediately per the documented fallback (Planning Agent stub only).
- **In-memory `RunContext` fragility** — don't restart the backend process during the demo
  window; this is a known, accepted limitation, not something to "fix quickly" under pressure.

## Daily Progress Tracking

Keep it lightweight — a running note (not a formal report) covering:
- What merged today, against which workstream/ticket ID (`FEATURE_BACKLOG.md`'s GF-XXX numbers)
- What's blocked and on whom
- Any doc changes you made to `docs/graphforge/*` and why
- Any deviation from the plan (a scope cut, a contract change) — write it down the day it happens,
  not reconstructed later from memory

This log is what makes the Post-Demo Checklist's retro (`RELEASE_CHECKLIST.md`) actually useful
instead of everyone trying to remember what happened three days later.

---

## Git Branching Strategy (Task 7 Recommendation)

**Verified repository state**: the trunk branch is currently named `master`, not `main`. Every
planning document (`TEAM_IMPLEMENTATION_PLAN.md`, `DEVELOPER_ONBOARDING.md`) and
`.github/workflows/ci.yml` assume `main`. **This mismatch means CI has never actually triggered on
this repository** — a real, verified finding, not a hypothetical. No remote is currently
configured (`git remote -v` is empty).

**Recommendation — do this first, before any engineer branches:**

1. **Rename the trunk branch from `master` to `main`.** This is the standard modern default,
   matches every existing planning document without requiring five doc edits, and is the only fix
   that also repairs the dead-CI problem (rather than editing `ci.yml` to chase whatever the
   branch happens to be called). This is a git operation the Captain performs directly — not
   something delegated mid-hackathon.
2. **If/when this repository is first pushed to a hosted Git provider** (no remote exists yet),
   create the remote repository directly under the name `graphforge`, not `changeguard` renamed
   later — this avoids any future clone-URL/redirect churn entirely, for free, simply by
   sequencing the one-time action correctly (per `FINAL_ARCHITECTURE_REVIEW.md` Part 3).

**main/master usage**: `main` is the only long-lived branch. No `develop`, no `integration` branch
— that's ceremony a 24-hour build can't afford, and the existing repository has never used one.
`main` must stay green (tests passing) after every merge; nobody merges onto a broken `main`.

**Feature branches**: cut directly from `main`, one per workstream deliverable (not one per person,
not one giant branch per workstream that lives all day) — matching the "2-3 PRs per workstream"
sizing already specified in `TEAM_IMPLEMENTATION_PLAN.md` §3's "Expected merge frequency" column.

**Naming convention**: `ws/<workstream-number>-<short-kebab-case-description>`, e.g.
`ws/2-orchestrator-core`, `ws/3-planning-agent-stub`. Exact suggested names for every ticket are
in `FEATURE_BACKLOG.md`. This convention makes it immediately obvious from the branch name alone
which workstream (and therefore which ownership rules from `TEAM_IMPLEMENTATION_PLAN.md` §4) apply.

**Merge strategy**: squash-merge every PR into `main`. Keeps trunk history readable — one commit
per logical change, matching this repository's existing commit discipline (its commit history to
date is already one-feature-per-commit, not a pile of "wip"/"fix" commits).

**Commit message format**: matching the existing repository convention (`feat: ...`,
`fix: ...`-style Conventional-Commits-adjacent prefixes are already visible in this repo's log,
e.g. `feat: Implement Change Investigation Agent with tools, planner, and models`). Continue this:
`<type>: <imperative summary>`, where `type` is `feat`/`fix`/`docs`/`test`/`refactor`. Reference
the `FEATURE_BACKLOG.md` ticket ID in the body when useful (e.g. `Implements GF-007.`), not the
subject line — keep subject lines readable without ticket-number noise.

**Tagging strategy**: tag `main` at each Integration Checkpoint (`checkpoint-1`, `checkpoint-2`)
and immediately before the demo (`demo-final`). These tags are your Emergency Rollback targets
(above) and your Demo Build reference (above) — without them, "roll back to the last good state"
has no concrete meaning under pressure.

**What NOT to do**: don't introduce a `develop`/`release` branch model — this repository has never
used one and a hackathon timeline doesn't benefit from the extra merge hops. Don't rebase `main`
itself (only feature branches rebase onto `main`, never the reverse). Don't force-push to `main`
under any circumstance, including "fixing" a bad merge — revert instead (Emergency Rollback Plan,
above).
