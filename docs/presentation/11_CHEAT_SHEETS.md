# 11 — Cheat Sheets

One page per presenter. Only what you must have cold, no notes. Print or
keep open on a second screen during Q&A.

---

## Presenter 1 — Product / UX

- **30-sec pitch**: GraphForge indexes repos into a knowledge graph, then
  only promotes a relationship to "known" once independent, deterministic
  evidence confirms it. Deterministic core, LLM proposes/narrates, never
  asserts alone.
- **Origin**: ChangeGuard (deterministic PR-impact tool) → generalized
  across the SDLC. Evolution, not a rewrite.
- **Core principle to quote**: "The graph is the product. The agents are
  features of the graph."
- **vs. Copilot**: different job — in-editor completion vs. cross-repo,
  persistent, evidence-graded reasoning.
- **vs. GraphRAG**: we gate LLM-extracted relationships behind
  independent, deterministic validation before they're trusted; most
  GraphRAG patterns write LLM extractions directly.
- **UX rule to know**: confidence is always a percentage next to the
  claim, never a bare adjective; every agent claim links to its evidence.
- **If stuck**: "Not measured yet — here's what we prioritized instead:
  [evidence-linking rule]."

---

## Presenter 2 — Architecture

- **Pipeline (memorize the arrow)**: Evidence → Hypothesis → Validation →
  Confidence → Knowledge.
- **The one inversion to nail**: Postgres (Engineering Memory) is the
  append-only source of truth; Neo4j is a synced, rebuildable projection
  — proven by a real replay test, not yet the live write path (say both
  halves).
- **AWS**: ECS Fargate ×2, RDS Multi-AZ, Neo4j (Aura/EC2), Bedrock via IAM
  Task Role, zero static keys. `desiredCount=1` on backend — name the
  real reason (background-execution durability) if asked.
- **Why not X**: EKS (2 services, no CRD need), Lambda (long-running,
  stateful mid-flight calls), App Runner (no VPC-native DB access).
- **Scaling ceiling to admit**: full-clone-per-index doesn't scale past a
  handful of repos yet.
- **If stuck**: "The design intent is documented; I haven't independently
  re-verified every code path for that."

---

## Presenter 3 — AI

- **The rule, verbatim**: a `HypothesisGenerator` never writes to the
  graph; a `KnowledgeValidator` never calls an LLM; `generator_confidence`
  never influences validation or confidence aggregation.
- **Six confidence states**: verified, highly_likely, likely, candidate,
  rejected, conflicting — computed only from independent validator
  confirmations, never self-reported LLM confidence.
- **Hallucination defense, six layers**: fixed vocabulary → no direct
  write → CANDIDATE default → evidence-keyword promotion from cited
  evidence only → Verified needs ≥2 independent sources → materializer
  never surfaces unpromoted candidates.
- **Why Bedrock**: multi-provider registry (Bedrock/OpenAI/Gemini/Groq),
  zero static AWS keys via IAM Task Role.
- **Honest gap**: Frontier LLM generator's real-world precision/recall is
  unmeasured — shipped gated off by default, on purpose.
- **If stuck**: "That's not measured — here's the mechanism that bounds
  the risk instead: [six-layer answer]."

---

## Presenter 4 — Engineering Excellence

- **Testing rule**: real Postgres/Neo4j in integration tests, no mocked
  DB; mock only the external HTTP boundary.
- **Validation suite**: 24 repos, 10 validations, black-box against real
  APIs, publishes its own known gaps — cite this proactively as a
  credibility signal.
- **Shadow mode**: every RFC ships alongside the live pipeline first,
  proven correct, before any cutover — one-line rollback always available.
- **The honest reliability gap to own**: in-process `asyncio.Task`
  background execution doesn't survive a process restart; recovery only
  happens at next startup. Real historical evidence exists of this in our
  own dev environment. Backend fixed at `desiredCount=1` in AWS because
  of exactly this.
- **CI/CD**: CI is real (lint/test/build on every push). CD is a spec,
  not implemented — say this precisely.
- **If stuck**: "That's a known, documented limitation — here's the
  mitigation already in place: [orphan recovery / CloudWatch alarm]."

---

## Presenter 5 — Demo

- **Scenario**: order-service PR #1 — `OrderCreatedEvent.total`
  (BigDecimal) → `totalCents` (long). Risk = `HIGH`, zero LLM calls needed
  for that rating.
- **Expected impacted set**: all four listeners across both Kafka topics
  — correct because impact analysis is file-level, not field-level (the
  producer file touches both topics).
- **Four known gaps, one-liners**:
  1. Kafka: literal-string topics only, no shared-SDK support.
  2. Feign: suffix-only name matching breaks on
     `<domain>-service-<language>`.
  3. Impact Analysis can't cross repositories (traversal filter bug).
  4. Dependency Query counts are intra-repo noise (same root cause as #7
     Parity failures).
- **Fallback trigger**: no progress in 20s or a visible error → switch to
  the pre-loaded second tab immediately, one sentence, no apology beyond
  it.
- **If stuck**: "That's gap #[N] in our own validation suite's documented
  findings" (for anything matching the four gaps above), or "not measured
  yet" otherwise.

---

## All presenters — the three universal fallback lines

1. "Not measured yet — here's what we do know: [nearest grounded fact]."
2. "That's roadmap, not built — [RFC/phase name] scopes it."
3. "That's [presenter]'s area — [name], can you take this one?"
