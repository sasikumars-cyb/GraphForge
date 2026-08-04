# KAN-8 Implementation Report — Confidence Calibration Made Real

**Epic:** [KAN-8](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-8)
**Date:** 2026-08-04
**Status:** Substantively resolved (KAN-23 implemented and tested; KAN-24/KAN-25 explicitly deferred, not silently skipped)

## Summary

`ROADMAP.md`'s own risk register calls this out as a hard blocker past Phase 2: "Confidence scores become decorative (unchecked against outcomes)." Before this epic, `ConfidenceCalibration` rows were written on every workflow approve/reject decision (`workflow_service._record_confidence_calibration`) but nothing ever read them back — the table was a write-only audit log with no product surface. `frontend/src/types/calibration.ts` even had response types already scaffolded for an endpoint that didn't exist.

This epic (KAN-23) built that endpoint: `GET /api/v1/calibration/summary`, admin-only, computing real per-agent approval-rate-by-confidence-bucket curves, plus a per-`prompt_version` breakdown that flags a version whose approval rate has drifted materially from its own agent's overall rate — the concrete signal a human would need to catch "this agent's confidence score stopped meaning what it used to, right after this prompt change."

KAN-24 (a data-retention/anonymization policy for calibration rows) and KAN-25 (measuring the threshold against real production data) both require a decision or a data source this sandbox cannot produce; both are named explicitly below as deferred, not folded into "epic complete."

## Jira IDs

| Ticket | Outcome |
|---|---|
| [KAN-23](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-23) — Confidence calibration report | **Resolved.** New `GET /api/v1/calibration/summary` endpoint: per-agent totals, approval rate, average confidence, confidence-bucket breakdown (pre-existing), and a new per-`prompt_version` breakdown with a `flagged_miscalibrated` signal. 6 new integration tests covering admin gating, empty state, aggregation correctness, and both sides of the miscalibration-flag threshold (divergence above threshold with enough decisions vs. divergence below the decision floor). |
| [KAN-24](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-24) — Calibration data retention policy | **Deferred.** Requires a product/compliance decision (how long to retain `ConfidenceCalibration` rows, whether/how to anonymize) that no code path can answer. Left untouched rather than guessed at. |
| [KAN-25](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-25) — Tune the miscalibration threshold against real data | **Deferred.** The 20-percentage-point threshold and 5-decision floor shipped with KAN-23 are explicitly documented in `calibration.py` as starting points, "not derived from any measured baseline (none exists yet)" — this ticket's own premise is that tuning requires real multi-version production data, which doesn't exist in this sandbox. No LLM provider or production calibration history is available here to generate it. |

## Files changed

- `backend/app/api/v1/routers/calibration.py` — added `PromptVersionStat` model, `by_prompt_version` field on `AgentCalibration`, `_MIN_DECISIONS_FOR_FLAGGING`/`_MISCALIBRATION_THRESHOLD` constants, and rewrote `get_calibration_summary` to outer-join `ConfidenceCalibration` against `AgentStep` (on `run_id`+`agent_id`) to recover `prompt_version`, then group and flag per version
- `backend/tests/integration/test_calibration_api.py` — new; 6 tests (admin-only gating, unauthenticated rejection, empty-state response, bucket+prompt-version aggregation correctness, the miscalibration flag firing above threshold, and the same flag *not* firing below the decision floor despite a larger divergence)
- `frontend/src/types/calibration.ts` — added `PromptVersionStat` interface and `by_prompt_version` field on `AgentCalibration`, keeping the (still-unused) frontend contract in sync with the real backend response shape
- `docs/handbook/16_REALITY_CHECK.md` — updated the "Confidence calibration" row from "none implemented" to reflect the real endpoint and its actual boundary (no dashboard consumes it yet)

## Design decisions

