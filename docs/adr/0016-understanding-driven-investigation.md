# ADR 0016: Engineering Understanding drives investigation

## Status

Accepted.

## Context

ADR 0015 gave Context Discovery a cognitive reasoning layer: after
retrieval finished, a single LLM call turned curated evidence into
`EngineeringUnderstanding` (the validated conclusion) and
`InvestigationWorkspace` (scratch hypotheses, never shown to Planning).
That ADR's own self-review named the gap directly: it was a *post-hoc*
synthesis pass. A hypothesis that flagged "no evidence rules this out"
had no way to make the engine go get that evidence — there was no path
from the workspace back into `engine.py`'s action-selection loop.

This phase closes that gap: engineering understanding now participates
*during* investigation, not only after it. The instruction was explicit
that retrieval, the evidence package, graph traversal, repository
scoping, composite scoring, and the `EngineeringUnderstanding` schema stay
exactly as they are — this is a layer built on top of the existing
deterministic loop, not a replacement for it.

## Decision

### 1. `synthesize_engineering_understanding` runs more than once

Previously called exactly once, after the gather loop exited and
`curate_evidence` ran. Now it can also run **mid-loop**, immediately after
any action that (a) cost something real (not a free deterministic parse)
and (b) actually yielded new evidence, gated by a small fixed budget
(`MAX_MID_LOOP_SYNTHESIS_CALLS = 1` in `engine.py`) and by "evidence
actually changed since the last synthesis" (a `not_found`/`failed` outcome
that added an evidence record but taught nothing new doesn't earn a fresh
reasoning pass). The always-run call after the loop exits is unchanged and
unconditional — it is the only call that sees the final, curated
`EvidencePackage`, so it must always happen regardless of the mid-loop
budget.

Every call fully re-derives `workspace`/`understanding` from the complete
current ledger (same "recompute, don't accumulate" principle ADR 0015
already used for `enriched_text`) — except `investigation_history`, which
is explicitly carried forward and appended to by *code*, not regenerated
by the LLM (see Decision §3).

### 2. `capability_priority` — the deterministic bridge from workspace to action selection

`engine._select` stays exactly what its own docstring always insisted it
must be: deterministic and reproducible, never an LLM call at selection
time. What changed is that it now takes an optional `priority_boost: dict[str, float]`,
computed once per synthesis call by `reasoning.understanding.
capability_priority(workspace)` — a plain, pure function reading the
LLM's *already-produced* `information_gain_estimates` and
`next_investigation_candidates` (constrained to the four real capability
keys: `work_item`, `repository`, `architecture`, `documentation` — an
unlisted label is ignored, never guessed into the nearest match) plus any
unresolved `contradictions` (which always earn `architecture` a minimum
boost, since the graph is what most often confirms or refutes a
behavioral claim).

The boost adjusts a candidate's effective score *within its necessity
tier only* — `necessity_rank` is still compared first in the sort key, so
a boosted recommended action can never preempt an unmet required one
(verified by `test_select_priority_boost_never_overrides_necessity_tier`).
This is what "information gain drives the next investigation" means here:
a real, bounded influence on tie-breaking among already-legitimate
candidates, not a second selection mechanism competing with the
capability system ADR 0010/0014 already built.

### 3. Investigation history is code-authored, not LLM-authored

`InvestigationWorkspace.investigation_history` is the one field this
module writes itself. Every other field is entirely the LLM's account,
rebuilt from scratch each call; `investigation_history` is instead read
from the *previous* round's dump, kept, and appended to with one
deterministic line per call — cycle number, evidence count, hypothesis
count, unresolved-contradiction count, and (best-effort, matched by exact
description string) which hypotheses flipped status since last round.
This is a deliberate choice: a factual log of "what happened when" is
exactly the kind of thing that should not depend on the model remembering
to report it accurately, and keeps the fact/conclusion separation ADR
0015 established intact — a log of investigation actions is closer to a
fact than a conclusion.

### 4. `Contradiction` becomes a first-class model

`InvestigationWorkspace.contradictions: list[Contradiction]`
(description, evidence_for, evidence_against, resolved, resolution_note).
The system prompt (ground rules 8-9) explicitly instructs recording a
contradiction rather than silently averaging it away, and instructs the
model to *update* — not just repeat — a previous round's hypotheses and
contradictions, which is what the grounding text feeds back in (see
`_previous_round_lines`).

### 5. Cost is the real constraint, and it's bounded on purpose

Each mid-loop call is a genuine LLM invocation. Making every capability
execution a fresh reasoning pass, as read literally, would mean one LLM
call per gather cycle (up to `MAX_CYCLES = 8`) on top of the always-run
final call — a real cost multiplier this ADR deliberately does not take.
`MAX_MID_LOOP_SYNTHESIS_CALLS = 1` was chosen after actually measuring the
effect: the local test env has `ANTHROPIC_BASE_URL` configured, so
`invoke_llm_json` makes real (if ultimately failing) network calls during
unit tests, and a budget of 2 measurably doubled the `tests/unit/ai` suite
runtime (92s → 165s) versus the ADR 0015 baseline. A budget of 1 keeps
worst-case added cost at exactly one extra LLM call per discovery run —
one mid-loop checkpoint is enough for a live hypothesis or contradiction
to redirect the *rest* of the run's action selection, which is the
concrete capability this phase asked for.

## Self-review

The mission's own self-review questions, answered honestly:

