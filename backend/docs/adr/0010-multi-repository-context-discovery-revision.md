# ADR 0010: Multi-repository Context Discovery — architecture revision

## Status
Proposed, revised after Architecture Approval Review (round 2). Blocks merge until implemented as written here.

## Context

An initial implementation added multi-repository support to Context Discovery: cross-repository graph edges in Neo4j, explicit-vs-suggested repository candidates in the reasoning engine, a multi-select UI, and propagation through Planning/Development/Testing/Engineering Review. A principal-architect-level review found nine concrete defects, two of them correctness bugs meaning the feature didn't do what it was specified to do in its primary scenario. Round 1 of this ADR regrouped those findings into five architectural themes. This round is a stricter pass over round 1 itself: elevating its decisions into non-negotiable invariants, closing a real inconsistency round 1 left unresolved (interpretation was made symmetric for *some* candidate sources but not all of them — see Theme A below, revised), fully specifying repository-state ownership and relationship lifecycle, and adding lightweight version metadata so freshness is a computable fact, not an assumption.

**Verdict, reaffirmed:** the reasoning engine's core model (append-only Ledger of Facts/Evidence/Inferences, the Capability registry, the Investigator propose/run loop) and the indexer's core model (deterministic per-language extraction → generic `GraphPayload` → `IGraphRepository`, per ADR 0007) are both sound and are kept as-is. Everything below is scoped to *how multi-repository semantics attach to those models*, not a rewrite of either.

---

## 1. Architectural invariants

These are the rules this feature must never violate, stated so a future contributor can check their own change against them without re-reading the rest of this document. Each is enforced structurally where practical (the design makes the wrong thing impossible to write, not merely discouraged) — noted per invariant.

