"""Test-vs-production classification for indexed components.

The problem this closes: nothing in the graph itself recorded whether a
`Class`/`Function`/`Controller`/... node was test code or production code.
Every consumer that cared (today, only `app.agents.planning.tools`'s
`_is_test_component`) recomputed a private guess from `file_path`/`name` at
read time, so Development/Testing/Documentation Planning — which read the
same `graph_components` data but never imported that private helper — had
no way to know a component they were about to build a plan around was a
pytest test class, not the production code it exercises. A real run named
`TestSCDType2Merger`/`TestExactDeduplicator` (test classes in
`tests/unit/test_scd2.py`/`tests/unit/test_dedup.py`) as if they were the
production `SCDType2Merger`/`ExactDeduplicator` implementations throughout
Planning, Development, Testing, and Documentation Planning — verification
never caught it because those test classes are real, indexed components;
"does this exist" and "is this the production code, not its test" are
different questions, and only the former was ever checked.

This module is the one place `is_test` is decided, at index time, so it is
computed once and stored as a real node property — every consumer (graph
queries, ranking, verification, the UI) reads the same answer instead of
each maintaining its own regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Path-based signal: the strongest evidence a file is test code, because it
# reflects how the file is actually organized/run (pytest collection,
# Maven/Gradle `src/test/...` convention), not just a naming coincidence.
# Promoted from `app.agents.planning.tools._TEST_PATH_RE` — same pattern,
# now shared instead of private to one ranking function.
_TEST_PATH_RE = re.compile(r"(^|/)tests?(/|_)|(^|/)test_|_test\.[a-zA-Z0-9]+$|(^|/)conftest\.py$")

# Name-based signal: a class/function whose own name is test-shaped
# (`TestFoo`, `test_foo`, `FooTest`) even when the path pattern above
# doesn't independently confirm it — e.g. a test class defined in a module
# whose path doesn't match `_TEST_PATH_RE` (unusual, but not impossible: a
# non-standard repository layout, or a helper module under `tests/`'s
# parent that itself doesn't match `tests?/`). Name alone is weaker
# evidence than path — a production class can legitimately be named
# `TestConnectionPool` (a connection pool *for* tests) without being test
# code itself — so this is a name of "did the name look test-shaped", not
# "is this definitely a test", and confidence is scored accordingly below.
_TEST_NAME_RE = re.compile(r"^(Test|test_)[A-Za-z0-9_]*$|^[A-Za-z0-9_]*Test$")


@dataclass(frozen=True)
class Classification:
    """The classification recorded on a component node.

    `confidence` is this classification's own confidence in the `is_test`
    verdict specifically (1.0 = path convention confirmed it, 0.55 = name
    shape alone suggested it with no path corroboration, 0.05 = neither
    signal fired — i.e. "confidently production", not "unknown") — not a
    general-purpose quality score for the component.
    """

    is_test: bool
    confidence: float
    symbol_type: str


def classify_is_test(file_path: str, name: str) -> tuple[bool, float]:
    """Whether a component at `file_path` named `name` is test code, and
    how confident that verdict is.

    Both signals independently sufficient to call it a test (matching
    the codebase's existing precedent — `_is_test_component` used `or`,
    not `and`, and changing that would silently stop flagging real test
    files whose class name doesn't happen to start with `Test`), but the
    two together get materially higher confidence than either alone.
    """
    path_match = bool(_TEST_PATH_RE.search(file_path or ""))
    bare_name = (name or "").rsplit(".", 1)[-1]
    name_match = bool(_TEST_NAME_RE.match(bare_name))

    if path_match and name_match:
        return True, 1.0
    if path_match:
        # The file itself is organized as a test file (tests/, test_*.py,
        # ...); the specific symbol's name just doesn't happen to start
        # with Test/test_ — still test code, e.g. a bare helper function
        # in a test module. High confidence: the path convention is the
        # stronger signal of the two.
        return True, 0.9
    if name_match:
        # Name alone, no path corroboration. Real but weaker evidence —
        # see the module docstring's `TestConnectionPool` example.
        return True, 0.55
    return False, 0.95


def symbol_type_for(labels: list[str], class_name: str | None) -> str:
    """A finer-grained role than the coarse graph label: distinguishes a
    bare function from a class's method (both are `Function`-labeled
    nodes today), and gives Controller/Service/FeignClient/Class/Function
    a single lowercase vocabulary consumers can switch on without
    re-deriving it from `labels` themselves."""
    if "Function" in labels:
        return "method" if class_name else "function"
    if "Class" in labels:
        return "class"
    if "Controller" in labels:
        return "controller"
    if "Service" in labels:
        return "service"
    if "FeignClient" in labels:
        return "feign_client"
    if "Module" in labels:
        return "module"
    return "component"


def classify(
    *, file_path: str, name: str, labels: list[str], class_name: str | None = None
) -> Classification:
    """The single entry point `app.indexer.graph.builder` calls for every
    Component-labeled node it creates, of any language/kind."""
    is_test, confidence = classify_is_test(file_path, name)
    return Classification(
        is_test=is_test,
        confidence=confidence,
        symbol_type=symbol_type_for(labels, class_name),
    )


def production_sibling_name(test_name: str) -> str | None:
    """The production class/function name a test-shaped name most likely
    exercises, by stripping the `Test`/`test_` marker — `TestSCDType2Merger`
    -> `SCDType2Merger`, `test_exact_dedup` -> `exact_dedup`, `FooTest` ->
    `Foo`. Returns None when the name isn't test-shaped at all (nothing to
    strip), so callers can tell "not a test name" apart from "test name
    with no discoverable sibling" (the latter still returns the stripped
    string — whether that sibling actually *exists* is for the caller to
    check against real evidence, this is pure string transformation, no
    graph lookup).
    """
    bare = (test_name or "").rsplit(".", 1)[-1]
    if bare.startswith("Test") and len(bare) > 4:
        return bare[len("Test") :]
    if bare.startswith("test_") and len(bare) > 5:
        return bare[len("test_") :]
    if bare.endswith("Test") and len(bare) > 4:
        return bare[: -len("Test")]
    if bare.endswith("_test") and len(bare) > 5:
        return bare[: -len("_test")]
    return None
