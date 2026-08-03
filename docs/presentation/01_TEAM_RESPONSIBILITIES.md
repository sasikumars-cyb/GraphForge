# 01 — Team Responsibilities Matrix

Every topic a judge might raise, mapped to a primary owner, a backup, a
difficulty rating (how hard it is to defend well), and the exact files to
re-read before presenting. Difficulty: 🟢 straightforward · 🟡 needs real
prep · 🔴 genuinely hard, rehearse out loud.

## Presenter 1 — Product / UX / Vision

| Topic | Primary | Backup | Difficulty | Files |
|---|---|---|---|---|
| Problem statement / why GraphForge exists | P1 | P2 | 🟢 | `docs/graphforge/PRODUCT_VISION.md`, `docs/handbook/02_STORY.md` |
| Personas / target users | P1 | P5 | 🟢 | `PRODUCT_VISION.md` § User Personas |
| Competitive positioning (vs. Copilot/GraphRAG/chatbots) | P1 | P3 | 🔴 | `PRODUCT_VISION.md` § Competitive Positioning, `docs/handbook/12_DIFFICULT_QUESTIONS.md` |
| UX/UI design system | P1 | P2 | 🟡 | `docs/graphforge/UI_GUIDELINES.md` |
| Customer journey / demo narrative framing | P1 | P5 | 🟡 | `docs/presentation/02_PRODUCT_AND_UX_DEFENSE.md` |
| Business value / ROI framing | P1 | P4 | 🟡 | `PRODUCT_VISION.md` § Definition of Success |
| Out-of-scope / non-goals | P1 | P2 | 🟢 | `PRODUCT_VISION.md` § Out of Scope |

## Presenter 2 — Architecture

| Topic | Primary | Backup | Difficulty | Files |
|---|---|---|---|---|
| Overall system architecture | P2 | P3 | 🟡 | `docs/graphforge/ARCHITECTURE.md`, `docs/handbook/03_ARCHITECTURE.md` |
| Engineering Memory (append-only, Postgres) | P2 | P3 | 🔴 | ADR 0018 (RFC-04), `docs/handbook/04_ENGINEERING_MEMORY.md`, `app/repositories/engineering_memory_repository.py` |
| Knowledge Engine pipeline | P2 | P3 | 🔴 | ADR 0018 (full), `docs/handbook/05_KNOWLEDGE_ENGINE.md`, `app/knowledge_engine/` |
| Neo4j role / why not source of truth | P2 | P3 | 🔴 | ADR 0018, `docs/handbook/12_DIFFICULT_QUESTIONS.md`, `app/graph/interfaces.py` |
| Materializer (RFC-05B) | P2 | P4 | 🔴 | ADR 0018 RFC-05B, `app/knowledge_engine/materializer.py`, `tests/integration/test_materializer_replay.py` |
| Cross-repository reasoning | P2 | P5 | 🟡 | ADR 0018 RFC-05, `docs/handbook/09_VALIDATION_FRAMEWORK.md` (Known Gaps 1–4) |
| AWS deployment architecture | P2 | P4 | 🟡 | `docs/deployment/01_ARCHITECTURE.md`, `02_INFRASTRUCTURE.md` |
| Scalability | P2 | P4 | 🟡 | `docs/handbook/12_DIFFICULT_QUESTIONS.md` § How does it scale, `docs/deployment/12_OPERATIONS.md` § Scaling |
| Security architecture | P2 | P4 | 🟡 | `docs/deployment/04_SECURITY.md`, `05_IAM.md` |
| Orchestrator / Agent Framework | P2 | P3 | 🟡 | `docs/graphforge/AGENT_FRAMEWORK.md`, `app/orchestrator/` |

## Presenter 3 — AI

