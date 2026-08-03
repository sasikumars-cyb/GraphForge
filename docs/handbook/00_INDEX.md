# The GraphForge Architecture Defense Handbook

**Purpose**: a single, code-grounded reference for defending GraphForge in
architecture reviews, hackathon judging, executive Q&A, and technical demos.

**Method**: every claim in this handbook traces to one of: an ADR under
`docs/adr/`, RFC-001 (`docs/rfcs/RFC-001.md`) or the RFC roadmap embedded in
ADR 0018, the four `docs/graphforge/*.md` design documents (Architecture,
Product Vision, Agent Framework, Roadmap), the validation guide
(`graphforge-validation/docs/validation-guide.md`), or actual source under
`backend/app/`. Where GraphForge's own documents mark something "proposed,"
"not yet implemented," or "deferred," this handbook repeats that status —
it does not upgrade a plan into a claim of working software.

**Read this first**: [16_REALITY_CHECK.md](16_REALITY_CHECK.md). It is the
single most load-bearing section — implemented vs. partially-implemented
vs. deferred vs. roadmap, with nothing softened.

## Contents

| # | Section | File |
|---|---|---|
| 1 | Executive Summary (30s / 2m / 5m / 10m, six audiences) | [01_EXECUTIVE_SUMMARY.md](01_EXECUTIVE_SUMMARY.md) |
| 2 | The Story — why GraphForge exists | [02_STORY.md](02_STORY.md) |
| 3 | Complete Architecture | [03_ARCHITECTURE.md](03_ARCHITECTURE.md) |
| 4 | Engineering Memory | [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md) |
| 5 | Knowledge Engine (Evidence → Hypothesis → Validation → Confidence → Knowledge) | [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md) |
| 6 | Frontier AI (LLM hypothesis generation) | [06_FRONTIER_AI.md](06_FRONTIER_AI.md) |
| 7 | Engineering Intelligence Service Layer | [07_ENGINEERING_INTELLIGENCE.md](07_ENGINEERING_INTELLIGENCE.md) |
| 8 | Agents | [08_AGENTS.md](08_AGENTS.md) |
| 9 | Validation Framework | [09_VALIDATION_FRAMEWORK.md](09_VALIDATION_FRAMEWORK.md) |
| 10 | Design Decisions (one per major decision) | [10_DESIGN_DECISIONS.md](10_DESIGN_DECISIONS.md) |
| 11 | Architecture Review Questions | [11_REVIEW_QUESTIONS.md](11_REVIEW_QUESTIONS.md) |
| 12 | Difficult Questions (the "why not X" gauntlet) | [12_DIFFICULT_QUESTIONS.md](12_DIFFICULT_QUESTIONS.md) |
| 13 | Whiteboard Exercises | [13_WHITEBOARD.md](13_WHITEBOARD.md) |
| 14 | Presentation Coaching (panel drill — run live in chat) | [14_PRESENTATION_COACHING.md](14_PRESENTATION_COACHING.md) |
| 15 | Demo Coaching | [15_DEMO_COACHING.md](15_DEMO_COACHING.md) |
| 16 | **Reality Check** | [16_REALITY_CHECK.md](16_REALITY_CHECK.md) |

## A note on names, because they matter in review

GraphForge is the evolution of an earlier product called **ChangeGuard**.
Some code, comments, and ADRs (0001–0013) still say ChangeGuard or describe
a single-agent PR-review tool — that history is real and is the reason
things like `IVersionControlProvider`, the deterministic risk/impact engine,
and the Change Investigation Agent exist and are reused, not rebuilt.
`docs/graphforge/ARCHITECTURE.md` is explicit that GraphForge "does not
propose a rewrite" of ChangeGuard.

Two systems inside the codebase are easy to conflate and are not the same:

- **`app.knowledge`** — the external-source connection registry (GitHub,
  Jira, Confluence transport + auth catalog).
- **`app.knowledge_engine`** — the Evidence → Hypothesis → Validation →
  Confidence → Knowledge pipeline (ADR 0018). Named differently on purpose
  (ADR 0018's own package-structure note) so nobody conflates them.

Also distinct: **`app.context`** (Entry Resolvers + Context Assembler, the
`ARCHITECTURE.md`-level concept, Phase 1+ of the roadmap) vs.
**`app.context_pipeline`** (the actual, implemented Context Discovery
reasoning engine — ledger, capability system, curation, engineering
understanding — ADRs 0014–0017). Where this handbook says "Context
Discovery," it means the latter, implemented system.
