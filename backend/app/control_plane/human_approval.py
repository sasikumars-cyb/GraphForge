"""Human Approval — distinct from Execution Authorization.

Engineering State contract §13: "Human Approval — a specific, immutable,
content-hash-pinned authorization of a specific Plan/Scope, recorded at a
specific point in time. It MUST NOT be edited after the fact... Execution
Authorization ... MUST be computed fresh, immediately before the Action,
as: Human Approval (pinned, historical) AND Policy (current) AND Safety
Validity (current)."

This module models ONLY the Human Approval record and its staleness
check. It never grants Execution Authorization itself — `ControlPlane`
combines this with Policy and Safety Validity at the final gate,
per §13's formula, and this module has no knowledge of either.

Phase 3 scope: `query_knowledge_graph`'s declared `required_authorization
= "none"` (see `app.capabilities.setup`), so the representative Action's
own final gate does not require a Human Approval record to be present at
all — this module exists and is fully real (not stubbed) so a FUTURE
Capability that DOES declare `required_authorization` has a real
mechanism to bind to, but Phase 3's own integration tests exercise both
the "no approval required" path and the "approval present and pinned"
path explicitly, so this isn't dead code shipped untested.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HumanApprovalRecord:
    """Immutable once constructed — no setter exists, and callers are
    expected to construct a NEW record (never mutate) if a human
    re-approves a changed scope, exactly as §13 requires ("MUST NOT be
    edited after the fact")."""

    approval_id: str
    approved_scope: dict[str, Any]
    approved_by: str
    approved_at: str  # ISO-8601

    def __post_init__(self) -> None:
        if not self.approval_id.strip():
            raise ValueError("HumanApprovalRecord.approval_id must be non-empty.")
        if not self.approved_by.strip():
            raise ValueError("HumanApprovalRecord.approved_by must be non-empty.")

    @property
    def content_hash(self) -> str:
        """The "content-hash-pinned" identity §13 requires — computed from
        the approved scope itself, so a Grant recording this hash proves
        exactly what was approved, not merely that SOME approval by this
        id once existed (which could otherwise be reattached to a
        different scope by a bug elsewhere)."""
        canonical = json.dumps(
            {"scope": self.approved_scope, "approved_by": self.approved_by},
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_approval_still_valid_for_scope(
    approval: HumanApprovalRecord, *, current_scope: dict[str, Any]
) -> bool:
    """§13: "Approved scope changes (discovered mid-execution to differ
    from what was approved) MUST cause Execution Authorization to fail for
    the affected delta." This is a pure scope-equality check — it does
    NOT evaluate time-based staleness (that is a Policy-defined threshold,
    Cap §20, not something this content-hash module can decide on its
    own) and does NOT evaluate Safety Validity (a separate concern,
    `app.control_plane.safety`). `ControlPlane` combines all three per
    §13's formula; this function answers exactly one of them.
    """
    pinned = HumanApprovalRecord(
        approval_id=approval.approval_id,
        approved_scope=current_scope,
        approved_by=approval.approved_by,
        approved_at=approval.approved_at,
    )
    return pinned.content_hash == approval.content_hash


__all__ = ["HumanApprovalRecord", "is_approval_still_valid_for_scope"]