**I1 — Investigators observe. They never interpret.**
An `Investigator.run()` may only ever produce `Fact`s and `EvidenceRecord`s (via `Recorder`). It may never decide what a fact *means* for repository selection. Enforced structurally: `Recorder` (the only object an investigator's `run()` receives for writing) exposes `fact()`/`fact_once()`/`evidence()` and **no longer exposes `inference()`**. There is no method on the investigator-facing surface capable of writing an `Inference`. An investigator that wants to interpret something has no API to do it with.

**I2 — Investigators may read inferences, never write them.**
`propose()` may read `ledger.live_inferences(...)` to decide what to investigate *next* (e.g. "there's exactly one explicit candidate with no architecture yet, traverse it") — that's using an already-made decision to plan further observation, not making the decision. This is the one place investigators legitimately touch inference state, and it's read-only.

**I3 — All interpretation happens through deterministic ledger resynchronization.**
Every `Inference` of kind `repository_candidate` is produced exclusively by a function in `capabilities.LEDGER_RESYNC_HOOKS` — pure (`Ledger -> None`, no I/O, no session, no tool access), idempotent (running it twice produces the same live-inference set as running it once), and order-independent (it must not matter whether it runs before or after any other hook, or whether the facts it reads arrived on cycle 2 or cycle 6). If a new candidate source is ever added (a fourth relationship rule, a new claim type), it is added as a new resync hook — never as a write inside an investigator.

**I4 — Facts are immutable once recorded, with exactly one documented exception.**
No code may mutate a `Fact` after `Ledger.add_fact` returns it, except `engine._settle_claims`, which is the only place `Fact.verified` is ever raised from `False` to `True`, and only for `user_statement` facts, and only after independent corroborating evidence exists. This exception is not a precedent for adding others; any future need to "update" a fact means recording a new fact and superseding the old one (the same "superseded, not deleted" pattern `Inference.withdrawn` already establishes), not adding a second mutable field.

**I5 — There is exactly one canonical representation of repository state.**
`ContextDiscoveryResult.repositories: list[RepositoryCandidate]` is the only field any component may populate directly. Every other repository-shaped field (`ranked_repository_names`, `implementation_candidates`, `explicit_repositories`, `suggested_repositories`, `selected_repositories`) is a **read-only compatibility projection**, computed by one pure function (`projection.project_repositories`) from `repositories`, and by nothing else, anywhere. See §2.

**I6 — Compatibility projections are never independently written, overridden, or trusted as a source of truth by new code.**
A stored `AgentStep.result` may contain projection fields (written once, at `build_result` time, for legacy/debug readers). New code — anything written after this ADR — must never read them. It reads `repositories` and calls `project_repositories` itself if it needs a filtered/sorted view. The human-override mechanism may only ever target the `repositories` key. This is enforced by TypeScript typing the override payload shape to accept only `{repositories: RepositoryCandidate[]}`, and by a backend test asserting no other top-level key of `ContextDiscoveryResult` is accepted by the override endpoint for the context_discovery stage.

**I7 — Relationship computation and relationship storage have one owner each, and it is not the reasoning engine.**
`cross_repo_linker.py` is the only code that decides a cross-repository edge should exist. `Neo4jGraphRepository.replace_cross_repository_edges`/`get_outgoing_cross_repository_edges` is the only code that writes or reads one. `GraphInvestigator` only ever *reads* edges (via `Neo4jGraphTool`) and turns them into `repository_relationship` facts — it does not compute, filter, or re-derive relationships itself.

**I8 — Every relink is a full account-scoped recomputation, never a partial patch.**
Consistent with `replace_repository_graph`'s existing "full replace, not diff" policy (ADR 0007), `replace_cross_repository_edges` always replaces the *complete* set of one repository's outgoing edges. Nothing ever patches, appends to, or partially updates an existing edge set. This is what makes staleness bounded and reasoning about correctness tractable — see §3.

Enumerated field/behavior checks for I5/I6 and the `Recorder` surface for I1 are listed as concrete test requirements in the roadmap (§7), not left as documentation-only promises.

---

## 2. Canonical repository model (Theme D, strengthened)

### The single source of truth

```
                    ┌─────────────────────────────┐
                    │  repositories: list[         │   ← canonical. The ONLY
                    │    RepositoryCandidate]       │     field anything writes.
                    └───────────────┬───────────────┘
                                    │  project_repositories() — pure, one function
              ┌──────────┬──────────┼──────────┬──────────────┐
              ▼          ▼          ▼          ▼              ▼
   ranked_repository_  implementation_  explicit_  suggested_  selected_
   names               candidates       repositories repositories repositories
   (legacy, pre-       (legacy, pre-    (this feature's own — kept for
    existing field —    existing field   readability at call sites that
    Planning's star-    — see Theme B,   only ever want one filter, but
    rating & [0]        retired as a     any new consumer should call
    fallback chain      capability;      project_repositories() directly
    read this)          this projection  rather than add a sixth field)
                         stays as a
                         convenience
                         view only)
```

`RepositoryCandidate`:
```python
class RepositoryCandidate(BaseModel):
    name: str
    source: Literal["explicit", "suggested"]
    selected: bool
    reason: str = ""
    relationship: str = ""          # rel type, when source == "suggested" via a graph edge
    rank: int | None = None         # position in the ranking, when a ranking exists
    graph_version: str | None = None  # see §4 — the repo's own indexed_at at candidate time
```

`project_repositories(repositories: list[RepositoryCandidate]) -> dict[str, Any]` is a single pure function in `projection.py`:
```python
def project_repositories(repositories: list[RepositoryCandidate]) -> dict[str, Any]:
    ranked = sorted((r for r in repositories if r.rank is not None), key=lambda r: r.rank)
    return {
        "ranked_repository_names": [r.name for r in ranked] or [r.name for r in repositories],
        "implementation_candidates": [r.name for r in repositories],
        "explicit_repositories": [r for r in repositories if r.source == "explicit"],
        "suggested_repositories": [r for r in repositories if r.source == "suggested"],
        "selected_repositories": [r for r in repositories if r.selected],
    }
```
`build_result()` calls this once to populate the stored JSON. `Planning._resolve_context` and `RepositorySelector.tsx` call it again themselves, over whatever `repositories` they actually received (post human-override merge), instead of trusting the stored projection keys — this is what makes I6 real rather than aspirational, and it's what resolves a gap round 1 left open (see below).

### The override-consistency gap round 1 missed

Round 1 proposed overriding `selected_repositories` directly (mirroring the existing `graph_context_text` override). That's a violation of I5/I6: `get_stage_result()`'s merge is a **shallow** dict merge (`{**step.result, **override}`); if the override target were a *projection* field, the merged result would have an edited `selected_repositories` sitting next to an un-edited `repositories`, i.e. two disagreeing sources of truth — exactly the anti-pattern this theme exists to eliminate, reintroduced one layer up.

**Resolution:** the override target is `repositories` — the canonical field, wholesale-replaced, exactly like `graph_context_text` already is. `RepositorySelector.tsx`'s save action sends `{override: {repositories: [...full edited list, with updated `selected` flags...]}}`. Any reader that needs a projection (Planning, the panel itself on next render) derives it fresh from the merged `repositories` via `project_repositories`, so it's always consistent with whatever override is in effect, by construction — no second merge path to keep in sync.

### Trade-offs (carried over from round 1, unchanged)
Contained but real refactor of `build_result`/`ContextDiscoveryResult`; ships with an equivalence test proving the projected `ranked_repository_names`/`implementation_candidates` are byte-identical to the pre-refactor values for every existing fixture, before any downstream consumer is touched.

### Migration impact
`ranked_repository_names`/`implementation_candidates` are pre-existing production fields — their *values* are preserved exactly (equivalence-tested). `explicit_repositories`/`suggested_repositories`/`selected_repositories` are new in this same unreleased change; freely replaced. No Alembic migration (JSON column).

---

## 3. Relationship lifecycle

Answered against the existing architecture, no new subsystem.

| Question | Answer |
|---|---|
| **When created?** | Every time `cross_repo_linker`'s batch relink runs (Theme C — triggered by `run_indexing`), for every ordered pair of a user's indexed repositories. |
| **When deleted?** | (a) Every relink call `DETACH`-style replaces the *complete* outgoing edge set for the repository being relinked (I8) — an edge that no longer matches any rule simply isn't in the new set and is gone. (b) When a repository is deleted, `remove_repository`'s existing `replace_repository_graph(id, GraphPayload())` call issues `DETACH DELETE` on that repository's own node — Neo4j relationships cannot outlive either endpoint node, so this removes the repo's outgoing **and incoming** cross-repo edges in one operation, for free, with no separate cleanup code. This is existing behavior, not new; it needs a **new regression test** confirming it (round 1 asserted this from reading the Cypher; it was never actually tested against a repo with both incoming and outgoing cross-repo edges). |
| **When recomputed?** | Whenever *any* repository in the account finishes indexing (batch relink recomputes the whole account, not just the changed repository — per I8 and Theme C, because a change to repo X can affect what repo Y's own outgoing edges *should* be, e.g. Y's Feign target now resolves to X for the first time). |
| **How is staleness detected?** | An edge's `source_graph_version`/`target_graph_version` properties (§4) no longer match the current `graph_version` of the repository they were computed from. This is a pure comparison, computable on read, never a background sweep. |
| **Repository rename?** | Renames happen at `set_selected_repositories` (re-syncing the user's tracked-repo list from GitHub), independent of indexing. A rename updates `Repository.name` in Postgres immediately but does **not** trigger a relink. Edges computed under the old name remain valid (they're keyed by `repository_id`, a UUID, never by name — name is only the *matching key* at computation time) until the next index event for *any* repository in the account, at which point the relink pass re-evaluates every name-based rule using current names. **Bounded staleness window: until the next index event for the account.** No new mechanism proposed to shrink this window further — it's the same staleness class the underlying component/topic graph already has relative to source changes, and closing it fully means the out-of-scope real-time trigger system, not this feature. |
| **Repository deleted?** | Covered above — self-cleaning via `DETACH DELETE`. |
| **After partial indexing?** | Doesn't exist as a distinct state for one repository's *own* graph: `replace_repository_graph` writes inside one Neo4j transaction (`begin_transaction`...`commit`), so a mid-write failure rolls back and leaves the previous graph intact — no half-written repository graph is ever observable. What *can* happen: a repository's own graph indexes successfully, but the **relink step that follows it** (a separate operation) fails or the process dies before it runs. The repository's own graph is correct and usable; its cross-repo edges (and any repository that links *to* it) may be stale until the next successful relink for the account. This is visible and bounded by `graph_version` comparison (§4) — not hidden, not a new failure mode requiring its own status column. |
| **After failed indexing?** | `index_repository` raising means `replace_repository_graph` never ran (or rolled back) — the previous graph, if any, is untouched, `run_indexing` propagates the exception before reaching the relink step, and the relink step is never attempted. No edges change. `IndexingJob.status` already becomes `"failed"` via the existing `run_indexing_job` error handling. No change needed. |
| **Orphaned relationships?** | Structurally impossible, not merely handled: `_write_edges`'s Cypher is `MATCH (a {id: source_id}), (b {id: target_id}) MERGE (a)-[r:TYPE]->(b)` — a `MATCH`, not a `MERGE`, on the endpoint nodes. If either node doesn't exist, the edge silently fails to write (no relationship, no orphan). Neo4j itself also cannot represent a relationship with a missing endpoint. No cleanup job needed or proposed. |
| **Relink failure policy?** | Logged and swallowed, does not fail the triggering `IndexingJob` (the repository's own graph is still valid and usable on its own) — carried over from round 1, now made observable via `graph_version` mismatch instead of being silently unverifiable. |

---

## 4. Graph versioning (lightweight — no historical storage)

**Goal:** deterministic answers to "was this computed from current data," not a history of past graphs.

**Design — reuses existing data, adds nothing to Postgres, adds only scalar properties to existing Neo4j nodes/edges:**

- **`graph_version` for a repository = its most recent successfully-`finished_at` `IndexingJob` row.** This field already exists (`IndexingJob.finished_at`, set by `run_indexing_job` the moment `run_indexing` returns successfully). No new column, no new table.
- Every cross-repository edge gains three properties, stamped at computation time from each side's *current* `graph_version`: `computed_at` (now), `source_graph_version`, `target_graph_version`. Same `_write_edges` MERGE...SET+= mechanism already used — no new write path.
- Every `RepositoryCandidate` carries `graph_version: str | None` (the repository's own `graph_version` at the moment discovery produced this candidate) — so Planning, or a future audit view, can state "this plan was built from a graph indexed at T" without a separate lookup.
- **Freshness = `edge.source_graph_version == current_graph_version(source)` and similarly for target.** A pure comparison; no new job computes or stores "is this stale" as its own fact — it's derived on read, same principle as I5 applied to graph state instead of repository state.

**Explicitly not built:** a `graph_version` history table, a background staleness sweep, or automatic re-triggering of a relink when staleness is detected. Detection is free; *reaction* to detection (auto-relink) is a legitimate future feature this design doesn't foreclose (the version fields are exactly what it would need) but does not implement now — consistent with "keep this lightweight."

---

## 5. Data ownership

| Responsibility | Owner | Consumers | Output |
|---|---|---|---|
| Repository discovery (which repos exist & are indexed) | `GetIndexedRepositoriesTool` | `GraphInvestigator`, `Neo4jGraphTool`, Development/Testing standalone fallback | `repository` facts |
| Repository interpretation (fact → candidate, explicit/suggested) | `capabilities.LEDGER_RESYNC_HOOKS` (exclusively — I3) | `projection.build_result` | `repository_candidate` inferences |
| Repository selection (candidate → in-scope-for-this-work) | `projection._selected_repositories` (default rule) + human override on `repositories` (I5/I6) | Planning, Development, Testing, Engineering Review, `RepositorySelector` | `RepositoryCandidate.selected` |
| Relationship computation | `cross_repo_linker.py` (I7) | Theme C batch relink pass | edge candidates (in-memory) |
| Relationship storage | `Neo4jGraphRepository` (I7) | `cross_repo_linker` (write), `Neo4jGraphTool` (read) | Neo4j edges |
| Relationship presentation (edge → reason string) | `GraphInvestigator._relationship_reason` (sole renderer) | `repository_relationship` fact, `RepositoryCandidate.reason` | `str` |
| Confidence calculation | `capabilities.py` (`Capability.assess`/`overall_confidence`) — one `repository` capability post-Theme B, no duplicate | `discovery_report`, UI | `CapabilityAssessment` |
| Gap generation | `engine._sync_gaps` + each `Capability`'s own fields | UI clarification banner | `KnowledgeGap` |
| Remediation generation | Each `Capability.remediation` | `blocking_reasons`/`remediation_steps` | `list[str]` |

No responsibility above has a second, competing owner. Where round 1 left an implicit second owner (the override mechanism competing with `build_result` for who populates `selected_repositories`), §2 closes it.

---

## 6. Long-term maintainability — could a future engineer accidentally violate this?

Walked through the likely accidental violations and what stops each one:

- *"I'll just add a quick inference write inside my investigator for this new signal."* — Can't: `Recorder` has no `inference()` method (I1, structural, not documentation).
- *"I'll add a sixth repository-shaped field, it's simpler than touching the projection."* — Caught by a test enumerating `ContextDiscoveryResult`'s repository-related field names against a fixed allowlist; fails loudly if a new one appears without updating this ADR and `project_repositories`.
- *"I'll override `selected_repositories` directly, it's more targeted than the whole list."* — The override payload type only accepts `repositories`; `RepositorySelector.tsx` has no code path that constructs anything else. Backend test asserts the override endpoint accepts only `repositories` for this stage.
- *"I'll add relink logic inline in a new admin endpoint."* — Documented (I7) as reachable only through the single batch entry point (`cross_repo_linker.relink_account`); code review checklist item, since this one isn't structurally enforceable without more machinery than it's worth.
- *"I'll make this resync hook do a quick Neo4j lookup since it's convenient here."* — Violates I3 (no I/O). Enforced by a unit test that runs every registered hook against a `Ledger` with no session/tool access wired up at all (a `Ledger`-only signature makes this the natural test, not an extra one).

---

## 7. Re-prioritized roadmap (supersedes round 1's; scope grew — stated plainly, not hidden)

**P0 — blocks merge, correctness and invariant enforcement.**
1. Theme A, revised: collapse *all* `repository_candidate` production into `LEDGER_RESYNC_HOOKS` — not just relationship-based suggestion (round 1) but also ranking-based suggestion and verified-claim promotion, closing the same class of gap for the sources round 1 left inside the investigator. Remove `Recorder.inference()` entirely (I1). This is a larger change than round 1 scoped — sequencing within it:
   - `repository_ranking` fact (records the scored list; investigator no longer decides leaders)
   - `resync_repository_candidates` (explicit, from `reference` facts — exists)
   - `resync_verified_claim_candidates` (explicit, from verified `user_statement` facts — new; also fixes `_verify_repository`, which must check the underlying `repository` **fact**, not the derived `repository_candidate` **inference**, to avoid a circular dependency against `_settle_claims`'s own ordering — see below)
   - `resync_ranked_candidates` (suggested, from `repository_ranking` facts — new)
   - `resync_relationship_candidates` (suggested, from `repository_relationship` facts — round 1)
   - Engine sequencing fix: `resume()` must re-run `LEDGER_RESYNC_HOOKS` once more **after** `_settle_claims` (which is the only place `Fact.verified` flips) and before its final `refresh_assessments()`/`_sync_gaps()` — otherwise a claim verified in this call is never reflected in the readiness/gaps the caller sees. Round 1's design had this exact gap; caught in this pass, not left for implementation to discover.
2. Theme B: retire `implementation_candidates` as a separate capability; fold into `repository`.
3. Regression tests: the exact failing scenario from the original review, plus order-independence tests for each resync hook, plus the `resume()` sequencing fix above.

**P1 — blocks merge, enterprise-scale bar.**
4. Theme C: batch-fetch-then-evaluate; single-flight guard per account.
5. Graph versioning (§4) stamped into the same relink transaction — near-zero incremental cost, ships alongside Theme C rather than as a separate pass.

**P2 — this PR, contained.**
6. Theme D / canonical model (§2), including the override-consistency fix (targeting `repositories`, not a projection field) — this closes a gap round 1's version of Theme D would have shipped with.

**P3 — this PR, isolated.**
7. Theme E: unindexed-mention gap; per-edge-type confidence.

**P4 — accompanies P0–P3, not deferred, not optional.**
8. The relationship-lifecycle regression tests from §3 (delete cascades both directions; relink failure doesn't fail indexing; orphan-impossibility is asserted, not just argued).
9. The invariant-enforcement tests from §6 (field allowlist, override payload shape, resync-hook I/O-free assertion).
10. The originally-promised full discovery→planning integration test.

**Explicitly out of scope, named so it isn't silently expected:** a real task queue (ADR 0007's own deferred decision); collapsing Planning/Development/Testing onto `repositories` directly instead of the legacy projections (a second, separable migration once this shape has run in production for one release); auto-triggered relink on staleness detection (§4 makes it possible, doesn't implement it); a `graph_version` history/audit trail beyond "the current value."

---

## 8. Final architecture review

**Is the architecture internally consistent?** Yes. Traced every cross-theme dependency: Theme A's revised scope (all four candidate sources through resync hooks) is consistent with I3 as stated, not a partial application of it. Theme D's override fix is consistent with I5/I6 and doesn't reintroduce the shallow-merge inconsistency round 1 would have shipped. Theme C's version stamping (§4) writes into the same transaction Theme C already opens — no new transaction boundary, no new consistency question. The ownership table (§5) has no entry with two owners.

**Does any theme contradict another?** No contradictions found. One real gap found and closed in this pass: round 1's Theme A was inconsistent with its own stated goal (interpretation symmetry) because it only fixed *one* of three investigator-owned interpretation paths (relationship-based). This pass generalizes the fix to all three and states the general rule as invariant I3, which is the more defensible, less patch-shaped form of the same idea — this is a genuine strengthening, not a cosmetic addition.

**Unnecessary complexity?** The resync-hook count went from 2 (round 1) to 4 (this round). Net complexity is flat, not higher: each hook is smaller and single-purpose than the logic it replaces inside `_reassess_candidates`, and `_reassess_candidates` itself shrinks correspondingly (it stops writing inferences at all — it becomes "run the query, record facts, return an observation string"). Simplification happened at the investigator; the hook count grew to receive what was removed from there. Not over-engineering — it's the same total logic with exactly-one-owner-per-piece instead of one file doing three different things conditionally.

**Can any theme be simplified?** Considered collapsing the four resync hooks into one large function. Rejected: each hook has a distinct fact-kind input and is independently testable/orderable; one large function would reintroduce the "one place does everything, conditionally" shape this pass exists to remove.

**Hidden migration risks?** One identified and resolved during this pass (the override-target inconsistency, §2) that round 1 would have shipped as a working-but-architecturally-inconsistent feature. Re-checked Theme B's blast radius (still confirmed zero downstream readers of `implementation_candidates` beyond `capabilities.py`'s own registry and the report renderer). Re-checked Theme C's version stamping doesn't require a new migration (reuses `IndexingJob.finished_at`, confirmed present in the existing model). No further hidden risks identified.

**Enterprise scalability adequately addressed?** Theme C's O(N) fetch shape plus the single-flight guard is adequate for the stated scale (tens to low hundreds of repositories per account). Graph versioning adds negligible cost (three scalar properties, same transaction). A real task queue remains explicitly deferred, consistent with ADR 0007, not silently assumed away.

**Would I approve this ADR for implementation?** Yes.

**Architecture Approved.**

Implementation resumes at P0 (item 1: the revised Theme A). No implementation code has been written during this review.