| Topic | Primary | Backup | Difficulty | Files |
|---|---|---|---|---|
| Why deterministic-first | P3 | P2 | 🔴 | `PRODUCT_VISION.md` Core Principle 3, ADR 0007, ADR 0018 |
| Frontier LLM Hypothesis Generator | P3 | P2 | 🔴 | ADR 0018 RFC-06/06B, `app/indexer/hypotheses/llm_generator.py` |
| Validators (deterministic-only, never LLM) | P3 | P2 | 🔴 | ADR 0018, `app/knowledge_engine/validators/` |
| Confidence Engine (6-state, monotonic, incremental) | P3 | P2 | 🔴 | ADR 0018 RFC-03, `app/knowledge_engine/confidence/default_engine.py` |
| Explainability | P3 | P2 | 🟡 | ADR 0018 RFC-06C, `app/knowledge_engine/explainability.py` |
| Learning/Feedback Engine | P3 | P4 | 🟡 | ADR 0018 RFC-06D, `app/learning_engine/` |
| Bedrock / multi-provider | P3 | P4 | 🟡 | `docs/deployment/13_AI_PROVIDER_CONFIGURATION.md`, `app/ai/providers/` |
| Hallucination mitigation (the whole story) | P3 | P1 | 🔴 | `docs/handbook/06_FRONTIER_AI.md` |
| Context Discovery / Engineering Understanding | P3 | P2 | 🔴 | ADR 0014–0017, `app/context_pipeline/` |
| Comparison vs. Copilot/Cursor/GraphRAG | P3 | P1 | 🔴 | `docs/handbook/12_DIFFICULT_QUESTIONS.md` |

## Presenter 4 — Engineering Excellence

| Topic | Primary | Backup | Difficulty | Files |
|---|---|---|---|---|
| Testing discipline (real DB, no mocks) | P4 | P2 | 🟢 | `docs/graphforge/ROADMAP.md` § Testing Strategy, ADR 0007 § Verification |
| 24-repo validation suite | P4 | P5 | 🟡 | `graphforge-validation/docs/validation-guide.md` |
| Shadow mode delivery pattern | P4 | P2 | 🟡 | ADR 0018 (every RFC's rollout), `docs/handbook/10_DESIGN_DECISIONS.md` § Why shadow mode |
| Parity Engine / Graph Parity dashboard | P4 | P2 | 🟡 | ADR 0018 RFC-05B, `app/knowledge_engine/parity/` |
| CI/CD | P4 | P2 | 🟡 | `docs/deployment/07_CICD.md`, `.github/workflows/ci.yml` |
| AWS operations (CloudWatch, IAM, Secrets Manager) | P4 | P2 | 🔴 | `docs/deployment/05_IAM.md`, `06_SECRETS.md`, `12_OPERATIONS.md` |
| Reliability / failure handling | P4 | P2 | 🔴 | `app/orchestrator/run_coordinator.py`, `background_execution.py` |
| Known operational gap: background-execution durability | P4 | P2 | 🔴 | `docs/deployment/09_DEPLOYMENT_RUNBOOK.md` incident table, `12_OPERATIONS.md` (recovered_orphaned_runs alarm) |
| Rollback strategy | P4 | P2 | 🟡 | `docs/deployment/09_DEPLOYMENT_RUNBOOK.md` § Rollback |

## Presenter 5 — Demo

| Topic | Primary | Backup | Difficulty | Files |
|---|---|---|---|---|
| Live demo execution | P5 | P4 | 🟡 | `docs/presentation/06_DEMO_GUIDE.md`, `demo/DEMO_GUIDE.md`, `demo/scenarios/` |
| Repository Understanding Agent | P5 | P3 | 🟢 | `app/agents/repository_understanding/` |
| Dependency Query Agent | P5 | P3 | 🟡 | `app/agents/dependency_query/`, known gap 4 |
| Impact Analysis Agent | P5 | P3 | 🟡 | `app/agents/impact_analysis/`, known gap 3 |
| Known limitations (must state proactively) | P5 | P4 | 🔴 | `graphforge-validation/docs/validation-guide.md` § Known Gaps |
| Roadmap / future work | P5 | P1 | 🟢 | `docs/graphforge/ROADMAP.md`, ADR 0018 RFC-07/08/09 |

## Cross-cutting ownership (everyone should be able to give the 30-second version)

| Topic | Everyone's 30-second line |
|---|---|
| "What is GraphForge in one sentence?" | See `docs/handbook/01_EXECUTIVE_SUMMARY.md` § 30 seconds |
| "Is this real or a demo trick?" | Real indexed repositories, real pipeline, same code path as production — see `docs/presentation/06_DEMO_GUIDE.md` |
| "What's not built yet?" | See `docs/presentation/12_REALITY_CHECK_PRESENTATION.md` |

## Rehearsal checklist (per presenter)

- [ ] Can state your 3 hardest questions' short answers from memory, no notes
- [ ] Have read every 🔴 row's cited file at least once this week
- [ ] Know your backup's topics well enough to cover a 60-second gap if they're mid-answer elsewhere
- [ ] Timed your section of `00_PRESENTATION_FLOW.md` at least twice
