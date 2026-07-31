"""Pure runtime-execution traversal — RFC-004 Capability 1's reusable
library, not the capability itself.

This module reconstructs a bounded, cycle-safe call chain from an already-
available `GraphPayload`'s `CALLS`-typed edges. It is deliberately a
standard-library-shaped module: pure functions over data already in hand,
no I/O, no Ledger, no capability registration, no investigator. Later
commits wire this into `curate_evidence()` (RFC-004 Phase 1a, Commit 4) and
record its output as Ledger facts — this module knows nothing about either.

`GraphNode`/`GraphEdge`/`GraphPayload` (`app.graph.models`) are reused
directly as input; they already represent exactly "a graph's nodes and
edges" and reinventing them here would be a parallel graph vocabulary for
no reason. `CallStep`/`CallChain` are the only new types — nothing existing
represents an *ordered, depth-tagged traversal result*, which is a
different kind of object from an unordered structural edge/node.

Determinism: for identical input, `build_call_chains` always produces
identical output. Traversal order is fixed by the order `CALLS` edges
appear in `payload.edges` (a plain list, already deterministic), never by
Python `set`/`dict` iteration order.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from pydantic import BaseModel, Field

from app.graph.models import GraphPayload

_CALLS_EDGE_TYPE = "CALLS"


class CallStep(BaseModel):
    """One `CALLS` edge traversed while reconstructing a chain.

    `depth` is the step's distance from the entry point (1 for a direct
    call out of the entry point, 2 for a call one level further, ...).
    A step whose `target` had already been visited by an earlier, shallower
    step is still recorded (the edge is real and worth showing), it is
    simply never expanded further — see `build_call_chains`'s cycle
    handling.
    """

    source: str
    target: str
    depth: int


class CallChain(BaseModel):
    """The bounded, cycle-safe reconstruction of what `entry_point` calls,
    directly or transitively, over `CALLS` edges only.

    `terminal_operations` are visited components with no further outgoing
    `CALLS` edge in this payload — the ends of the chain. `truncated` is
    `True` only when a real edge existed past `max_depth` and traversal
    deliberately stopped there — a truncated chain must never be
    indistinguishable from one that terminated naturally, so this field is
    never inferred, only set at the exact point traversal actually cut a
    real edge off. `cycle_detected` is `True` when at least one traversed
    edge pointed back at an already-visited component (self-recursion or a
    longer cycle) — recorded, not silently absorbed.
    """

    entry_point: str
    steps: tuple[CallStep, ...] = Field(default_factory=tuple)
    terminal_operations: tuple[str, ...] = Field(default_factory=tuple)
    truncated: bool = False
    cycle_detected: bool = False


def _calls_adjacency(payload: GraphPayload) -> dict[str, tuple[str, ...]]:
    """Every component's *unique* `CALLS` targets, in the order they first
    appear in `payload.edges` — the one place duplicate edges and edge-type
    filtering are handled, shared by every entry point's traversal below.
    """
    seen: dict[str, dict[str, None]] = {}
    for edge in payload.edges:
        if edge.type != _CALLS_EDGE_TYPE:
            continue
        targets = seen.setdefault(edge.source_id, {})
        targets.setdefault(edge.target_id, None)
    return {source: tuple(targets) for source, targets in seen.items()}


def _is_ancestor(candidate: str, node: str, parent: dict[str, str | None]) -> bool:
    """Is `candidate` on the traversal path from the entry point down to
    `node` (or `node` itself)? This is what distinguishes a genuine cycle
    (an edge back to something that led to this node — self-recursion is
    the `candidate == node` case) from a merely convergent edge (two
    different branches legitimately calling the same shared component,
    which is normal DAG shape, not recursion, and must never be flagged as
    one)."""
    current: str | None = node
    while current is not None:
        if current == candidate:
            return True
        current = parent.get(current)
    return False


def _build_one_chain(
    entry_point: str, adjacency: dict[str, tuple[str, ...]], *, max_depth: int
) -> CallChain:
    """Breadth-first traversal from `entry_point`, each component visited at
    most once (its shallowest reachable depth) — the standard, non-
    recursive way to make a cycle structurally unable to loop forever: a
    node already in `visited` is never re-enqueued, so the queue is
    strictly finite regardless of how the underlying edges are shaped.

    Revisiting an already-visited node is only recorded as a step, and only
    counts toward `cycle_detected`, when the revisited node is a genuine
    ancestor of the current one (`_is_ancestor`) — a back-edge. A revisit
    that is merely a second, independent path converging on a node already
    reached by a shorter path is real graph shape, not a cycle, and is
    silently not re-recorded: that node's one true shortest-path edge into
    it already appears in `steps`.
    """
    steps: list[CallStep] = []
    visited: dict[str, None] = {entry_point: None}
    parent: dict[str, str | None] = {entry_point: None}
    truncated = False
    cycle_detected = False

    queue: deque[tuple[str, int]] = deque([(entry_point, 0)])
    while queue:
        node, depth = queue.popleft()
        for target in adjacency.get(node, ()):
            if target in visited:
                if _is_ancestor(target, node, parent):
                    cycle_detected = True
                    steps.append(CallStep(source=node, target=target, depth=depth + 1))
                # else: a convergent edge onto an already-reached node —
                # real graph shape, not a cycle; not re-recorded.
                continue
            if depth >= max_depth:
                truncated = True
                # A real edge exists past the boundary; record that it was
                # seen, but never expand past it.
                visited[target] = None
                parent[target] = node
                steps.append(CallStep(source=node, target=target, depth=depth + 1))
                continue
            visited[target] = None
            parent[target] = node
            steps.append(CallStep(source=node, target=target, depth=depth + 1))
            queue.append((target, depth + 1))

    terminal_operations = tuple(node for node in visited if not adjacency.get(node))

    return CallChain(
        entry_point=entry_point,
        steps=tuple(steps),
        terminal_operations=terminal_operations,
        truncated=truncated,
        cycle_detected=cycle_detected,
    )


def build_call_chains(
    payload: GraphPayload,
    entry_points: Sequence[str],
    *,
    max_depth: int = 5,
) -> tuple[CallChain, ...]:
    """One `CallChain` per entry point, in the same order as `entry_points`.

    Pure: does not read or write anything beyond `payload` and its
    arguments, never mutates `payload`, holds no state between calls. An
    entry point with no outgoing `CALLS` edges at all (disconnected, or the
    payload has none) produces an honest empty chain — `steps=()`,
    `terminal_operations=(entry_point,)` — never an error and never a
    fabricated one.
    """
    adjacency = _calls_adjacency(payload)
    return tuple(
        _build_one_chain(entry_point, adjacency, max_depth=max_depth)
        for entry_point in entry_points
    )
