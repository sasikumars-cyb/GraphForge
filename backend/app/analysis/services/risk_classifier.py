"""Deterministic risk classification.

Rules (highest-precedence trigger wins):

HIGH   - a changed file is `pom.xml` (shared library/dependency change);
         OR a directly-changed node produces to or consumes from a Kafka
         topic (schema changes have no compiler-enforced contract, so any
         touch is conservatively treated as a possible schema change);
         OR a directly-changed node is a `FeignClient` (this service's own
         declared calling contract against an external API - a "breaking
         public API change" candidate).
MEDIUM - a directly-changed node is a `Controller` or `Service` (the
         REST API / service contract surface, per the graph's own
         vocabulary).
LOW    - none of the above - the changed files don't touch any discovered
         architecture surface at all (DTOs, config, tests, ...).

See ADR 0008 for why a Controller change is MEDIUM rather than LOW: with
only a single indexed graph snapshot (not a before/after diff of the
endpoint set), a Controller *is* the REST API in this graph's vocabulary,
so the two literal user-facing categories can't both be honored - this was
confirmed with the user as the deliberate interpretation.
"""

from app.analysis.models.impact import RiskLevel
from app.graph.models import GraphNode

_MEDIUM_LABELS = frozenset({"Controller", "Service"})


def classify_risk(
    directly_impacted_services: list[GraphNode],
    *,
    pom_changed: bool,
    topics_touched: bool,
) -> RiskLevel:
    if pom_changed or topics_touched:
        return RiskLevel.HIGH
    if any("FeignClient" in node.labels for node in directly_impacted_services):
        return RiskLevel.HIGH
    if any(_MEDIUM_LABELS & set(node.labels) for node in directly_impacted_services):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW
