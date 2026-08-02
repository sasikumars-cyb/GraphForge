"""ADR 0018 — Graph Parity Engine.

A pure, deterministic proof that two `GraphPayload`s (the legacy
direct-write graph and the Materializer's Engineering-Memory projection)
are equivalent, up to a configurable, evidence-based set of accepted
differences. No database access, no Neo4j, no Postgres, no feature flags,
no cutover logic — this package answers exactly one question ("are these
two graphs the same graph?") and nothing else. Two real, already-named
future consumers justify its existence as its own package rather than a
function tucked into `materializer.py`: Shadow Mode (compare on every
indexing run, before any cutover decision) and Production Cutover
(the gate a repository must pass before `"primary"` mode is enabled for
it) — both read this package's output, neither is built by it.
"""

from __future__ import annotations
