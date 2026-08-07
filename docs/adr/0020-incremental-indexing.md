# ADR 0020: Incremental indexing (KAN-32)

## Status

Partially Accepted. The incremental indexing mechanism itself (below) is
implemented and tested. The webhook receiver that would trigger it on a
real GitHub push is a documented, scoped follow-up, not built in this
pass — see "Not implemented" below.

## Context

`app.indexer.services.indexing_service.index_repository` has always done
a full shallow `git clone` + full re-parse + full `replace_repository_graph`
(`DETACH DELETE` every node for the repository, then rewrite everything)
on every indexing run, regardless of how much actually changed. ADR 0007
scoped incremental indexing out of the initial deterministic-parsing
implementation deliberately; `ROADMAP.md`'s own Technical Debt section and
`docs/handbook/12_DIFFICULT_QUESTIONS.md`'s "How does it scale?" both
already name the consequence: indexing cost scales with total repo count
× re-index frequency, not with how much of a repository actually changed,
which the product's own roadmap treats as a blocker for the multi-repo
Architecture Agent use case "at more than demo scale" — not a nice-to-have.

## Decision

### The mechanism

A push touching a handful of files no longer needs a clone at all:

1. **`app.indexer.scanner.incremental.compute_changed_files`** — GitHub's
   Compare API (`GET /repos/{owner}/{repo}/compare/{base}...{head}`)
   returns the changed-file list directly; no clone needed to know *what*
   changed.
2. **`is_safe_for_incremental_update`** — a conservative gate: too many
   changed files (>50), or any changed manifest (`pom.xml`,
   `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile` — these
   carry project-level facts like `MavenDependency`/`PythonDependency`
   that aren't scoped to one file's node set the way `Controller`/
   `PythonModule` are), and the run falls back to a full index instead.
   A false "not safe" costs one full index (today's existing, always-
   correct behavior); a false "safe" would mean silently wrong graph
   data — the asymmetry this gate is built around.
3. **`materialize_changed_files`** — fetches only the changed files'
   current content via GitHub's Contents API and writes them into a
   fresh temp directory at their real relative paths. The *existing,
   unmodified* `ILanguageParser` implementations (`PythonParser`,
   `SpringBootJavaParser`) just walk a directory looking for their own
   file extension — verified directly from their source before writing
   any of this: neither one does anything cross-file during parsing
   itself (cross-file symbol resolution, where it exists, happens later,
   in `build_graph`) — so they parse this minimal directory correctly
   with **zero parser changes**.
4. **`Neo4jGraphRepository.replace_repository_files_subgraph`** — the new
   primitive this whole mechanism is built on: deletes only nodes whose
   `file_path` matches one of the changed files (`DETACH DELETE`, so
   stale edges go with them), then upserts the freshly-parsed subset via
   the same `MERGE` `replace_repository_graph` already uses. Nodes with
   no `file_path` at all (`MavenDependency`, `KafkaTopic` — see
   `app.indexer.graph.builder`) are never in the deletion scope
   regardless of what's in `file_paths`; a shared node the changed files
   still reference (a Kafka topic still produced to) is upserted, not
   deleted-then-orphaned.
5. **`Repository.last_indexed_commit_sha`/`last_indexed_language`** (new
   columns, migration `3a7c1e9f4b52`) — what the *next* run diffs
   against. Resolved via `git rev-parse HEAD` against the clone a **full**
   index already made (`app.indexer.scanner.repository_cloner.
   resolve_head_commit_sha`) — deliberately not a second GitHub API call:
   works identically for `source="github"` and `source="local"`
   repositories, and costs no extra network round-trip.

`app.indexer.services.indexing_service.run_indexing` (the one DB-aware
entrypoint every real indexing run already goes through) tries the
incremental path first (`_attempt_incremental_index`); any of the
following makes it decline and fall back to the exact, unmodified,
always-correct full `index_repository` path instead — never raising,
always logged:

- no prior indexed commit (first index for this repository)
- `repository.source != "github"` (a local path has no GitHub API to ask)
- the branch head or the diff can't be resolved (network/API failure)
- `is_safe_for_incremental_update` rejects the diff
- the scoped re-parse+merge itself raises for any reason

