# Section 15 — Demo Coaching

Grounded in `demo/DEMO_GUIDE.md` and `demo/scenarios/*.md` — the local,
GitHub-free demo environment (four real Spring Boot repos: order-service,
payment-service, inventory-service, notification-service).

## Setup, once, before anyone is watching

```bash
scripts/demo-up.sh
cd backend && uv run python scripts/seed_demo.py
```
Open `http://localhost:5173`, sign in as `demo@graphforge.example.com`.
**Confirm `OPENAI_API_KEY` (or configured `AI_PROVIDER`) is set in
`backend/.env` before this** — without it, indexing and deterministic
analysis still work, but AI analysis and the Release Coordination Plan
silently skip, and that's a bad thing to discover live.

## Walkthrough: Scenario 1 — Breaking Kafka schema change

**What to click**: open `order-service`, PR `pr-1`. Show the diff first
(`OrderCreatedEvent.total: BigDecimal` → `totalCents: long`) before
opening any analysis tab — the audience should see the raw change before
the tool's interpretation of it.

**What to say**: "This is a field rename *and* a type change, on an event
two other services deserialize, with nothing at compile time catching the
mismatch. Topic name (`order.created`) is unchanged, so this is invisible
to a 'did the topic change' check — it has to be graph-plus-payload-aware."

**What to emphasize**: click through to the deterministic risk badge
(`HIGH`) before the AI tab. Say explicitly: "This HIGH rating is
computed with zero LLM calls — it's a graph rule: the changed file already
produces to a Kafka topic. That's the deterministic floor the AI
narrative sits on top of, not a replacement for it."

**Possible audience interruption**: "Why does it say all four listeners
are impacted, when only `order.created`'s payload changed — isn't
`order.cancelled` unaffected?" **How to answer**: this is a real,
documented nuance, not a bug — say so directly. "Impact analysis here is
file-level, not field-level: `OrderEventPublisher.java` produces to both
topics, so once that file is flagged as changed, everything downstream of
*both* topics it touches is reported, even though only one topic's payload
shape actually changed. That's an honest precision/recall trade — file-
level granularity is what's implemented; field-level is not." Do not
improvise a claim that field-level tracking exists.

**Then show**: the Release Coordination Plan's `deployment_order` — this
is the one scenario with a genuine multi-step order (deploy consumers
tolerant-reader first, producer last). Point out both notified
repositories are marked `blocking`.

**How to recover if the AI tab is empty or errors**: say "the deterministic
graph and risk analysis are independent of the AI provider — that part
just failed or wasn't configured; let's look at what's graph-backed while
that's sorted," and pivot to the Architecture/Graph view. Never let an AI
outage stall the whole demo — the deterministic layer is the actual proof
point anyway.

## Walkthrough: Scenario 2 — Feign client change

**What to click**: `order-service`'s `PaymentClient` (Feign) change.

**What to say, and the honest caveat to state before anyone asks**: "Feign
calls are same-repository edges only in this graph today —
`FeignClient.target_name` is parsed and stored, but never matched against
another tracked repository, so a Feign relationship never produces a
cross-repository edge or impact hop. Only Kafka topic-name equality
crosses repositories right now." Say this proactively, in your own words,
before a technical reviewer catches it — it lands far better as "here's a
known limitation we're upfront about" than as something extracted from you
under questioning. This exact gap is independently confirmed by the
validation suite's Known Gap 2 (§ [09_VALIDATION_FRAMEWORK.md](09_VALIDATION_FRAMEWORK.md)),
so it's safe to cite as a documented, cross-referenced limitation, not a
one-off caveat you're inventing on the spot.

## Walkthrough: Scenario 3 — New Kafka consumer

Use to demonstrate the graph updating on re-index: show `inventory-service`
before and after adding a consumer, show the new `Component`→`CONSUMES_FROM`→
`KafkaTopic` edge appear. Good moment to mention: "re-indexing fully
replaces this repository's graph — there's no diff-based incremental
indexing yet, every run is a full re-parse and rewrite."

## Walkthrough: Scenario 4 — Delete topic, blast radius

Best scenario for showing the deterministic risk classifier's `HIGH` tier
firing on a delete, and for pivoting into the Engineering Intelligence
Impact Analysis agent if you want to show the newer, AI-narrated blast-
radius surface — but **know before you click** that today it will report
`impacted_repositories = [itself]` only, per the same-repository traversal
filter gap (Known Gap 3). If a technical audience is likely to probe
cross-repository impact specifically, either skip this pivot or state the
limitation before demonstrating it, exactly as in Scenario 2's Feign
caveat — proactive disclosure reads as rigor, not weakness.

## General recovery patterns

- **LLM call fails or times out mid-demo**: every agent in this codebase
  degrades to deterministic output rather than raising (§
  [08_AGENTS.md](08_AGENTS.md), Prompt Builder) — say exactly that, and
  show the deterministic facts still rendered correctly.
- **A number looks wrong to someone in the room**: don't guess an
  explanation live. Say "let's check what's actually being counted" and,
  if it's one of the four documented gaps, name it directly — it is far
  better to be caught being right about a known limitation than caught
  improvising a wrong explanation.
- **"Is this real or a demo trick?"**: the honest, strong answer is that
  the demo repositories are real, hand-written, multi-commit Spring Boot
  projects with real `git` history, indexed through the exact same
  pipeline production repositories go through — `LocalGitVersionControlProvider`
  swaps only the transport (local git branches instead of GitHub PRs), not
  the indexing/analysis logic.