- **Does Engineering Understanding actively control investigation?**
  Yes, but through one checkpoint per run, not continuously per cycle.
  `priority_boost` is real and tested (`test_select_priority_boost_
  breaks_ties_within_the_same_necessity_tier`,
  `test_mid_loop_synthesis_runs_and_is_bounded_by_its_own_budget`), but it
  is a single mid-loop intervention, not "before every capability
  execution" as the brief's literal wording asked for. That gap is
  intentional (see Decision §5's cost measurement) and stated here rather
  than silently narrowed.
- **Does the engine dynamically change direction?** Within the bound
  above — yes. `investigation_history`'s hypothesis-flip detection
  (`test_second_synthesis_call_carries_investigation_history_forward_
  and_detects_flip`) proves a hypothesis moving from `supported` to
  `rejected` between rounds is captured and visible, not silently lost.
- **Are contradictions investigated?** An unresolved contradiction always
  boosts `architecture`'s priority — but always the *same* capability,
  regardless of what the contradiction is actually about. Routing a
  contradiction's free text to the *specific* capability that could
  resolve it would require either an NLP classification step (a guess,
  which ADR 0007's no-guessing precedent argues against) or asking the
  LLM itself to name a capability directly in its `information_gain_
  estimates` (which it already can, and often will, for exactly this
  case) — `architecture` is the deliberately-chosen floor for when it
  doesn't. Documented here as a known coarseness, not hidden.
- **Would this behave like a senior engineer performing an investigation
  rather than a retrieval system?** More than ADR 0015's post-hoc version,
  because a hypothesis formed mid-investigation can now change what
  happens next — but it is one checkpoint, not a continuous loop, and
  that remains the honest, cost-bounded distance from the brief's full
  ambition. The natural next increment (not attempted here) is making the
  budget adaptive — e.g., spend a second mid-loop call only when the first
  one actually produced an unresolved contradiction or a low-confidence
  hypothesis, rather than a fixed constant — so cost scales with how much
  there is to reconsider, not with a flat cap.

## What this deliberately does not do

- Does not re-synthesize on every cycle — bounded to one mid-loop call
  plus the always-run final call, for measured cost reasons (Decision §5).
- Does not route a contradiction's free text to the specific capability
  that could resolve it — always boosts `architecture` as a deliberate,
  documented floor (Self-review above).
- Does not change the deterministic capability/gap/readiness system's own
  stopping condition (`_sync_gaps`, `readiness`) — `priority_boost` only
  ever re-orders among already-legitimate candidates within a necessity
  tier; it cannot make the engine stop earlier or later than the
  capability system already decides.
- Does not migrate Development/Testing/Documentation Planning — unchanged
  from ADR 0015, still reading `evidence_package` directly.

## Files

**Modified**
- `backend/app/context_pipeline/reasoning/understanding.py` — `Contradiction`
  model; `InvestigationWorkspace` gains `contradictions`,
  `next_investigation_candidates`, `information_gain_estimates`,
  `investigation_history`; `capability_priority()`; grounding text now
  feeds the previous round's hypotheses/contradictions back in;
  `investigation_history` carried forward and appended to deterministically.
- `backend/app/context_pipeline/reasoning/memory.py` — `DiscoveryMetadata.
  synthesis_calls`.
- `backend/app/context_pipeline/reasoning/engine.py` — `_select` gains
  `priority_boost`; `MAX_MID_LOOP_SYNTHESIS_CALLS`; mid-loop re-synthesis
  call site.
- `backend/tests/unit/ai/test_understanding.py` — `capability_priority`
  tests, cross-call `investigation_history`/hypothesis-flip test.
- `backend/tests/unit/ai/test_context_reasoning_engine.py` —
  `priority_boost` tie-break/necessity-tier tests, mid-loop wiring test.

## Test plan

- Deterministic `_select` tests: boost breaks ties within a necessity
  tier; boost never overrides necessity ranking.
- `capability_priority`: known labels mapped and clamped to [0, 1];
  unknown labels ignored; `next_investigation_candidates` fallback;
  unresolved contradiction boosts `architecture`; resolved contradiction
  does not.
- Engine wiring: mid-loop synthesis fires after a yielding paid action,
  respects the budget, and its `investigation_priority` output lands where
  `_select` reads it — tested with a fake `synthesize_engineering_
  understanding` (fast, deterministic; the real LLM-backed function is
  covered separately in `test_understanding.py`).
- Cross-call continuity: a hypothesis's status flip between two synthesis
  calls is captured in `investigation_history`.
- Full backend unit suite: 909/909 in `tests/unit/ai` unchanged (pass
  count identical to the ADR 0015 baseline); full `tests/unit` run
  confirms no regressions elsewhere.

## Migration / performance / rollback

- **Migration**: additive. `priority_boost` defaults to `None` (identical
  behavior to before); `DiscoveryMetadata.synthesis_calls` defaults to `0`
  for any already-persisted `WorkingContext`.
- **Performance**: up to one additional LLM call per discovery run versus
  ADR 0015 (bounded by `MAX_MID_LOOP_SYNTHESIS_CALLS`), measured directly
  against the `tests/unit/ai` suite runtime rather than estimated.
- **Rollback**: set `MAX_MID_LOOP_SYNTHESIS_CALLS = 0` to fully disable
  mid-loop synthesis while keeping everything else (the models, the
  always-run final call, `capability_priority`) intact; delete the
  `priority_boost` argument at the `_select` call site to fully revert to
  ADR 0015's behavior.
