"""Test-vs-production component grounding — shared by Planning,
Development, Testing, and Documentation Planning.

This is the check `app.agents.verification.verify_claims` cannot do:
`verify_claims` answers "does this claim exist in this run's own
evidence" — a pytest test class is real, indexed evidence, so a claim
naming one passes that check every time. It never asks "is the thing
being named actually the production implementation, or just its test
double" — which is the question that mattered in a real run that planned
an entire fix around `TestSCDType2Merger`/`TestExactDeduplicator` (test
classes) instead of the production `SCDType2Merger`/`ExactDeduplicator`
they exercise, and had every downstream stage repeat the same names
without independently re-checking them.

Each of the four agents calls `check_test_used_as_production` itself,
against the same `graph_components` data each of them already reads (see
each agent's own module docstring on reading Context Discovery's stored
result via `get_stage_result()`) — this is what makes grounding
independent per stage rather than Development/Testing/Documentation
Planning trusting Planning's own verification_warnings text: a stage that
skips this call, or whose author later removes it, loses its own
grounding without silently inheriting anyone else's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.agents._contract import ComponentWarning as ContractComponentWarning
from app.agents.normalization import normalize_text
from app.indexer.classification import production_sibling_name

# Task text that names testing as the actual subject of the work, not an
# incidental mention of the word "test" (e.g. "SCD2 merge... test
# coverage" in a bug report about the merge itself). Deliberately
# conservative: false negatives (treating a genuinely test-focused task as
# production-only) are the safe failure mode here, not false positives —
# see `is_task_test_related`'s docstring.
_TEST_TASK_RE = re.compile(
    r"\b(add|write|fix|update|improve|increase|create)\b[^.\n]{0,40}\btests?\b"
    r"|\bflaky\s+tests?\b"
    r"|\btest\s+coverage\s+(for|of)\b"
    r"|\bunit\s+tests?\s+(for|of|are|is)\b",
    re.IGNORECASE,
)


def is_task_test_related(task_text: str) -> bool:
    """Whether the task itself is genuinely about writing/fixing tests,
    in which case naming test classes as the thing to change is correct,
    not a mistake — a task like "add regression tests for the SCD2 merge"
    should be planned against the test files, and this check must not
    fight that.

    Deliberately narrow, matching this codebase's other heuristic checks
    (see `app.agents.verification.check_entity_mismatch`): a task that
    merely mentions "test" once (e.g. a bug report's "no test covers
    this") is NOT test-related by this definition — the fix under
    discussion is still production code. Only phrasing that names testing
    as the actual deliverable counts.
    """
    return bool(_TEST_TASK_RE.search(task_text or ""))


@dataclass(frozen=True)
class ComponentWarning:
    """A structured verification finding — distinct from the free-text
    `verification_warnings: list[str]` every agent already returns (kept,
    unchanged, for backward compatibility with existing consumers of that
    field). `warning_type` lets a caller (the UI, a future automated gate)
    branch on the *kind* of problem without parsing prose.
    """

    claim: str
    warning_type: str  # "test_used_as_production" | "nonexistent_component" | "nonexistent_file"
    message: str
    suggested_replacement: str | None = None


def _components_by_name(components: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for component in components:
        name = str(component.get("name", ""))
        if name:
            by_name.setdefault(normalize_text(name), []).append(component)
    return by_name


def check_test_used_as_production(
    claims: list[str],
    components: list[dict[str, Any]],
    task_text: str,
) -> tuple[list[str], list[ComponentWarning]]:
    """Check each claim against this run's own indexed components for the
    test-used-as-production failure mode, independent of whether the
    claim exists at all (that's `verify_claims`'s job — this assumes
    existence and asks a different question).

    Returns `(corrected_claims, warnings)`:

    - A claim that names something not indexed here at all, or that
      matches at least one *production* component sharing that exact
      name, passes through unchanged — this function only acts when
      EVERY component this run indexed under that exact name is
      test-classified.
    - When every match is test code and a same-repository production
      sibling exists (`SCDType2Merger` for `TestSCDType2Merger`, found via
      `app.indexer.classification.production_sibling_name`), the claim is
      replaced with the real name — this is the "reject" requirement:
      the test-only claim never survives into the corrected list.
    - When every match is test code and no production sibling is
      indexed, the claim is dropped entirely (rejected with no
      replacement) rather than left in place.

    A task genuinely about tests (`is_task_test_related`) exempts every
    claim from this check — naming test classes is then correct, not a
    mistake.
    """
    if is_task_test_related(task_text):
        return list(claims), []

    by_name = _components_by_name(components)
    corrected: list[str] = []
    warnings: list[ComponentWarning] = []

    for claim in claims:
        if not claim:
            continue
        matches = by_name.get(normalize_text(claim), [])
        if not matches or any(not m.get("is_test") for m in matches):
            # Nothing indexed under this name (existence is verify_claims's
            # job), or at least one production component shares it — no
            # test/production confusion to flag.
            corrected.append(claim)
            continue

        sibling_name = production_sibling_name(claim)
        sibling_matches = by_name.get(normalize_text(sibling_name), []) if sibling_name else []
        production_sibling = next((m for m in sibling_matches if not m.get("is_test")), None)

        if production_sibling is not None:
            real_name = str(production_sibling["name"])
            corrected.append(real_name)
            warnings.append(
                ComponentWarning(
                    claim=claim,
                    warning_type="test_used_as_production",
                    message=(
                        f"'{claim}' is a test class/function, not production code — "
                        f"replaced with '{real_name}', the real implementation this run's "
                        "own graph traversal confirmed exists."
                    ),
                    suggested_replacement=real_name,
                )
            )
        else:
            warnings.append(
                ComponentWarning(
                    claim=claim,
                    warning_type="test_used_as_production",
                    message=(
                        f"'{claim}' is a test class/function, not production code, and no "
                        "corresponding production component was found in this run's "
                        "indexed graph data — removed from the affected components list. "
                        "The production code this test exercises may not be indexed, or "
                        "may be named differently than the test's own name suggests."
                    ),
                    suggested_replacement=None,
                )
            )
            # Rejected: deliberately not appended to `corrected`.

    return corrected, warnings


def to_contract_warnings(warnings: list[ComponentWarning]) -> list[ContractComponentWarning]:
    """Convert this module's plain dataclasses to the pydantic
    `ComponentWarning` every agent's result schema stores — kept as a
    dataclass here so this module (pure logic, no I/O) doesn't need a
    pydantic/schema dependency of its own."""
    return [
        ContractComponentWarning(
            claim=w.claim,
            warning_type=w.warning_type,
            message=w.message,
            suggested_replacement=w.suggested_replacement,
        )
        for w in warnings
    ]
