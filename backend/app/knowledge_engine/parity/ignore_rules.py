"""The configurable ignore mechanism Phase 4 requires — no field name is
hardcoded into the comparator itself (`comparator.py` never mentions
`confidence` or `graph_version` anywhere). `DEFAULT_IGNORE_RULES` encodes
today's two evidence-backed accepted differences (see this RFC's Phase 1
audit); a caller comparing graphs from a different vintage, or auditing a
gap this RFC didn't close, passes its own `IgnoreRules` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EntityKind = Literal["node", "edge"]


@dataclass(frozen=True)
class PropertyIgnoreRule:
    """Ignore one property, on one entity kind, optionally scoped to a
    specific node label or edge type. `label_or_type=None` means "every
    label/type" — the common case for a property like `confidence` that
    means something different on every edge type it appears on.
    """

    applies_to: EntityKind
    property_name: str
    label_or_type: str | None = None
    reason: str = ""

    def matches(self, kind: EntityKind, label_or_type: str) -> bool:
        if self.applies_to != kind:
            return False
        return self.label_or_type is None or self.label_or_type == label_or_type


IgnoreRules = tuple[PropertyIgnoreRule, ...]

DEFAULT_IGNORE_RULES: IgnoreRules = (
    PropertyIgnoreRule(
        applies_to="edge",
        property_name="confidence",
        label_or_type=None,
        reason=(
            "Legacy writes a 2-value match-method vocabulary "
            "('structural'/'heuristic', cross_repo_linker.py); the "
            "Materializer writes a 6-value ConfidenceState "
            "(materializer.py). Same property key, unrelated meaning — "
            "never comparable, not a defect."
        ),
    ),
    PropertyIgnoreRule(
        applies_to="edge",
        property_name="computed_at",
        label_or_type=None,
        reason=(
            "Stamped by legacy cross_repo_linker.py's compute_edges only; "
            "no evidence item captures it today, so the Materializer "
            "cannot reproduce it yet. Known, unclosed gap (Production "
            "Cutover RFC Phase 1) — excluded here so the parity report "
            "stays actionable instead of permanently red on a gap this "
            "RFC did not fix."
        ),
    ),
    PropertyIgnoreRule(
        applies_to="edge",
        property_name="source_graph_version",
        label_or_type=None,
        reason="Same unclosed gap as computed_at — see that rule's reason.",
    ),
    PropertyIgnoreRule(
        applies_to="edge",
        property_name="target_graph_version",
        label_or_type=None,
        reason="Same unclosed gap as computed_at — see that rule's reason.",
    ),
)


def filter_properties(
    kind: EntityKind,
    label_or_type: str,
    properties: dict[str, object],
    ignore_rules: IgnoreRules,
) -> dict[str, object]:
    """Properties with every ignored key removed — the only thing the
    comparator ever diffs. Deterministic: same inputs, same output, no
    dependency on dict iteration order (returns a plain dict comprehension
    over the input's own keys)."""
    ignored_keys = {
        rule.property_name for rule in ignore_rules if rule.matches(kind, label_or_type)
    }
    return {key: value for key, value in properties.items() if key not in ignored_keys}
