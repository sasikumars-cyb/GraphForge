# 06 — Demo Guide (Presenter 5)

Budget: 90 seconds live action, 30 seconds narrating the result (see
`00_PRESENTATION_FLOW.md`). This file is the operational script; deeper
per-scenario detail already exists in `docs/handbook/15_DEMO_COACHING.md`
— read that once, then rehearse from this shorter version.

## Setup (before anyone is watching)

```bash
scripts/demo-up.sh
cd backend && uv run python scripts/seed_demo.py
```
Sign in as `demo@graphforge.example.com`. **Confirm the AI provider
credentials are live** before walking on stage — this environment has a
known, documented dependency on real Bedrock/OpenAI credentials, and
expired AWS STS tokens are a real, previously-observed failure mode here
(see Known Limitations below). Check this at least 15 minutes before
presenting, not "right before."

## Exact demo flow

**Repositories used**: `order-service`, `payment-service`,
`inventory-service`, `notification-service` — four real, hand-written,
multi-commit Spring Boot repos with real git history, indexed through the
exact same pipeline production repositories use
(`LocalGitVersionControlProvider` swaps only the transport, not the
logic).

### Primary scenario: Scenario 1 — Breaking Kafka schema change

1. Open `order-service`, PR `pr-1`. **Show the raw diff first** —
   `OrderCreatedEvent.total` (`BigDecimal`) renamed to `totalCents`
   (`long`) — before opening any analysis. The audience needs to see the
   real change before the tool's interpretation of it.
2. Click into the deterministic risk badge — **`HIGH`** — before the AI
   tab. Say: "Zero LLM calls produced this rating — it's a graph rule:
   the changed file already produces to a Kafka topic."
3. Open the AI analysis tab. **Expected output**: executive summary
   naming the field rename/type change explicitly; one breaking-change
   entry for `OrderEventPublisher`/`order.created`, severity high;
   migration advice to update both consumers before/alongside deploy.
4. Show the Release Coordination Plan's `deployment_order` — this is the
   one scenario with a genuine multi-step order (tolerant-reader
   consumers deploy first, producer last). Point out both notified repos
   are marked `blocking`.

**Expected outputs to have memorized**: risk=`HIGH`; directly impacted =
`OrderEventPublisher`; indirectly impacted = all four listeners across
both topics (`order.created` AND `order.cancelled` — **this is real and
correct, not a bug**, because impact analysis is file-level, not
field/topic-level, and that file touches both topics).

### Secondary scenario, if time allows: Repository Understanding / Dependency Query / Impact Analysis agents

Show one AI Workspace agent live (`analyze_repository_understanding` on
`order-service` is the cheapest/fastest — `cost_class="cheap"`). Narrate:
"This is computed entirely by a deterministic service layer that never
calls an LLM — the agent only narrates it."

## Recovery steps (mid-demo, minor hiccup)

- **A number looks off**: don't improvise an explanation. Say "let's
  check what's actually being counted" — if it's one of the four
  documented gaps below, name it directly.
- **AI tab is slow or empty**: say "the deterministic graph and risk
  analysis are independent of the AI provider — let's look at what's
  graph-backed while that resolves," and pivot to the Architecture/Graph
  view. Never let an AI outage stall the whole demo.
- **A run appears stuck ("Queued")**: this can occur if the demo backend
  process restarted at the wrong moment (a durability gap we know about
  and document — see `05_ENGINEERING_EXCELLENCE.md`). Recovery: refresh
  Run History — if it shows `failed` with a clear message, say "that's a
  known operational limitation, not a logic bug — let me re-run it," and
  do. Do not debug live.

## Fallback demo (full failure)

Full protocol: `00_PRESENTATION_FLOW.md` § Fallback plan. Summary: switch
immediately to a second browser tab with a pre-completed Run History
entry for the same scenario, or a screen recording. One sentence, no
apology beyond it, keep moving.

## Validation suite walkthrough (if a judge asks "prove it")

Show `graphforge-validation/docs/validation-guide.md` directly, or
narrate its structure: 24 repositories, 10 validations, black-box against
GraphForge's real API — "does not reimplement any GraphForge logic."
Exit code `0` iff every gating validation passes; designed as a CI
acceptance gate. If you have a recent `reports/latest.html`, have it
bookmarked and ready — don't generate it live (indexing + agent runs take
real time and real LLM cost).

## Known limitations — state these proactively, before a judge finds them

Four documented, numbered, root-caused gaps from the validation suite —
memorize the one-line version of each:

1. **Kafka topic detection**: literal-string-only (`@KafkaListener(topics
   = "literal")`), no shared-SDK-wrapper support, no Python extractor at
   all yet.
2. **Feign cross-repo name matching**: suffix-only normalization can't
   bridge a `<domain>-service-<language>` naming convention — 0
   cross-repo `CALLS_SERVICE` edges for a completely realistic polyglot
   naming scheme.
3. **Impact Analysis can't cross repositories today** — a traversal
   filter bug (both edge endpoints filtered to the same `repository_id`),
   not a missing feature. Blast radius is always `[itself]` regardless of
   real cross-repo edges in Neo4j.
4. **Dependency Query's counts are intra-repository noise today** — same
   root cause as the Validation 7 (Parity) failures on affected repos;
   closing one closes both.

If a judge asks about any of these mid-demo, the strongest answer is:
"Yes — that's gap #[N] in our own validation suite's documented findings,
not something we're discovering right now." Confidence in citing your own
known gaps reads better than pretending they don't exist.

## Roadmap / future work (closing line if time remains)

RFC-07 (first language with no dedicated parser), RFC-08 (multi-provider
LLM consensus, with a built-in anti-correlation cap already designed),
RFC-09 (incremental evidence ingestion, infra manifests first, runtime
telemetry last). Frame as: "every one of these plugs into the same
five-stage pipeline you just saw — no rearchitecture required."

## Judge questions after the demo — quick answers

**Q: Is this the full product or a cut-down demo build?**
A: Same code path as production — the demo swaps only the git transport
(local branches instead of GitHub PRs) via `LocalGitVersionControlProvider`;
indexing, analysis, and the graph are unmodified.

**Q: What would break if we pointed this at our real repos right now?**
A: Honestly — likely the four known gaps above, plus indexing scale past
"a handful of repos." Say this before being asked twice.

**Q: How long did indexing take?**
A: Deterministic parsing is fast (tree-sitter, in-process, no AI cost);
the AI analysis step is what costs real time and LLM spend — point to
`seed_demo.py` skipping AI gracefully when no key is configured, proving
the deterministic layer has zero external dependency.

**Q: Can I try this on my own repo right now?**
A: Answer honestly based on actual event constraints — don't promise a
live index of an unknown, potentially large repository during Q&A unless
you're prepared for the time and failure modes that entails.
