"""`OnCompleteSpec` — a JSON-safe stand-in for `background_execution
.OnComplete`, so a queued job's post-completion bookkeeping can cross the
`background_jobs` table and be reconstructed by whichever worker eventually
claims the job, instead of requiring the original in-memory closure to still
exist.

Every `OnComplete` this codebase has ever constructed captures exactly one
UUID and delegates to a named function
(`app.api.v1.routers.workflows._workflow_stage_finalizer`/
`_report_finalizer` — verified by grep, not assumed: nothing else in the
codebase returns `OnComplete`). That made this translation possible without
inventing a general closure-serialization mechanism: `kind` names which of
the two, `target_id` is the one UUID each of them closes over. Adding a
third kind of `on_complete` in the future means adding one arm to
`resolve()` below, deliberately, rather than the queue silently accepting an
`OnComplete` it has no way to reconstruct.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.orchestrator.background_execution import OnComplete

OnCompleteKind = Literal["workflow_stage_finalizer", "report_finalizer"]


@dataclass(frozen=True)
class OnCompleteSpec:
    kind: OnCompleteKind
    target_id: uuid.UUID

    def to_payload(self) -> dict[str, str]:
        return {"kind": self.kind, "target_id": str(self.target_id)}

    @staticmethod
    def from_payload(payload: dict[str, object] | None) -> OnCompleteSpec | None:
        if payload is None:
            return None
        kind = payload["kind"]
        if kind not in ("workflow_stage_finalizer", "report_finalizer"):
            raise ValueError(f"Unknown OnCompleteSpec kind: {kind!r}")
        return OnCompleteSpec(kind=kind, target_id=uuid.UUID(str(payload["target_id"])))


def resolve(spec: OnCompleteSpec | None) -> OnComplete | None:
    """Reconstruct the live closure a `spec` describes. Imports the
    finalizer builders from `app.api.v1.routers.workflows` lazily (inside
    the function body, not at module load) to avoid a circular import —
    that module already imports `schedule_run_execution` from
    `background_execution.py` the same way, at call time rather than at
    import time, for the same reason."""
    if spec is None:
        return None

    from app.api.v1.routers.workflows import _report_finalizer, _workflow_stage_finalizer

    if spec.kind == "workflow_stage_finalizer":
        return _workflow_stage_finalizer(spec.target_id)
    return _report_finalizer(spec.target_id)
