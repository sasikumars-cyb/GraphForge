# 12 — Reality Check (Presentation Version)

Read this the night before. Full detail lives in
`docs/handbook/16_REALITY_CHECK.md` — this is the compressed,
stage-ready version: what to say when asked "is that actually built."

## The one rule for the whole team

**Never let a judge catch a gap you didn't mention first.** Every gap
below is already documented in our own validation suite or ADRs — citing
it yourself is a credibility signal, not an admission of weakness.

## Implemented (say this with full confidence, no hedging)

- Deterministic Java/Spring Boot + Python indexing (tree-sitter) → Neo4j.
- The full five-stage Knowledge Engine pipeline (Evidence → Hypothesis →
  Validation → Confidence → Knowledge) — real code, real tests, dated
  "Implemented" status per RFC.
- Engineering Memory — append-only Postgres log, real migrations, real
  tests.
- `DefaultConfidenceEngine` — deterministic, incremental, monotonic,
  parity-tested against the pre-existing hand-assigned labels it
  generalizes.
- Cross-repository relationship persistence into Engineering Memory.
- The Materializer — replay-tested, proven to rebuild Neo4j from Postgres
  alone.
- The Frontier LLM Hypothesis Generator — real, shipped, gated off by
  default.
- Evidence-keyword validators, confidence explainability, the Learning/
  feedback engine with real REST endpoints.
- Context Discovery — deterministic investigation loop, evidence
  curation, engineering-understanding synthesis with graceful degradation.
- The Engineering Session aggregate (RFC-001) — Beliefs, Hypotheses,
  Evidence, Recommendations, Decisions, Contradictions, full API, 68
  tests.
- The Orchestrator — registry, selector, run coordinator, preflight,
  background execution — genuinely implemented, not a proposal (a real
  doc-drift we found and corrected: `ARCHITECTURE.md` undersells this as
  "new").
- 12+ registered agents behind manifests.
- The Engineering Intelligence Service Layer — 6 LLM-free, deterministic
  services.
- The 24-repository external validation suite, 10 validations, black-box
  against real APIs.

## Partially implemented — the phrase to use: "proven, not yet the live path" / "built, gated off"

- **Neo4j as derived projection**: the materializer proves this works;
  `replace_repository_graph` still writes Neo4j directly in production
  today. The inversion is *proven possible*, not yet *how it normally
  happens*.
- **Cross-repository knowledge**: real, but Feign name-matching and Kafka
  topic detection both have documented, numbered precision gaps for
  realistic naming/abstraction patterns.
- **Impact Analysis**: works within one repository; cannot cross
  repositories today (a traversal-filter bug, not a missing feature).
- **Dependency Query**: confidence-aware search works; "direct
  dependencies"/"downstream consumers" counts are not meaningful yet
  (same root cause as the Impact Analysis gap, one layer over).
- **Frontier LLM Generator**: mechanism fully proven end-to-end; real-
  world precision/recall has never been measured — it's off by default
  specifically because that evaluation hasn't happened.
- **Confidence calibration**: the Learning Engine captures the raw
  feedback data this needs; calibration itself is not built.
- **Background execution durability**: real, working, single-process
  execution with startup-time orphan recovery; does not survive a process
  restart mid-run, and recovery can be arbitrarily delayed until the next
  restart. This is a genuinely observed, real limitation (not
  hypothetical) — we have historical evidence of it in our own dev
  environment.

## Deferred / roadmap (say "not built, explicitly scoped for later" — never imply it exists)

- RFC-07 (first non-parser language), RFC-08 (multi-provider LLM
  consensus), RFC-09 (incremental evidence ingestion) — all roadmap only.
- Belief promotion into an org-wide System Model, Mission/Organization
  aggregates, Policy as a first-class concept (RFC-001's own stated
  scope boundaries).
- `RuntimeValidator`/`OwnershipValidator`/`ApiContractValidator` —
  deliberately not built; no evidence source exists yet to validate
  against.
- LLM-based Selector, natural-language Goal inference — Phase 3 roadmap.
- A real distributed task queue for indexing/background execution.
- Frontend rendering of the tiered Evidence Package / Engineering
  Understanding — backend-complete, no UI yet.
- The AWS CD pipeline — specified in `docs/deployment/`, not implemented
  as an actual GitHub Actions workflow.
- Application-level rate limiting; distributed tracing (X-Ray/OTel).

## Known limitations (the four to have memorized cold, team-wide)

1. Kafka topic detection — literal-string-only, no shared-SDK support, no
   Python extractor.
2. Feign cross-repo name matching — suffix-only, breaks on
   `<domain>-service-<language>` naming.
3. Impact Analysis structurally cannot leave the seed repository.
4. Dependency Query's direct/downstream counts are intra-repository noise
   (shared root cause with #7 Parity failures).

## Known technical debt

- `GET .../ai-analysis` doesn't expose `release_coordination_plan`.
- Full-clone-per-index doesn't scale past a handful of repos per org.
- `Recommendation.target_contradiction_id` has no FK (documented,
  intentional — would create a circular table dependency).
- Background-execution durability gap (see above) — explicitly named in
  our own AWS deployment docs as the reason backend `desiredCount` is
  fixed at 1.

## Questions to answer honestly, with the exact honest phrasing

**"Is this in production?"** → "No — this is a hackathon build. The AWS
deployment blueprint (`docs/deployment/`) is specified and code-verified
against our actual settings/IAM/config code, but not deployed."

**"Have you measured [X]?"** → If not measured: "Not measured — here's
what we did verify instead: [nearest real, grounded fact]." Never invent
a number.

**"Does the LLM generator actually work well?"** → "The mechanism is
proven end-to-end — generation, shadow persistence, validator-driven
promotion from CANDIDATE to LIKELY. Real-world precision hasn't been
evaluated yet; that's explicitly the next step, not something we're
hiding."

**"Why isn't the materializer live yet?"** → "Shadow-mode discipline —
every RFC in our roadmap proves correctness in isolation before betting
production writes on it. It's tested and correct; the cutover itself
just hasn't been scheduled."

**"What's the biggest thing you'd fix with one more week?"** → Give a
real, specific answer, not a vague one: either the Feign/Kafka
cross-repo matching gaps (highest product-visible impact) or the
background-execution durability gap (highest operational-risk impact).
Pick one and justify it — that's a stronger answer than naming both
vaguely.
