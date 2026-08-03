# 08 — Problem-Solving Scenarios

Panel-style "what would you do" scenarios. Format per scenario: Diagnosis
→ Investigation → Design decision (what the system already does) →
Recovery → Trade-offs. Answer live in this shape — it demonstrates
process, not just a fact.

## Scenario 1: "Your Neo4j database crashes."

**Diagnosis**: Read-path queries (blast radius, dependency lookups,
Repository Understanding) start failing. Nothing is lost, because Neo4j
is architecturally a derived projection, not the source of truth.
**Investigation**: Check RDS/Neo4j instance status directly (per
`docs/deployment/09_DEPLOYMENT_RUNBOOK.md`'s incident table pattern —
"check security groups first, then instance status").
**Design decision already in place**: Engineering Memory (Postgres) holds
every hypothesis/validation/confidence transition that ever produced a
graph fact — the materializer (`app.knowledge_engine.materializer`) can
rebuild the graph from that log alone, proven by a real replay test.
**Recovery**: Restore/restart Neo4j (Aura's managed recovery, or EBS
snapshot restore if self-hosted), then re-run indexing or invoke the
materializer to repopulate. Multi-AZ RDS doesn't apply here — this is
Neo4j specifically, which has its own backup story (Aura built-in, or
scheduled EBS snapshots).
**Trade-off to name honestly**: the materializer is tested and proven,
but not yet the *live* write path — actual recovery today would most
likely mean re-running the deterministic indexer, not invoking the
materializer, since that cutover hasn't shipped. Say this precisely.

## Scenario 2: "Bedrock is unavailable."