1. **Joined against `AgentStep` rather than denormalizing `prompt_version` onto `ConfidenceCalibration`.** `ConfidenceCalibration` doesn't carry `prompt_version` — only the `AgentStep` that produced the decision does (`_record_confidence_calibration` writes one calibration row per step with confidence, keyed by `run_id`+`agent_id`). Joining at query time avoids a schema migration and keeps `ConfidenceCalibration` a pure decision-outcome record; the cost is a `LEFT OUTER JOIN` instead of a plain `SELECT`, negligible at this table's current scale (already capped at 5000 most-recent rows).
2. **`LEFT OUTER JOIN`, not `INNER JOIN`.** A calibration row with no matching `AgentStep` (not expected today, but not enforced by a DB constraint either) still counts toward the agent's overall total/approval-rate/bucket numbers — it just can't contribute to any specific prompt-version's breakdown, since it has no version to attribute to. Losing a row from the top-level agent numbers because of a missing join match would silently understate `total_decisions`, which is worse than a version-breakdown row that's simply absent.
3. **Both threshold constants are named, documented, and explicitly *not* claimed to be data-derived.** Following the same discipline KAN-25 itself calls for: rather than picking a number and presenting it as calibrated, the docstring on `_MISCALIBRATION_THRESHOLD` says outright "not derived from any measured baseline (none exists yet)" and names KAN-25 as the ticket to revisit it once real data exists.
4. **`_MIN_DECISIONS_FOR_FLAGGING` exists specifically to prevent a false-positive on small samples.** Tested directly: a prompt version with only 2 decisions and a 100-point divergence from its agent's overall rate is *not* flagged, while a version with exactly 5 decisions and a 50-point divergence *is* — the floor is enforced as `>=`, not `>`, matching the boundary the test suite checks.
5. **No frontend dashboard built.** The response types were already scaffolded and unused before this epic; adding a full calibration dashboard page is a real, separate scope of work (a new admin route, charts, no existing design to extend) that this incremental pass didn't attempt. The types file was kept in sync with the real API contract so a future pass has an accurate starting point, and this gap is named directly in Remaining work below rather than left implicit.

## Environment note

Same Neo4j-unavailable situation as every prior epic this session (Docker registry blocked by org egress policy in this sandbox) — irrelevant here regardless, since calibration data lives entirely in Postgres (`ConfidenceCalibration`, `AgentStep`, `Workflow`, `Run` are all Postgres-only tables). Postgres itself was found stopped at the start of this session (same idle-gap pattern as prior epics) and restarted cleanly via `service postgresql start`.

- `ruff`, `black`, `mypy` — clean on every changed file (`calibration.py` required one `type: ignore[arg-type]` for a known SQLAlchemy outer-join nullability gap mypy can't infer, documented inline)
- New integration suite (`test_calibration_api.py`): **6 passed**
- Full non-integration backend suite: **1883 passed** (unchanged from KAN-11's baseline — this epic added integration, not unit, coverage), same 23 pre-existing, unrelated `test_run_coordinator.py` failures
- Full integration suite: **250 passed**, 77 failed / 55 errors — all Neo4j-dependent (confirmed via grep: zero failures/errors reference `calibration`), consistent with every prior epic run in this sandbox

## Risks

- **No frontend surface exists yet.** The endpoint is real and tested, but nothing in the product actually shows it to anyone — an admin would need to call the API directly today. This is the natural next slice of KAN-23's remaining ambition, named explicitly rather than implied as done.
- **Thresholds are placeholders, not tuned values.** `_MISCALIBRATION_THRESHOLD` (0.20) and `_MIN_DECISIONS_FOR_FLAGGING` (5) are reasonable starting points, not measured against real approval-rate variance. Until KAN-25 can run against production data, `flagged_miscalibrated` should be read as "worth a look," not as a calibrated alarm.
- **The 5000-row cap on the underlying query is unchanged from before this epic** — still most-recent-first, still not a rolling time window. Noted as a pre-existing condition, not introduced here.

## Remaining work

- Build the admin-facing calibration dashboard the frontend types were originally scaffolded for.
- KAN-24, once the retention/anonymization policy decision is made.
- KAN-25, once real multi-prompt-version production data exists to tune the threshold constants against.
