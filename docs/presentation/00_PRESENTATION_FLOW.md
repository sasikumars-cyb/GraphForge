# 00 — Presentation Flow

Total: **13 minutes** talk + **2 minutes** buffer = 15-minute slot. Every
minute is allocated; rehearse against a visible timer. This is a script,
not a suggestion — deviating costs the next presenter their time.

Do **not** duplicate `docs/handbook/`. This file sequences *who says what,
when* — the content itself lives in `02`–`06` of this folder, each owned by
one presenter.

## Roles

| # | Presenter | Owns | Speaks during |
|---|---|---|---|
| 1 | Product/UX | [02_PRODUCT_AND_UX_DEFENSE.md](02_PRODUCT_AND_UX_DEFENSE.md) | Minutes 0–3 |
| 2 | Architecture | [03_ARCHITECTURE_DEFENSE.md](03_ARCHITECTURE_DEFENSE.md) | Minutes 3–6.5 |
| 3 | AI | [04_AI_DEFENSE.md](04_AI_DEFENSE.md) | Minutes 6.5–9.5 |
| 4 | Engineering Excellence | [05_ENGINEERING_EXCELLENCE.md](05_ENGINEERING_EXCELLENCE.md) | Minutes 9.5–11 |
| 5 | Demo | [06_DEMO_GUIDE.md](06_DEMO_GUIDE.md) | Minutes 11–13 |
| All 5 | Q&A | — | Remainder of slot |

## Minute-by-minute script

### 0:00–0:45 — Presenter 1: Hook (the problem)
**Slide 1**: one line — *"'Does this PR break anything?' gets asked of the
same senior engineer ten times a week."* No logo slide, no agenda slide —
open on the problem.
Say: the ChangeGuard origin story in one sentence, then the generalization
to GraphForge. (Script: [02_PRODUCT_AND_UX_DEFENSE.md](02_PRODUCT_AND_UX_DEFENSE.md) § Why this problem exists.)
**Transition line to self**: "So we built a graph that remembers — here's
what's actually in it."

### 0:45–3:00 — Presenter 1 continued: Product/UX/Story
**Slide 2**: Product Pillars table (Unified Knowledge Graph / Multi-Agent
Reasoning / Deterministic Grounding / Continuous SDLC / Developer-Native
UX).
**Slide 3**: one persona (Priya) and her want-statement — humanizes it in
5 seconds.
**Transition to Presenter 2**: "None of that works without a real graph
underneath it — [name] will show you what's actually there."

### 3:00–6:30 — Presenter 2: Architecture
**Slide 4**: the five-stage pipeline diagram (Evidence → Hypothesis →
Validation → Confidence → Knowledge).
**Slide 5**: Neo4j-as-projection / Postgres-as-source-of-truth inversion —
this is the single most defensible, most interesting architectural claim;
give it real time.
**Slide 6**: AWS deployment diagram (ALB → ECS Fargate ×2 → RDS + Neo4j,
Bedrock via IAM Task Role, no static keys).
**Transition to Presenter 3**: "Every one of those graph edges only
exists because it survived independent validation — here's how that
actually works."

### 6:30–9:30 — Presenter 3: AI
**Slide 7**: hallucination-protection layers (fixed vocabulary → no direct
write → CANDIDATE default → evidence-keyword promotion → Verified requires
≥2 independent sources).
**Slide 8**: one sentence each on Validators / Confidence Engine /
Explainability / Learning Engine, framed as "propose → validate →
score → explain → learn," not four separate features.
**Transition to Presenter 4**: "Trusting a system this deterministic
means trusting that we tested it that way too."

### 9:30–11:00 — Presenter 4: Engineering Excellence
**Slide 9**: the 24-repository validation suite, black-box against real
APIs, with its own documented gap list on screen — **show the gaps, don't
hide them**. This is the single highest-credibility moment in the deck;
judges notice teams that self-report limitations.
**Transition to Presenter 5**: "Let's stop talking about it — here it is,
live."

### 11:00–13:00 — Presenter 5: Live Demo
Exact steps, timing, and fallback: [06_DEMO_GUIDE.md](06_DEMO_GUIDE.md).
Budget: 90 seconds live action, 30 seconds narrating the result.
**Closing line** (Presenter 5, 12:45–13:00): "The graph is the product —
every agent you just saw is a feature of it, and the next one we ship adds
to the same graph, not a new silo." (Direct callback to Presenter 1's
opening — close the loop.)

### 13:00–15:00 — Q&A (all 5, floor rules below)

## Floor rules for Q&A

- **Route by topic, not by turn order.** Whoever's `01_TEAM_RESPONSIBILITIES.md`
  row owns the question answers it. Nominate a moderator (Presenter 2 or
  4, since Architecture/Engineering questions are most common) to
  redirect ("that's [name]'s area") if a judge doesn't address anyone by
  name.
- **No presenter answers outside their lane on a first pass.** If your
  backup owner is the only one present for a topic, say so and answer as
  backup, don't guess as a non-owner.
- **Unknown answer protocol**: "That's not something we've measured/built
  yet — here's what we do know: [nearest grounded fact]." Never invent a
  number. See [12_REALITY_CHECK_PRESENTATION.md](12_REALITY_CHECK_PRESENTATION.md).
- **90-second cap per answer** unless the judge explicitly asks for more —
  practice cutting yourself off at the short answer and stopping.

## Fallback plan if the live demo fails

Rehearsed, not improvised — decide the trigger **before** presenting:

1. **Trigger**: no visible progress within 20 seconds of clicking "Start
   Review" / "Analyze," or a visible error banner.
2. **Presenter 5 says, verbatim**: "Looks like our live environment hit a
   hiccup — let me show you a captured run instead, same data, same
   pipeline." No apology beyond that one line — don't dwell.
3. **Switch immediately** to the pre-recorded screen capture or the
   pre-loaded Run History page showing a completed run with the same
   scenario (`demo/scenarios/01-breaking-kafka-schema.md`) — this must be
   open in a second browser tab, ready, before the talk starts, not
   searched for live.
4. **If the specific failure is a known one** (e.g. Bedrock credential
   expiry, matching the documented note in `graphforge-validation/docs/validation-guide.md`),
   Presenter 4 may add one sentence: "That's actually a known operational
   dependency, not a design flaw — every agent degrades to its
   deterministic output when the LLM call fails, which is what you're
   about to see in the fallback." This turns a visible failure into a
   demonstration of the graceful-degradation design.
5. **Never debug live in front of judges.** If the fallback also fails,
   move straight to Q&A — "we'll follow up with a working recording" is
   an acceptable, honest close; frantic troubleshooting on stage is not.

## Slide count discipline

Target **9 slides total** for the 13-minute talk (listed above) — roughly
90 seconds of speaking per slide. Do not add slides to "fit more in";
cut content to fit the slide, not the reverse.
