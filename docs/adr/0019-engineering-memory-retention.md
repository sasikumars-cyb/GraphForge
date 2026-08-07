# ADR 0019: Engineering Memory retention (KAN-24)

## Status

Partially Accepted. Phase 1 (below) is implemented. Phase 2 (time-based
partitioning + cold-storage archival) is a documented proposal, not yet
built — it needs a retention-window decision this ADR cannot make (see
"Dependencies").

## Context

ADR 0018 made Engineering Memory's growth tables — `engineering_evidence_packs`,
`knowledge_relationships`, `user_corrections` — deliberately append-only,
and its own Consequences section accepted the resulting unbounded row
growth "by design," with one carve-out: `EvidencePack` blobs are
regenerable by re-extraction against the same commit, so they alone "may
be archived/compacted." `Hypothesis`/`ValidationResult`/confidence-history
rows are, in the same section, "never compacted or deleted" — that is the
audit trail this platform's explainability promise depends on, not an
oversight.

KAN-24 named the resulting gap: "accepted as a design trade-off... without
a corresponding retention mechanism being scoped." Left unaddressed,
storage and query cost on these tables scale with re-index frequency ×
repository count, with no ceiling — `docs/handbook/12_DIFFICULT_QUESTIONS.md`
already names this as the platform's most defensible "what breaks first
under real growth" answer, alongside the indexer's full-clone model.

The ticket's own "Dependencies" field is explicit: *"Retention window
requires a product/compliance decision before implementation."* No such
decision exists yet. This ADR does not invent one on the audit-trail
tables' behalf — see Phase 2 below for what is proposed, pending that
decision, and Phase 1 for the part of the ticket that needed no such
decision at all.

## Decision

### Phase 1 (implemented): what didn't need a compliance decision

Two changes, both already-authorized by ADR 0018's own text or already an
acknowledged design debt, neither touching the "never compacted or
deleted" tables' actual row count:

