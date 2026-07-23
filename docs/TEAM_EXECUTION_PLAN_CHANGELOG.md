# TEAM_EXECUTION_PLAN_CHANGELOG.md — EDR Pass 2

Full record of the second Engineering Director review of `TEAM_EXECUTION_PLAN.md`: every finding
validated (or challenged) independently against recomputed numbers, one additional required
review pass, and the resulting update to the plan. Nothing in this pass touched the architecture,
introduced a new framework, or expanded scope — every change is a redistribution of existing work
or a fix to a real, previously-undetected gap.

---

## Accepted Findings

| # | Finding | Disposition |
|---|---|---|
| 1 | Senior Engineer's workload (PW-2 + PW-3, 5.5–6.5h) doesn't fit before Checkpoint 1 (hour 5–6/5–7), starting no earlier than hour 2–3 | **ACCEPTED — with a better fix than proposed.** The suggested fix (move PW-3 to the Captain) is correct but insufficient on its own — it still leaves the Senior Engineer with the full 4–5h Orchestrator bundle, landing Checkpoint 1 around hour 6.5–7.5. **Improved fix**: also move `Registry`/`Selector` (new PW-1a) to the Captain — both are trivial once PW-1 freezes their signatures (~45 min combined). This narrows the Senior Engineer's scope to just `RunCoordinator` + models (3–4h), which genuinely fits before a recalculated hour 5.5–6.5 checkpoint. |
| 2 | No real PR entry point into the Orchestrator; the demo can only prove a synthetic subject | **ACCEPTED — required, and smaller than first proposed.** Validated: without this, the Review Agent's run history is provable only in a test, never through any UI a judge would see clicked — a direct threat to the demo's central thesis. Smallest implementation found: `InvestigationAgent.investigate()` already takes a bare `pull_request_id`, so the resolver is a ~30-minute addition (`Subject(subject_type="pull_request", subject_id=f"pr:{id}", ...)`), folded into PW-3 rather than a new workstream. A matching PR-trigger input was added to PW-7 (frontend) so the path is actually clickable, not just callable. |
| 3 | Agent registration documented in two places (`agents/__init__.py` vs. `orchestrator/registry.py`) | **ACCEPTED.** Confirmed as a genuine contradiction, not a nitpick — row 4 and row 6 of the original Section 7 gave two different answers to the same question. Fixed: `agents/__init__.py` is now explicitly a package marker only, zero registration logic; `registry.py` is the single canonical registration point. |
| 4 | Senior QA depends on an interface (`RunCoordinator`'s shape) that's never explicitly frozen | **ACCEPTED.** Confirmed no hour was ever scheduled for the "Senior Engineer commits to a signature" handoff. Fixed by expanding PW-1's scope: the Captain now freezes `Registry.register()`, `Selector.select()`, and `RunCoordinator.execute()`'s method signatures (interface only, no bodies) alongside the original output-contract types. This has a second-order benefit beyond just "fixing the finding": it decouples PW-6 (QA) from ever needing to wait on the Senior Engineer's actual implementation timing. |
| 5 | Checkpoint timing doesn't match the estimated work | **ACCEPTED — recalculated, not just re-asserted.** With the Finding 1 redistribution: Checkpoint 1 moves from the original unrealistic "hour 5–6" to a recalculated, honest **hour 5.5–6.5**. Checkpoint 2 tightens slightly from "hour 9–11" to **hour 9–10**, because Finding 4's fix lets QA and Frontend build against mocks/frozen interfaces throughout, decoupled from exactly when the Senior Engineer's real implementation lands. |
| 6 | Developer 1 finishes too close to demo freeze | **ACCEPTED — MVP scope change, not timeline change** (per the explicit instruction to recommend one). Moving Checkpoint 2 later would eat directly into the hardening time this entire redesign exists to buy. Instead, PW-4's Definition of Done was split: the Checkpoint-2/demo-freeze bar is now "stub + at least one fully evidence-backed example" (with the graph-grounding requirement from the Additional Review, below); the original 3+-distinct-inputs Prompt Validation continues into the Hardening block, non-blocking. |
| 7 | Captain becomes a merge bottleneck near checkpoints | **ACCEPTED — structural fix, not "be more careful."** Real fix: PW-2 now ships as staged sub-PRs with their own target hours (a partial skeleton by hour ~4, the full implementation by hour ~5.5–6), each reviewed as it lands. By the time a checkpoint hour arrives, most of its review work is already done incrementally — the checkpoint itself becomes integration verification (regression + walkthrough), not a backlog of first-pass reviews arriving at once. |
| 8 | Developers may branch before PW-1 merges | **ACCEPTED — needs a concrete trigger, not a verbal agreement.** Fixed: the Captain now posts an explicit "PW-1 merged — go" message in the shared channel, and everyone runs `git pull origin main` immediately before cutting a branch — turning an assumed instant into an observable, named event that specifically guards against branching from a local `main` pulled before PW-1 actually merged. |

## Rejected Findings

None. All eight findings held up under independent recomputation — none were based on a
misreading of the plan or a miscounted estimate that, on reinspection, actually closed. Where the
suggested fix was insufficient (Finding 1) or the recommended remedy type needed to be chosen
explicitly (Finding 6), those judgment calls are recorded above, not silently substituted.

## Partially Accepted Findings

None outright — every finding's core diagnosis was correct. Finding 1 is the closest to a partial
acceptance in spirit: the *diagnosis* (workload doesn't fit) was fully accepted, but the
*suggested fix* (move only PW-3) was judged insufficient on its own and extended (also move
Registry/Selector) rather than implemented as literally proposed.

## New Findings

Found during the mandatory additional independent review pass, beyond the eight given findings:

| # | Finding | Severity | Disposition |
|---|---|---|---|
| N1 | **PW-4's original Definition of Done didn't require any Knowledge Graph interaction.** A Planning Agent producing only `kind="llm_reasoning"` evidence — a bare LLM call with zero graph grounding — would have technically satisfied the original DoD ("non-empty Evidence for 3+ distinct free-text inputs") while silently undermining `PRODUCT_VISION.md`'s explicit "GraphForge is NOT another chatbot" claim, in front of the exact audience that claim exists for. | High (demo-credibility risk, judge-visible) | **Fixed.** PW-4's DoD now explicitly requires at least one `Evidence` entry of `kind="graph_traversal"` or `kind="tool_call"`, not only `llm_reasoning`. QA's Prompt Validation pass checks for this specifically. |
| N2 | **PW-3 should wrap `InvestigationAgent.investigate()` (the agentic path), not `AIAnalysisService.analyze()` (the single-shot path)** — a decision the original plan left unstated. Verified directly against the codebase: the agentic path already produces a structured `reasoning_log` (per-step tool calls + observations) that maps naturally onto the new `Evidence` schema; the single-shot path has only a flat confidence/reasoning string and would produce thin, low-quality evidence by comparison — exactly the wrong choice for the one code path the demo's credibility rests on most heavily. | Medium (quality risk, not a hard blocker, but affects how compelling the Review Agent's shown evidence looks next to the Planning Agent's) | **Fixed.** PW-3's deliverables now explicitly name `InvestigationAgent.investigate()` as the wrapped method, with the rationale stated inline so it isn't silently reversed by whoever implements it. |
| N3 | **`API_CONTRACTS.md`'s `GET /api/v1/agents` endpoint has no full example JSON response** — verified directly (only a one-line prose description exists, unlike `POST`/`GET /agent-runs`, which both have full examples). | Low | **Not fixed in this pass** — flagged for whoever picks up PW-6/PW-7; a 5-minute addition to `API_CONTRACTS.md`, not urgent enough to block kickoff. |

## Updated Timeline

| Milestone | Original plan | EDR Pass 2 (recalculated) |
|---|---|---|
| PW-1 merges | Hour 2–3 | Hour ~2 |
| PW-1a (Registry+Selector) merges | N/A (didn't exist — was bundled into PW-2) | Hour ~2.75 |
| PW-3 (adapter) merges | Bundled with PW-2, effectively hour 6.5–8.5 | Hour ~4.75 (parallel with PW-2, not sequential after it) |
| PW-2/`RunCoordinator` merges | Hour 6–7 (bundled with Registry/Selector/RunContext) | Hour 5.5–6.5 (narrowed scope) |
| **Checkpoint 1** | Hour 5–6 (claimed, didn't fit the plan's own numbers) | **Hour 5.5–6.5** (recalculated, fits) |
| PW-6 mocked-complete | N/A (informal) | Hour ~4.5 |
| PW-6 real-rewired | Hour 9–10 (implicitly, per original Section 13) | Hour ~7–7.5 |
| **Checkpoint 2** | Hour 9–11 | **Hour 9–10** (tightened) |
| Hardening window | ~6–8h (claimed) | **~8h** (now honestly earned, not asserted) |

## Updated Work Distribution

| Person | Original coding scope | Revised coding scope | Net change |
|---|---|---|---|
| Captain | PW-1 only (~1–2h, ~10% of day) | PW-1 + PW-1a + PW-3 (~4–4.75h, ~18% of day) | **+3 hours** — this is the change that actually closes Finding 1's gap |
| Senior Engineer | PW-2 + PW-3 (~5.5–6.5h) | PW-2 only, narrowed to `RunCoordinator` + models (~3–4h) | **−2 to −2.5 hours** |
| Senior QA | PW-5 + PW-6 (~3–3.5h) | Unchanged in scope, but PW-6 can now start ~4 hours earlier against a frozen interface instead of an informal handoff | No change in hours, earlier start |
| Developer 1 | PW-4 (~5–6h) | PW-4, widened to 5–7h for realism, with a split DoD (stub for demo freeze, full validation non-blocking) | Effort estimate widened, not the scope |
| Developer 2 | PW-7 (~5–6h) | PW-7, +PR-trigger input (~5–6.5h) | Small increase for the second trigger UI |

This is not an equal redistribution and isn't meant to be — the Captain's coding share nearly
doubled specifically because it was the only way to make the Senior Engineer's remaining,
genuinely-hard work fit the schedule without adding a person or slipping the checkpoint.

## Updated Critical Path

**Before**: `D0 → PW-1 → PW-2 (bundled) → PW-3 (sequential, same owner) → CP1 → PW-6 → CP2`. PW-3
sat on the critical path purely because it shared an owner with PW-2, not because of a real data
dependency.

**After**: `D0 → PW-1 → PW-2 (narrowed) → CP1 → PW-6 (rewire) → CP2`. PW-3 is off the critical path
entirely — it's the Captain's, running in parallel with PW-2. The only genuinely serial chain
remaining is `PW-1 → PW-2 → Checkpoint 1`, and it's now sized correctly.

## Updated Dependencies

- PW-2 (`RunCoordinator`) now depends only on PW-1's frozen signatures — not on PW-1a's actual
  implementation to *start* (though it needs PW-1a merged to integration-test end-to-end, which
  happens well before PW-2 finishes, since PW-1a is ~45 minutes of work done early).
- PW-6 now depends on PW-1's frozen `RunCoordinator` signature specifically (not an informal
  Senior Engineer commitment) — this is the single most valuable dependency change in this pass,
  since it fully decouples QA's start time from the Senior Engineer's actual progress.
- PW-3 now depends on PW-1a's `Registry` (both Captain-owned, sequenced within one person's day,
  not a cross-person handoff).
- PW-7 gains a dependency it didn't have before: a PR-reference lookup path must exist (via PW-3)
  before the frontend's new PR-trigger input can be wired to something real — satisfied by
  Checkpoint 1, before PW-7's live-wiring work begins.

## Updated Demo Flow

Verified against the required checklist — Review Agent, Planning Agent, Orchestrator, shared
execution pipeline, shared run history, Knowledge Graph integration, end-to-end workflow:

| Capability | Achievable before this pass? | Achievable after this pass? |
|---|---|---|
| Review Agent | Yes (pre-existing, proven) | Yes, unchanged |
| Planning Agent | Yes, but could ship with zero graph interaction (N1) | Yes, **with a required graph-grounded evidence entry** |
| Orchestrator | Yes | Yes, unchanged |
| Shared execution pipeline | Yes — both agents call the same `RunCoordinator.execute()` | Yes, unchanged |
| Shared run history | **No** — only the Planning Agent could ever appear in it live, since no real PR could reach the Orchestrator (Finding 2) | **Yes** — a real PR resolves through PW-3's adapter and appears alongside Planning Agent runs |
| Knowledge Graph integration | Ambiguous — provable only via the pre-existing Review Agent, not the new Planning Agent (N1) | Yes — both agents now demonstrably touch the graph |
| End-to-end workflow (free text → agent → visible run; real PR → agent → visible run) | **No** — the real-PR half of this didn't exist | **Yes** — both halves now exist and are triggerable from the same Agents page |

**This is the most consequential finding of the entire review**: before this pass, the demo's own
central thesis was not fully achievable through any UI. It is now.

## Final Team Assignments

See `TEAM_EXECUTION_PLAN.md` Section 13 for the authoritative table (updated in this pass). Summary:

| Person | Primary | Secondary | Expected Completion |
|---|---|---|---|
| Captain | PW-1, PW-1a | PW-3 | Hour 4.75 (coding), continuous (review/integration) |
| Senior Engineer | PW-2 (narrowed) | None | Hour 5.5–6.5 |
| Senior QA | PW-5 → PW-6 | Continuous regression, demo rehearsal | Hour 1.5–2 (PW-5), Hour 7–7.5 (PW-6 real) |
| Developer 1 | PW-4 | None | Hour 5.5–6.5 (stub/demo bar), continuing into Hardening (full validation) |
| Developer 2 | PW-7 | None | Hour 9–10 |

## Remaining Risks

Carried forward, unresolved by this pass — none are blockers, all are the normal residual risk of
any hackathon plan, not defects unique to this one:

- **Schedule variance.** The recalculated numbers are real math, not optimism — but hackathon-day
  friction (environment issues, an unexpectedly gnarly edge case, a slow rebase) is not modeled and
  can't be. The pre-agreed scope cuts (Finding 6, Section 11) exist specifically to absorb this.
- **AI-tool misuse.** Section 9's rejection triggers (no migrating `app/ai/agent/*`, no shared
  `ToolRegistry`) rely on the Captain and named reviewers actually enforcing them in the moment,
  under time pressure, which is a human-process risk no document fully eliminates.
