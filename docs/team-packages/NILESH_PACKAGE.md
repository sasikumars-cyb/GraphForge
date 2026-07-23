# NILESH_PACKAGE.md — Day 0 Implementation Package

Source of truth: `TEAM_EXECUTION_PLAN.md` Section 3 (PW-7), Section 5, `UI_GUIDELINES.md`. This
package extracts what you need to start coding at hour 0 — you don't wait for anyone.

## Mission

Make the multi-agent story visible **and triggerable** — both agents' run history in one place,
in the existing design system, with a way to actually kick off a run for each (free text for
Planning, a real PR reference for Review), not just view results after the fact.

## Features

- `AgentsPage`: run history for both agents, `ReasoningLogPanel` reused for run detail (do not
  rewrite it — it's already agent-agnostic in its props).
- `components/agents/{AgentCard, ConfidenceBadge, EvidencePanel}.tsx`: compose existing
  `Card`/`StatusBadge` primitives, no new colors/spacing/visual primitives.
- **Two trigger inputs on the page**: the free-text box (`goal=plan_freeform`) and a PR-reference
  field/picker (`goal=review_pr`, `subject_reference=pr:<id>`) — both POST to the same
  `agent-runs` endpoint. Without the second input, the Review Agent's run history could only ever
  be viewed, never demonstrated live — this is not optional polish, it's load-bearing for the demo.
- `lib/api/agentRuns.ts`, `hooks/useAgentRun.ts`, nav wiring (`nav-items.ts`/`router.tsx` — these
  are your exclusive files this hackathon; nobody else touches them, and you don't touch anyone
  else's page).

## Owned Files

- `frontend/src/pages/AgentsPage.tsx`
- `frontend/src/components/agents/` (all files)
- `frontend/src/lib/api/agentRuns.ts` — **new file**, do not extend `lib/api/analysis.ts` (already
  the largest, most-extended API client file in the repo)
- `frontend/src/hooks/useAgentRun.ts`
- `frontend/src/types/agent.ts` — **new file**, do not extend `types/analysis.ts` (same reasoning)
- One-line additions to `frontend/src/components/layout/nav-items.ts` and
  `frontend/src/app/router.tsx`

## Owned Modules

None on the backend — pure frontend consumer.

## Implementation Order

1. **Hour 0**: start immediately, against **mocked** `agentRuns.ts` responses (matching
   `API_CONTRACTS.md`'s documented JSON exactly) — do not wait for PW-6 to merge.
2. **Hour 0–~5**: build `AgentCard`/`ConfidenceBadge`/`EvidencePanel`, `AgentsPage` with both
   trigger inputs, nav wiring — all testable against mocks.
3. **Hour ~5.5–7.5** (once PW-6's real endpoint lands): rewire `agentRuns.ts` from mocks to the
   real API.
4. **By Checkpoint 2 (hour 9–10)**: full live-wiring done, manually verified against a real run of
   each agent.

## Acceptance Criteria

- Both agents' run history render, using `ReasoningLogPanel` for detail, not a rewrite.
- A real PR can be triggered through the Orchestrator from this page — not just viewed after the
  fact elsewhere.
- Real API wired at Checkpoint 2, not left on mocks.
- Every agent-produced claim rendered shows its confidence and links to its evidence (per
  `UI_GUIDELINES.md` Consistency Rule 4) — never a raw text dump of agent output.

## Definition of Done

- [ ] `AgentsPage` + all `components/agents/*` merged, tested against mocks
- [ ] Both trigger inputs present and functional (free text + PR reference)
- [ ] Live-wired to the real `agent-runs` API by Checkpoint 2
- [ ] `ReasoningLogPanel` reused verbatim for run detail
- [ ] Nav entry added, tests passing

## Testing Checklist

- Component tests for `AgentCard`/`ConfidenceBadge`/`EvidencePanel` in isolation (existing
  component test convention — see `RiskBadge.test.tsx`/`StatusBadge.test.tsx` for the pattern).
- Page test for `AgentsPage`, mocking `agentRuns.ts` via `vi.spyOn` (existing convention — see
  `PullRequestDetailPage.test.tsx`).
- Test both trigger inputs independently: free-text submission calls the API with
  `goal=plan_freeform`; PR-reference submission calls it with `goal=review_pr` and the correct
  `subject_reference`.
- Test the disabled/loading states while a run is in flight, matching existing button-loading
  conventions (`"Running…"`-style labels).

## Files to Avoid

- `components/Card.tsx`, `Table.tsx`, `StatusBadge.tsx`, `RiskBadge.tsx` — compose, never edit.
- Any existing page (`PullRequestDetailPage.tsx`, `RepositoriesPage.tsx`, etc.) — read-only
  reference for patterns, zero diff.
- `frontend/src/types/analysis.ts`, `frontend/src/lib/api/analysis.ts` — do not extend, create new
  files instead.
- Anything on the backend.

## Dependencies

- `API_CONTRACTS.md`'s documented `agent-runs`/`agents` JSON shapes (available now — build against
  mocks immediately).
- PW-6 (Ani) for the real endpoint — only needed for the final live-wiring pass, not for the
  bulk of your build.

## Public Interfaces

None — pure consumer of PW-6's HTTP contract.

## UI Consistency Checklist

- [ ] No new colors — the "trigger a run" button uses `violet-600` (agentic action, consistent
      with the existing "Investigate" button), confirmed explicitly in `TEAM_IMPLEMENTATION_PLAN.md`
- [ ] No new spacing values outside the existing `gap-2`/`gap-4`/`gap-6` scale
- [ ] Loading states use the existing present-participle-verb convention (`"Running…"`)
- [ ] Empty states use the existing one-sentence, states-the-next-action convention
- [ ] Error states use the existing `rounded-lg border border-rose-500/30 bg-rose-500/10` banner —
      no bespoke error UI
- [ ] Confidence is always shown as a percentage next to the claim it supports — never a bare
      adjective like "high confidence"

## Example PR Titles

- `feat: add Agents page components against mocked API`
- `feat: add AgentsPage with dual trigger inputs (free text + PR reference)`
- `feat: wire Agents page to real agent-runs API`

## Example Commit Messages

```
feat: add AgentCard/ConfidenceBadge/EvidencePanel components

Composes existing Card/StatusBadge primitives per UI_GUIDELINES.md.
No new colors or spacing introduced. Built and tested against
mocked API_CONTRACTS.md-shaped responses.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

```
feat: add AgentsPage with free-text and PR-reference triggers

Both inputs POST to the same agent-runs endpoint with different
goal values. Without the PR-reference input, the Review Agent could
only ever be viewed here, never triggered - see
TEAM_EXECUTION_PLAN_CHANGELOG.md Finding 2.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

## AI Prompt Template — Implementation

```
Context: GraphForge frontend. Here is UI_GUIDELINES.md's Color Palette / Component Library
sections: [paste them]. Here is the existing Card/StatusBadge/ReasoningLogPanel component source:
[paste them]. Here is API_CONTRACTS.md's exact agent-runs/agents JSON: [paste it]. Here is
PullRequestDetailPage.tsx's action-button pattern to match: [paste it].

Task: Build [component/page], composing existing components only - no new colors, spacing, or
primitives. Include both a free-text trigger (goal=plan_freeform) and a PR-reference trigger
(goal=review_pr), both posting to the same endpoint. Follow the existing loading/empty/error
state conventions exactly.

Output: component code + a .test.tsx file following the existing test pattern in
[paste a reference test file, e.g. PullRequestDetailPage.test.tsx].
```

## AI Prompt Template — Debugging

```
Context: [Component] isn't rendering [describe the issue] when given [describe the data shape].
Here is the component: [paste it]. Here is the mocked/real API response it receives: [paste it].
Here is API_CONTRACTS.md's documented shape for comparison: [paste it].

Task: Determine whether the bug is in my component's handling or a mismatch between the mock and
the real API_CONTRACTS.md shape. If the real API (once PW-6 lands) differs from what I built
against, that's a contract mismatch to flag, not something to silently patch around in my
component.

Output: root cause + fix scoped to my files only.
```

## AI Prompt Template — Code Review

```
Context: Reviewing my own AgentsPage PR before requesting Sasikumar's review: [paste the diff].
Here is UI_GUIDELINES.md's Consistency Rules: [paste them].

Task: Check for: (1) any new color/spacing/component not in UI_GUIDELINES.md's existing set,
(2) whether every agent claim shown has a visible confidence and evidence link, (3) whether
Card.tsx/Table.tsx/StatusBadge.tsx/RiskBadge.tsx were edited instead of composed, (4) missing
loading/empty/error states, (5) missing tests for either trigger input.

Output: a list of findings, self-corrected before requesting review.
```

## Daily Completion Checklist

- [ ] Start against mocks at hour 0 — don't wait for anything
- [ ] Components + page merged by ~hour 5, tested against mocks
- [ ] Nav wiring is a one-line addition each to `nav-items.ts`/`router.tsx` — no one else touches
      these, and you don't touch anyone else's files
- [ ] Live-wire to real API once PW-6 lands (~hour 7–7.5), confirmed working by Checkpoint 2
- [ ] Full test suite (component + page) green before requesting final review

## Implementation Safety

**Protected files**: every existing frontend page, `components/Card.tsx`/`Table.tsx`/
`StatusBadge.tsx`/`RiskBadge.tsx`, `frontend/src/types/analysis.ts`,
`frontend/src/lib/api/analysis.ts` (extend via new files, never these).

**Shared contracts**: `API_CONTRACTS.md`'s `agent-runs`/`agents` JSON shapes — match exactly,
never infer field names from prose.

**Architecture rules**: not applicable to frontend work directly, but your two trigger inputs must
both call the same `agent-runs` endpoint with different `goal` values — don't invent two different
endpoints.

**API rules**: consume `API_CONTRACTS.md` exactly; never guess a field name or status code.

**UI rules**: see UI Consistency Checklist above — this is the section most directly relevant to
your work. `violet-600` for the trigger button, existing loading/empty/error conventions, no new
primitives.

**Forbidden shortcuts**: shipping only the free-text trigger and treating the PR-reference trigger
as optional polish — it isn't, it's the fix for the single most important gap the last plan review
found. Editing `nav-items.ts`/`router.tsx` beyond a one-line addition each.

**Common mistakes**: rewriting `ReasoningLogPanel` instead of reusing it (it's already
agent-agnostic in its props — check before assuming you need to change it); extending
`types/analysis.ts`/`lib/api/analysis.ts` instead of creating the new `agent.ts`/`agentRuns.ts`
files; waiting for PW-6 to merge before starting anything (you should be done with the bulk of the
UI before that even lands).
