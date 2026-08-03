# 05 — Engineering Excellence Defense (Presenter 4)

## Testing strategy

Real Postgres/Neo4j in integration tests, no mocked DB; mock only the
exact external HTTP boundary (`httpx.MockTransport` convention). Unit
tests for pure logic (Selector rules, validators, prompt builders) with no
I/O. Stated discipline (`ROADMAP.md` § Testing Strategy), verified in this
audit against real test file counts: 236 backend test files; RFC-001 alone
added 68 tests across schema/service/repository/API layers.

## Validation framework — your strongest engineering-excellence proof point

24-repository validation suite (`graphforge-validation/`), **outside** the
backend, black-box against real APIs — "does not reimplement any
GraphForge logic... every fact it asserts on comes from GraphForge's own
REST API or Engineering Memory service, called the same way the app's own
routers do." 10 validations, exit code wired for CI gating. **Show the
Known Gaps section of the validation guide on screen if you can** — a
team that documents its own precision gaps with root causes, not vague
caveats, reads as senior engineering, not as weakness.

## Regression suite mechanics worth naming precisely

- Deterministic fields: exact match. Narrative (LLM) fields: keyword
  match — explicitly justified as avoiding flaky assertions on wording
  that varies between equally-correct LLM outputs.
- Fixture discipline: "do not fix a FAIL by editing the fixture repos
  themselves... a FAIL means either GraphForge changed, or fixtures are
  stale" — never "go edit the repos to match what the fixture expected."

## Shadow Mode — the delivery discipline behind every RFC

Every RFC in ADR 0018's roadmap ships **alongside** the live pipeline
first, writing to nothing production-visible, before any cutover — proven
via tests (e.g. RFC-02B: byte-for-byte identical `GraphPayload` whether
shadow generation runs or not), with a one-or-two-line rollback. This is
why the Frontier LLM generator can be fully built, tested, and merged
while contributing zero production behavior until explicitly enabled.

## Materializer / Parity — proof, not assertion

Materializer: pure projection, replay-tested (delete Neo4j, rebuild from
Postgres alone, diff node/edge/property equality). Parity Engine
(`app.knowledge_engine.parity.comparator`): pure, deterministic,
multiset-based edge comparison — powers both Validation 7 in the
regression suite and the live Graph Parity dashboard in the frontend.
Say: "we don't just claim the graph is rebuildable from history — we ship
a dashboard that proves it, continuously."

## Performance

- Bounded traversal (`get_neighborhood`, hop-limited) replaced an
  unbounded `get_full_graph` read — a real, measured fix (ADR 0014):
  "previously O(every indexed repo); now O(1)" once a repository is known.
- Validator execution is concurrent (`asyncio.gather`) yet provably
  deterministic — results reassembled in selection order, never
  completion order.
- Context Discovery's LLM cost is capped by a *measured* benchmark, not a
  guess (`MAX_MID_LOOP_SYNTHESIS_CALLS = 1`, chosen after observing a
  92s→165s test-suite runtime increase at a budget of 2).

## Scalability — the honest ceiling (don't let Presenter 2 be the only one who knows this)

Indexing doesn't scale past a handful of repos per org today
(full-clone-per-index, no incremental re-indexing — named directly in
`ROADMAP.md` Technical Debt). Backend is fixed at `desiredCount=1` in AWS
until background-execution durability is redesigned. State both
proactively — this is Engineering Excellence's job to own, not hide.

## Reliability / failure handling

- `RunCoordinator` never swallows an error — a failed agent run persists
  `status="failed"` with the real error message, always, both for the
  agent's own exceptions and for a pre-flight dependency check
  (ADR 0011) that fails before the LLM is even called.
- A `HypothesisGenerator`'s or `KnowledgeValidator`'s failure is isolated
  — logged and swallowed for that one generator/validator only, never
  blocking or corrupting another's output for the same run (matches the
  pre-existing `run_indexing`/`relink_account` isolation pattern).
- **The one real, documented reliability gap, own it directly**: background
  agent execution runs on in-process `asyncio.Task`, which does not
  survive a process restart. `recover_orphaned_runs()` sweeps any run left
  `queued`/`running` to `failed` — but only at the *next* process startup,
  which can be an arbitrary delay later. This is why the AWS blueprint
  fixes `desiredCount=1` and adds a CloudWatch alarm on the
  `recovered_orphaned_runs` log line specifically — "turns a silent
  data-loss event into a paged, trackable incident" (`docs/deployment/12_OPERATIONS.md`).
  If asked "have you seen this happen," the honest answer is yes — real
  historical rows in our own dev database show exactly this pattern
  during active local development with `uvicorn --reload`.

## Observability

- Structured `loguru` logs with mandatory `run_id`/`agent_id`/`subject_id`
  on every agent-related line.
- CloudWatch: `awslogs` driver, zero code change; Container Insights for
  CPU/memory/task-count; a metric filter alarm specifically on
  `recovered_orphaned_runs` (see above) and on `ERROR`-level log lines.
- **Not yet present, say so if asked**: distributed tracing (X-Ray/OTel)
  — explicitly deferred until the durable-queue redesign lands, since
  before that a request's causal chain is "one process, one call stack"
  and doesn't yet need it.

## CI/CD

`.github/workflows/ci.yml` — real, running lint/test/build on every push/PR
to `master` (the actual default branch — not `main`). The CD extension
(build → ECR push → task definition → migration task → service update →
health check) is a **specification**, not yet implemented — state this
distinction precisely if asked; don't imply automated production
deploys exist today.

## AWS operations — the pieces to have crisp

- **Secrets**: `Settings` is the only module allowed to read `os.environ`;
  production boot fails loudly (`_reject_insecure_defaults_in_production`)
  if `jwt_secret_key`/`token_encryption_key`/`neo4j_password` still hold
  their public, checked-in dev defaults. "Treat that failure as the
  system working correctly, not a bug to route around."
- **IAM**: zero static AWS keys anywhere; Bedrock via the ECS Task Role,
  scoped to exactly the Converse API operations against exactly the
  configured model ARNs — never a wildcard.
- **Encryption at rest**: RDS encryption must be enabled **at creation**
  — cannot be retrofitted without a snapshot/restore cycle. Say this if
  asked about security posture — it signals you understand a real
  operational constraint, not just a checkbox.
- **Backups**: RDS automated snapshots + point-in-time recovery, both
  standard features, both enabled. Explicit discipline: "an untested
  backup is not a backup" — restore procedure should be tested, not
  assumed.

## Rollback

Every RFC ships with a one-or-two-line rollback path (a config flag, an
unregistered call site) because of the shadow-mode discipline above — a
production rollback is `aws ecs update-service --task-definition
<previous-revision>`, plus the ECS deployment circuit breaker's automatic
rollback if new tasks never reach healthy. Database migrations do *not*
auto-rollback — the stated convention is expand/contract, so an
application rollback never forces a schema rollback.

## Engineering trade-offs — the "why we accepted this" list

| Trade-off | Why accepted |
|---|---|
| Engineering Memory grows unbounded (by design) | The audit trail is the point — only raw evidence-pack blobs are archivable, never the hypothesis/validation/correction/confidence history |
| Backend fixed at 1 replica | Correctness (no lost runs) over horizontal scale, until durable execution ships |
| No incremental indexing yet | Full-clone-per-index is simple and correct; incremental is a scoped, not-yet-built Phase 2 prerequisite |
| Frontier LLM generator gated off by default | Cost and correctness (unmeasured precision) outweigh coverage until evaluated |
| Materializer not yet the live write path | Prove correctness in shadow mode before betting production writes on a new code path |