- **Demo-day network/GitHub outage.** Mitigated by the backup recording, not eliminated — outside
  the team's control.
- **`API_CONTRACTS.md`'s incomplete `GET /agents` example (N3).** Low severity, not fixed this pass,
  could cause a small amount of guesswork for whoever implements PW-6/PW-7 against it.
- **In-memory `RunContext` doesn't survive a mid-demo backend restart.** Known, accepted, procedural
  mitigation only (don't restart the process during the demo window).

None of these rise to the level of "the plan cannot be executed" — they're the residual risks any
reasonable reviewer would still flag after a plan is otherwise sound.

---

# FINAL VERDICT

**1. Is the execution plan now internally consistent?**
Yes. The registration-location contradiction (Finding 3) is resolved to a single source of truth.
The checkpoint timing (Finding 5) now matches the per-person effort estimates it's derived from,
rather than asserting a number the estimates didn't support. Section 13's Expected Completion
column agrees with Section 6/8/10's checkpoint hours, which it did not before this pass.

**2. Can five engineers work in parallel from Hour 2 onward?**
Yes. By hour ~2 (once PW-1 merges): the Captain codes PW-1a then PW-3, the Senior Engineer codes
`RunCoordinator`, Developer 1 codes the Planning Agent, Senior QA builds PW-6 against the frozen
interface and mocks, and Developer 2 continues PW-7 against mocks (already started at hour 0, along
with Senior QA's PW-5). All five are productively occupied simultaneously from this point.

**3. Is the critical path realistic?**
Yes, with normal caveats. `D0 → PW-1 → PW-2 → Checkpoint 1 → PW-6 rewire → Checkpoint 2 →
Hardening → Demo` is now sized against real per-person hour budgets, not asserted. PW-3 was
removed from the critical path entirely rather than just re-timed, which is a structural
improvement, not just a numbers fix.

**4. Is the demo achievable?**
Yes — but it was not, before this pass. Finding 2 and New Finding N1 together mean the original
plan could not have demonstrated a real pull request flowing through the Orchestrator, nor
guaranteed the Planning Agent showed genuine Knowledge Graph grounding. Both are now fixed and
verified against the actual demo-flow checklist above.

**5. Is there any remaining blocker that should prevent implementation?**
No blocker remains, given the corrections in this pass are applied to `TEAM_EXECUTION_PLAN.md`
(they are, as of this changelog). The remaining risks listed above are the ordinary residual risks
of any hackathon-scale plan — schedule variance, AI-tool misuse under pressure, external outages —
not defects specific to this plan's design.

## Declaration

**`TEAM_EXECUTION_PLAN.md` is READY FOR IMPLEMENTATION**, as revised by this pass. Five engineers
can begin executing it starting at Hour 0 tomorrow, subject to the two Day-0 actions already named
in the plan (commit the baseline — already done, verified `e6889ff` — and fix the branch/CI
mismatch, still outstanding and scheduled as the literal first action in Section 10).