This is KAN-32's own Acceptance Criterion #3 ("full re-index remains
available as a fallback/repair operation") satisfied by construction, not
by every caller remembering to catch something — `index_repository` is
untouched by this ADR except for two new optional callback parameters
(`on_language_detected`/`on_commit_resolved`) that every existing call
site omits and is unaffected by; its return type (`IndexingSummary =
dict[str, int]`, asserted on by key/shape in
`tests/integration/test_indexing_pipeline.py` and others) was deliberately
not widened for this feature — an avoidable blast radius for what is, for
every existing caller, purely additive.

### What "safe" deliberately excludes: cross-file resolution correctness

`build_graph`'s Python function-call resolution
(`_build_python_graph`'s `function_node_id_by_bare_name`) needs a
repository-wide function-name index to decide whether a bare call is
unambiguous — a scoped re-parse of only the changed files cannot rebuild
that index from a partial view. This is not a gap papered over: a scoped
update only ever *adds or replaces* nodes for the files it re-parsed and
their own edges; it never touches or recomputes edges belonging to
unchanged files, so an unchanged file's already-correct call resolution
is never disturbed. The only theoretical staleness this leaves is a call
*from* a changed file that should now resolve differently given the
broader repository (rare — most calls are local or to a stable API) — an
acceptable, documented trade-off matching the same asymmetry
`is_safe_for_incremental_update` is built around, not a correctness
promise this ADR claims to fully close.

### What is safe to delete: nothing new, same rule as ADR 0019

Confidence-history/audit-trail tables are unrelated to this ADR — this is
purely graph-store (Neo4j) scoping, not a retention policy. No new
deletion authority was created; `replace_repository_files_subgraph`
deletes exactly what `replace_repository_graph` already deleted for the
same repository, just narrower in scope.

## Not implemented — deliberately deferred

**The GitHub `push` webhook receiver itself.** `app/api/v1/routers/
webhooks.py` today only handles `pull_request` events
(`handle_pull_request_event`) — there is no `push` handling at all, so
nothing currently *calls* `run_indexing` in response to a real push; it
only runs today via the existing manual `POST /repositories/{id}/index`
trigger. Adding a `push` handler is a well-scoped, low-risk follow-up that
mirrors the existing handler exactly (same signature verification via
`verify_signature`, same `handle_*_event`-shaped service function, same
durable-queue scheduling via `schedule_indexing_job`) — deferred from this
pass only because it's a distinct, separately-reviewable unit of work
(a new inbound, security-sensitive endpoint) from the indexing mechanism
itself, not because of any technical blocker. `run_indexing` (this ADR's
actual deliverable) needs no further change to be called from it: a push
handler's whole job is "look up the `Repository` row, call
`schedule_indexing_job`," identical to what `trigger_indexing` already
does today.

**Materializer (KAN-16) sequencing**, named as a real risk in KAN-32's own
ticket ("sequence carefully against the Materializer cutover... both
touch how the graph gets written"). `run_indexing`'s existing KAN-16
shadow-compare step (diagnostic-only, runs after every real indexing run
already) was left completely untouched — this ADR adds a new Neo4j write
path (`replace_repository_files_subgraph`) alongside the existing one,
it does not change what the Materializer shadow-compares against.
Confirmed by the full test suite passing unchanged.

## Consequences

- A push touching a handful of files, with a prior full index available,
  no longer requires a `git clone` or a full re-parse — cost now scales
  with files changed, not repository size, for the common case.
- First index, any repository with no prior indexed commit, a `source=
  "local"` repository, an unsafe diff, or any failure along the way all
  still get the exact full-index behavior that existed before this ADR,
  unchanged.
- `IndexingSummary` gained one optional key (`files_reindexed`) present
  only on a scoped run — existing consumers reading the other keys are
  unaffected; nothing currently asserts the key set is exhaustive.
- The push webhook is the next concrete step to make this "webhook-
  driven" in the sense KAN-32's title asks for; today it is "incremental-
  capable, triggered the same way full indexing already is."

## References

- ADR 0007 (deterministic parsing) Consequences — where incremental
  indexing was originally scoped out
- `docs/graphforge/ROADMAP.md` Technical Debt; `docs/handbook/
  12_DIFFICULT_QUESTIONS.md` "How does it scale?"
- `app/indexer/scanner/incremental.py`, `app/indexer/services/
  indexing_service.py`, `app/graph/neo4j_repository.py`
  (`replace_repository_files_subgraph`)
- `alembic/versions/3a7c1e9f4b52_add_last_indexed_tracking_to_repositories.py`
- `tests/integration/test_incremental_indexing.py`, `tests/integration/
  test_replace_repository_files_subgraph.py`, `tests/unit/indexer/
  test_incremental.py`
