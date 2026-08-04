# BLOCKER — KAN-26: Context Discovery hypothesis-driven feedback loop

**Epic:** [KAN-9](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-9) — Agent Framework & Orchestration Hardening
**Ticket:** [KAN-26](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-26) — Context Discovery has no full feedback loop
**Date:** 2026-08-04
**Status:** Not started. KAN-9's other two stories (KAN-27, KAN-28) are complete; this is the reason the epic is not.

## Blocker

KAN-26's acceptance criteria are not just "implement an iterative loop" — they explicitly require:

1. An iterative retrieval loop implemented with a hard bound on iterations/cost
2. **Investigation quality (evidence coverage, hypothesis confidence) measured before/after on the same sample cases**
3. **Feature flagged; default-on decision backed by the measurement, not assumption**

Criteria 2 and 3 require running real investigations against a real LLM provider, on real sample cases, and comparing outcomes. This sandbox has **no LLM provider configured** (confirmed directly: the pre-existing `test_run_coordinator.py` failures documented in the KAN-7 and KAN-9 reports are `PreFlightCheckFailed: No API key is configured for the 'OpenAI' provider`). There is no way to produce the measurement this ticket's own Definition of Done requires here — not a permissions problem, an actual absence of the thing being measured against.

## Evidence

- `backend/app/context_pipeline/reasoning/investigators.py` (1,704 lines) and `investigation_planner.py` (627 lines) are the two files this ticket names as affected — both large, and both implement the mid-loop checkpoint mechanism (ADR 0016) this ticket extends. Understanding them well enough to extend the loop correctly, without breaking the existing bounded-cost guarantee, is itself substantial, LLM-call-shaped work (the checkpoint decides whether to redirect a real investigation based on real LLM output).
- The ticket's own risk note: *"uncontrolled iteration could blow LLM cost budgets — must ship with a hard ceiling from day one."* Verifying a hard ceiling actually holds under real iteration requires running it, not just reading the code.
- ADR 0015's self-review (cited in the ticket) already names this gap directly — it was a known, deliberately deferred scope boundary, not an oversight, when the original bounded version shipped.

## Attempted solutions

- Considered implementing the mechanism behind a feature flag (default off) without running the measurement — rejected. Shipping an unmeasured, cost-risky change to a core reasoning loop, then leaving "measure before defaulting on" as someone else's problem, does not close this ticket; it just moves the same unmeasured risk into the codebase under an assumption of safety I cannot verify. That is exactly the "never guess" rule this session operates under.
- Considered a smaller, mechanically-safe slice (e.g., just widening the existing single mid-loop checkpoint to N checkpoints, still gated by a flag) — still requires the same before/after measurement to know whether N checkpoints improves anything over 1, so it doesn't avoid the blocker, only shrinks the code around it.
- Checked for a way to configure a provider in this sandbox: no API key is available, and provisioning one is outside what this session can do for itself.

## Recommendation

1. **This ticket needs to run in an environment with a configured LLM provider** (a real `AI_PROVIDER` + API key, matching `docs/deployment/13_AI_PROVIDER_CONFIGURATION.md`) before any code is written — the measurement is the deliverable's hard requirement, not an afterthought.
2. When that environment is available, the actual implementation work (extending ADR 0016's checkpoint to an N-bounded loop with an explicit stopping condition) is well-scoped by the ticket's own suggested solution and should be a contained change to `investigators.py`/`investigation_planner.py` — the blocker is entirely about the measurement step, not about design ambiguity.
3. In the meantime, KAN-9's other two stories (KAN-27: GitHub Entry Resolver + doc reconciliation; KAN-28: external-write authorization audit) are complete, tested, and committed — see `docs/reports/KAN-9-implementation-report.md`.