**1. `EngineeringMemoryRepository.prune_evidence_packs(repository_id,
keep_last_n)`** — deletes `engineering_evidence_packs` rows beyond the
newest `keep_last_n` for a repository. This is the one table ADR 0018
already blessed for deletion ("regenerable by re-extraction against the
same commit"); no new policy decision is required to delete from it, only
a choice of `keep_last_n`, which is an operational parameter, not a
retention-window/compliance question — nothing here is presented to a user
or auditor as a permanent record. Not wired into an unattended background
job: it is called explicitly (by an operator, or a future scheduled task
someone deliberately adds), so a wrong `keep_last_n` doesn't silently
delete more than intended before anyone notices. `knowledge_relationships`
and `user_corrections` gained no equivalent method — a regression test
(`test_no_delete_path_exists_for_the_audit_trail_tables`) asserts neither
repository method exists, so a future change can't casually add one
without deliberately removing that guard.

**2. `EngineeringMemoryRepository.get_current_relationships` now computes
"latest version per key" in SQL** (a `row_number()` window function,
partitioned by `relationship_key`, ordered by `sequence` descending) instead
of pulling every historical row for a repository into Python and deduping
there. That was this table's actual "query performance degrades over time"
risk (KAN-24's Technical Impact): the read path that rebuilds the Neo4j
projection scaled with *total history* — which grows unbounded by design —
not with the *current* relationship count, which does not. This is a real
fix, not a workaround: the method's own prior docstring already flagged the
Python-side approach as "worth revisiting only if this becomes measurably
slow, not pre-optimized now" — KAN-24 is that revisit.

Both changes are covered by `tests/integration/test_engineering_memory.py`
against a real Postgres transaction, including a synthetic high-growth
case (8 relationships × 20 versions each = 160 history rows) proving
`get_current_relationships` still returns exactly one, correct row per key.

A third, small addition supports Phase 1's own correctness: `engineering_evidence_packs`
gained a `sequence` identity column (migration `8f2d1b3d9024`), mirroring
`knowledge_relationships.sequence` exactly. `created_at` cannot order
"newest N" reliably — Postgres's `now()` is transaction-scoped, so two
packs committed in the same transaction get an identical timestamp (the
same bug `knowledge_relationships.sequence` was already added to fix, per
that model's own module docstring). Nothing depended on strict pack
ordering before pruning existed, so this was latent, not previously
observed — caught here because pruning is the first thing to actually
require it. Purely additive migration: no existing column changed, no row
touched beyond backfilling the new column's values.

### Phase 2 (proposed, not implemented): partitioning + cold archival

The ticket's own Suggested Solution: *"Time- or count-based table
partitioning (Postgres native partitioning) plus a cold-storage archival
job for rows past the audit-required retention window."* This remains the
right shape for `knowledge_relationships` and `user_corrections` — but
implementing it means recreating both tables as natively partitioned
(Postgres requires the partition key in every unique/primary-key
constraint, so `knowledge_relationships`' `(id)` PK and `(sequence)`
unique constraint would both need to become composite with `created_at`),
migrating existing rows, and coordinating a maintenance window — real
schema surgery on tables the live product already writes to continuously,
not a change to make unsupervised in the same pass as Phase 1.

Proposed design, for when a maintenance window and a real retention-window
decision are both available:

- `PARTITION BY RANGE (created_at)`, monthly partitions, on both tables.
- Partitions are **never dropped or detached by an automatic job.** Every
  partition stays attached (and therefore queryable, and therefore part of
  the audit trail) indefinitely by default — this alone already bounds
  per-partition index size and improves vacuum/maintenance behavior as
  total history grows, without deleting anything, so it does not depend on
  a retention-window decision either.
- **Cold-storage archival is a separate, operator-triggered, dry-run-by-default
  tool** — not a scheduled job — that can detach (not drop) a partition
  older than a configured threshold and export it (e.g., to S3/Parquet).
  Detaching a partition removes it from the live table's default query
  scope but does not delete the data; a detached partition can be
  re-attached. This is the actual point where a real retention-window
  decision is required: how old is "old enough to move to cold storage"
  is a business/compliance call this ADR is not positioned to make, and
  the ticket agrees ("ties into the deferred 'Policy as a first-class
  concept' RFC scope").
- **Provisional default, for discussion, not yet in effect:** 13 months
  hot (covers any reasonable audit/reporting cycle with a one-month
  buffer), archived-not-deleted indefinitely after. Chosen only as a
  starting point for that conversation — not committed to.

### Why not implement Phase 2 now anyway

Two independent reasons, not one:

1. **The retention window is a real open dependency**, named by the ticket
   itself, not a formality — picking a number unilaterally and shipping
   deletion/archival logic against it would be answering a compliance
   question no one asked me to answer.
2. **The migration itself is real production risk** independent of the
   retention question: composite-key changes to two tables under active
   write load, on infrastructure already flagged elsewhere in this
   backlog (KAN-30: `desiredCount=1`, no zero-downtime deploys) as unable
   to absorb a bad migration without an outage. Shipping that unsupervised,
   with no path for a human to review the migration plan first, is the
   kind of hard-to-reverse action this platform's own agent-permission
   model (KAN-28) exists to gate — applied here to the person building it,
   not just the agents it audits.

## Consequences

- `engineering_evidence_packs` now has a real, tested, ADR-authorized
  pruning path — usable today by an operator or a future scheduled task,
  bounding that table's growth without any new policy decision.
- `get_current_relationships` — the read path Neo4j projection rebuilds
  depend on — no longer degrades with total history size, only with
  current relationship count. This is the concrete "query performance
  verified to stay flat" acceptance criterion, for the one query this
  phase could actually fix without the retention-window dependency.
- `knowledge_relationships` and `user_corrections` remain fully
  unbounded in row count — Phase 1 does not change that, by design (see
  "Why not implement Phase 2 now"). KAN-24's storage-growth business risk
  is therefore only partially closed: the query-performance half is fixed,
  the storage-cost half still needs Phase 2.
- A follow-up ticket is the right next step for Phase 2, scoped exactly as
  this ADR proposes it, once a retention window is decided and a
  migration window is available — not a silent continuation of KAN-24
  under its original acceptance criteria, since "partitioning
  implemented and tested" (KAN-24's AC #2) is explicitly deferred here.

## References

- ADR 0018 Consequences (the original "accepted by design" acceptance)
- `docs/handbook/12_DIFFICULT_QUESTIONS.md` — "What breaks first (under
  real growth)?"
- `app/repositories/engineering_memory_repository.py` — `prune_evidence_packs`,
  `get_current_relationships`
- `alembic/versions/8f2d1b3d9024_add_sequence_to_engineering_evidence_packs.py`
- `tests/integration/test_engineering_memory.py`
