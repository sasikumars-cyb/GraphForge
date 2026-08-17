"""Architecture enforcement tests — Phase 0 of the frozen implementation
sequencing plan.

These tests do not test *behavior*. They test that the repository's
*shape* has not drifted away from the three frozen architecture contracts
(`docs/graphforge/ENGINEERING_STATE_ARCHITECTURE.md`,
`REASONING_ENGINE_ARCHITECTURE.md`,
`CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md`) while later implementation
phases are built out.

Every test in this package is a **ratchet**, not a behavior check: it
pins the current, known-legitimate set of exceptions to a rule the
frozen architecture will eventually enforce structurally (via a real
Control Plane, a real Capability registry, a real Policy store), and
fails the build the moment something *new* violates that rule. Extending
an allowlist here is a deliberate, reviewable, one-line diff — exactly
the "explicit architectural review" gate the Phase 0 design calls for.

Nothing in this package requires a database, Neo4j, or network access —
these are static/AST-level checks over the source tree, kept fast and
hermetic on purpose (same discipline as
`tests/unit/ai/test_manifest_dependency_integrity.py`, which this
package's tests are modeled on).
"""

from __future__ import annotations