**Diagnosis**: Every LLM-touching agent call fails or times out;
deterministic facts are unaffected.
**Investigation**: Check IAM Task Role permissions against the configured
`bedrock_model` first (`docs/deployment/09_DEPLOYMENT_RUNBOOK.md`'s
incident table: "a model change requires a matching IAM policy update, by
design — not an oversight"); separately check for an expired
credential/session token — a real, previously-observed failure mode in
our own dev environment (`ExpiredTokenException`).
**Design decision already in place**: graceful degradation is structural,
not a special case — `PromptBuilder` degrades to an empty narrative plus
a `status="failed"` Evidence entry rather than raising; Context
Discovery's synthesis falls back to a purely mechanical, deterministic
summary (`_deterministic_understanding`) rather than blocking the run.
**Recovery**: refresh credentials / fix IAM policy; no data corruption
risk since nothing partial gets promoted to trusted knowledge.
**Trade-off**: multi-provider failover (Bedrock → OpenAI/Gemini/Groq) is
architecturally possible (the registry pattern supports it) but not
automatic today — a provider outage requires a manual config change, not
an automatic failover.

## Scenario 3: "The LLM hallucinates."

**Diagnosis**: An LLM-sourced hypothesis proposes a relationship not
actually supported by evidence.
**Investigation**: Check its `ConfidenceState` — if the system is working
correctly, it should sit at `CANDIDATE` (the default for "no
confirmations, no contradictions") unless a validator's keyword table
happened to match text that doesn't actually support the claim (a real,
theoretical failure mode of `EvidenceKeywordValidator` — substring
matching isn't semantic understanding).
**Design decision already in place**: the hallucination can never write
directly to the graph (no generator does); it can't reach `Verified` from
one provider alone; the materializer never surfaces an unpromoted
candidate.
**Recovery**: a human correction via the Learning Engine
(`POST /repositories/{id}/learning/feedback`) with `trust_level=1.0`
overrides it directly — recorded as a new transition, never a silent
edit, so the wrong claim's history remains auditable.
**Trade-off**: `EvidenceKeywordValidator`'s substring matching is a real,
acknowledged limitation — "narrow, explainable pattern match," not
semantic verification. A sufficiently misleading piece of evidence text
could still cause a false confirmation. This is the honest cost of
choosing determinism/explainability over a more powerful but
non-deterministic (and non-auditable) semantic check.

## Scenario 4: "Validation disagrees with AI."

**Diagnosis**: This is the *expected*, designed-for case, not a failure —
it's exactly why the pipeline has five stages instead of one.
**Investigation**: Check the `ValidationResult.verdict` — `contradicts`
moves the relationship toward `REJECTED`/`CONFLICTING`, never silently
averaged away.
**Design decision already in place**: monotonic confidence aggregation —
a contradiction always weakens state, and the state machine has a
dedicated `CONFLICTING` tier specifically for "some confirmed, some
contradicted" rather than forcing a false resolution.
**Recovery**: nothing to "fix" — this is the system correctly refusing to
over-trust an LLM claim. If the AI's claim is later shown to be right, a
future run with better evidence can re-confirm it; the door isn't closed
permanently (append-only history means every new run is a new chance to
confirm or contradict).
**Trade-off**: this can look like the system being "wrong" to an outside
observer expecting a single confident answer — worth framing proactively
as evidence-over-assertion working as intended, not a bug.

## Scenario 5: "Two repositories disagree" (e.g. conflicting claims about a shared interface)

**Diagnosis**: Two hypotheses about the same relationship, from
different evidence sources, conflict.
**Investigation**: Check each hypothesis's own evidence and reliability
tier — `max_confirming_reliability_tier` and `confirming_source_types`
are exactly the fields that let the confidence engine reason about this
correctly (added specifically because the original contract couldn't
compute a correct incremental answer without them — see ADR 0018's RFC-01
amendments).
**Design decision already in place**: the higher-reliability-tier
evidence wins in the aggregation formula, but a genuine standoff between
equally-reliable, contradicting sources lands the relationship at
`CONFLICTING` rather than picking a winner arbitrarily.
**Recovery**: surfaced to a human via the Learning Engine for explicit
resolution if it matters enough to act on; otherwise it stays visibly
`CONFLICTING` in the API/UI rather than hiding the disagreement.
**Trade-off**: no automatic "which repo is more authoritative" heuristic
exists — by design, since inventing one would be exactly the kind of
guessing the deterministic-first principle rejects.

## Scenario 6: "A repository contains stale documentation."

**Diagnosis**: Documentation drift — Markdown content no longer matches
the indexed architecture graph.
**Investigation**: This is the Documentation Review Agent's specific job
(`app.agents.documentation`, goal `review_documentation`) — compares
Markdown against the indexed graph, reports outdated/missing/duplicate
docs and broken internal links.
**Design decision already in place**: read-only by design — it proposes
Markdown updates and new documents, "never applied automatically." This
is a deliberate human-in-the-loop boundary, matching the product's
explicit non-goal of autonomous, unattended writes.
**Recovery**: a human reviews and applies the proposed updates.
**Trade-off**: no automated staleness *detection trigger* (e.g. "alert me
when docs drift") exists yet — it's an on-demand agent, not a continuous
monitor.

## Scenario 7: "Cross-repository relationships fail to appear."

**Diagnosis**: Almost certainly one of the two documented, numbered gaps
— Feign name-matching (suffix-only normalization) or Kafka topic
detection (literal-string-only, no shared-SDK-wrapper support).
**Investigation**: Check the actual naming convention / Kafka usage
pattern against the validation suite's documented gap descriptions first,
before assuming a new bug.
**Design decision already in place**: this is exactly why the 24-repo
validation suite exists — to catch precisely this class of precision gap
against a realistic, polyglot fixture set, and to document it as the
honest baseline rather than a silently-passing inflated expectation.
**Recovery**: not a live fix (code is frozen for this hackathon) —
explain the root cause precisely and point to the roadmap (closing gap 4
closes both the Dependency Query symptom and the Validation 7 Parity
symptom, since they share one root cause).
**Trade-off**: none hidden — this is presented as exactly what it is, a
known limitation with a clear, already-diagnosed fix path.

## General panel technique (use for any scenario not listed above)

1. State what category of failure this is (data-loss vs. availability vs.
   correctness vs. trust).
2. Name the specific architectural mechanism already in place that
   bounds the damage (append-only history / graceful degradation /
   confidence gating / human-in-the-loop).
3. Give the concrete recovery step.
4. Name the honest trade-off or limitation — this is the move that
   separates a rehearsed answer from a genuinely understood one.
