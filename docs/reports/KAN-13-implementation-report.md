# KAN-13 Implementation Report — Documentation Integrity

**Epic:** [KAN-13](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-13)
**Date:** 2026-08-03
**Status:** Complete (1 of 1 ticket resolved)

## Summary

`docs/architecture/overview.md` documented the backend as it stood at the
end of the original "ChangeGuard" phases — auth, GitHub integration, the
deterministic indexer, deterministic PR impact analysis — and stated
directly that "no AI/LLM reasoning exists anywhere yet." That was true
when written; it has not been true for some time. The codebase now has an
Agent Orchestrator, 12+ registered agents, a five-stage Knowledge Engine,
and Engineering Memory, none of which this document — the first
architecture document `README.md` points a new reader to — mentioned.

Rather than rewrite `overview.md` into a second copy of
`docs/graphforge/ARCHITECTURE.md` (out of scope for a 3-point ticket, and
duplicative), the ticket's own suggested lighter option was taken: mark it
explicitly historical, accurate for what it covers, with a forward pointer
to the two documents that are current — `docs/graphforge/ARCHITECTURE.md`
(target design) and `docs/handbook/16_REALITY_CHECK.md` (evidence-cited
current state). The same pattern already used for ADR 0001 ("superseded").

## Jira IDs

| Ticket | Outcome |
|---|---|
| [KAN-39](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-39) — `docs/architecture/overview.md` contradicts current implementation | **Fixed.** Historical marker added with forward pointers; `README.md` status line, Stack table, and layout diagram all corrected to stop implying no AI/agent capability exists. |

## Files changed

- `docs/architecture/overview.md` — historical-document callout added at the top, pointing to `ARCHITECTURE.md` and `16_REALITY_CHECK.md`, generalizing the "trust the code" discipline `docs/deployment/README.md` already states for its own directory
- `README.md` — status line rewritten to reflect current capability with a pointer to the Reality Check; Stack table gets a new "AI / Agents" row and a corrected "Future" row (Jira integration and login-via-GitHub are still genuinely future; AI/LLM reasoning is not); project-layout diagram lists the backend's actual current top-level packages; the "Getting started" section's doc pointer updated to route to `ARCHITECTURE.md` for future-integration detail instead of the historical doc

## Design decisions

1. **Mark historical, don't rewrite.** `overview.md`'s content is still accurate for the subsystems it describes (auth, GitHub integration, the indexer, PR impact analysis) — the problem was silence about everything built since, not incorrect detail. A callout that's honest about scope costs a few lines and zero maintenance burden; a full rewrite would require keeping a second architecture document in sync with `ARCHITECTURE.md` going forward, which is exactly the kind of drift this ticket exists to fix, not reintroduce.
2. **Generalized the existing "trust the code" rule instead of inventing new process.** `docs/deployment/README.md` already states this discipline for its own directory. The acceptance criteria asked for a proposed repo-wide doc-drift check — rather than add new tooling/process infrastructure (out of scope for this ticket and this session's mandate to keep changes incremental), the existing rule was extended in place with one sentence tying it to the ADR superseded-doc precedent, which is the same social-technical answer already working elsewhere in this repo.
3. **Touched every place the same stale claim appeared, not just the cited ones.** The ticket named `docs/architecture/overview.md` and `README.md`'s status line specifically, but the same "AI engine" listed as future work also appeared in the Stack table's Future row and the "Getting started" section's doc pointer. Fixed all four occurrences in the same change rather than leaving three of four stale claims for a future pass.

## Risks

- None identified. This is a documentation-only change with no code path affected; no test suite exercises Markdown content.

## Remaining work

- `docs/handbook/12_DIFFICULT_QUESTIONS.md` still cites the (now-closed, per KAN-7) Feign cross-repository gap as "biggest technical debt" — a smaller, separate instance of the same doc-drift problem, left for a future documentation pass.
- The acceptance criteria's "repo-wide doc-drift check" is proposed as a discipline (documented in the callout), not automated tooling. If the team wants an *enforced* check (e.g., a CI step that fails on specific stale phrases), that would be new scope worth its own ticket rather than folding into this one.
