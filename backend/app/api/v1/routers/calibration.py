"""Confidence calibration API — admin-only view of whether confidence
scores actually track human approve/reject decisions.

See app.models.confidence_calibration for why this exists: ROADMAP.md's
risk register treats this as a hard blocker past Phase 2 ("Confidence
scores become decorative (unchecked against outcomes)").
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.database.session import get_db_session
from app.models.agent_step import AgentStep
from app.models.confidence_calibration import ConfidenceCalibration
from app.models.user import User

router = APIRouter(prefix="/calibration", tags=["calibration"])

# KAN-23 — a prompt version is only worth flagging once it has enough
# decisions to say something real; below this, one unlucky rejection can
# swing an approval rate by 50 points on pure noise.
_MIN_DECISIONS_FOR_FLAGGING = 5

# A prompt version's approval rate diverging from its own agent's overall
# rate by more than this many percentage points is flagged as
# systematically miscalibrated - the confidence score for that version is
# tracking something other than what actually gets approved. Not derived
# from any measured baseline (none exists yet - see KAN-25's precedent of
# not inventing thresholds without data); documented here as a starting
# point to revisit once real multi-version data exists to tune it against.
_MISCALIBRATION_THRESHOLD = 0.20

# Confidence is a 0.0-1.0 score; bucketing it is what turns raw rows into
# an actual calibration curve (does "0.85-1.0 confidence" really approve
# more often than "0.0-0.5"?) instead of just a table of numbers.
_BUCKETS: list[tuple[float, float, str]] = [
    (0.0, 0.5, "0.0 - 0.5"),
    (0.5, 0.7, "0.5 - 0.7"),
    (0.7, 0.85, "0.7 - 0.85"),
    (0.85, 1.01, "0.85 - 1.0"),
]


def _bucket_label(score: float) -> str:
    for lo, hi, label in _BUCKETS:
        if lo <= score < hi:
            return label
    return _BUCKETS[-1][2]


class BucketStat(BaseModel):
    bucket: str
    total: int
    approved: int
    approval_rate: float


class PromptVersionStat(BaseModel):
    prompt_version: str
    total: int
    approved: int
    approval_rate: float
    avg_confidence: float
    # True when this version's approval rate diverges from its agent's
    # overall approval rate by more than _MISCALIBRATION_THRESHOLD, with
    # enough decisions (_MIN_DECISIONS_FOR_FLAGGING) for that divergence
    # to mean something - see module-level docstrings for both constants.
    flagged_miscalibrated: bool


class AgentCalibration(BaseModel):
    agent_id: str
    total_decisions: int
    approval_rate: float
    avg_confidence: float
    buckets: list[BucketStat]
    by_prompt_version: list[PromptVersionStat]


class CalibrationSummaryResponse(BaseModel):
    agents: list[AgentCalibration]


@router.get("/summary", response_model=CalibrationSummaryResponse)
async def get_calibration_summary(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> CalibrationSummaryResponse:
    """Per-agent calibration: does this agent's confidence_score actually
    predict whether a human approves the workflow it's part of?

    A well-calibrated agent's high-confidence bucket should have a
    materially higher approval rate than its low-confidence bucket — if
    they're roughly equal, the score isn't carrying real information yet.
    """
    # One row per completed workflow-stage-run decision — grows slower than
    # most tables here, but still unbounded with no cap at all. Ordered
    # most-recent-first and capped so calibration reflects recent agent
    # behavior (what you actually want to know — "is this agent well
    # calibrated *now*") rather than an ever-growing full-history pull that
    # degrades this endpoint's latency/memory as the table grows. A rolling
    # time-window aggregation would be the more correct long-term fix if
    # this cap is ever actually reached in practice.
    # Joined to AgentStep on (run_id, agent_id) to recover prompt_version —
    # ConfidenceCalibration itself doesn't carry it, only the step that
    # produced the decision does. LEFT OUTER so a calibration row from a
    # run/agent pairing with no matching step (shouldn't happen, but not
    # guaranteed by a DB constraint) still contributes to the agent's
    # overall stats instead of silently vanishing from the summary.
    result = await db.execute(
        select(ConfidenceCalibration, AgentStep.prompt_version)
        .outerjoin(
            AgentStep,
            (AgentStep.run_id == ConfidenceCalibration.run_id)
            & (AgentStep.agent_id == ConfidenceCalibration.agent_id),
        )
        .order_by(ConfidenceCalibration.decided_at.desc())
        .limit(5000)
    )
    # AgentStep.prompt_version is itself non-nullable, but the outerjoin
    # above can still produce None here when a calibration row has no
    # matching step — mypy's inferred column type doesn't reflect that.
    rows: list[tuple[ConfidenceCalibration, str | None]] = list(result.all())  # type: ignore[arg-type]

    by_agent: dict[str, list[tuple[ConfidenceCalibration, str | None]]] = defaultdict(list)
    for row, prompt_version in rows:
        by_agent[row.agent_id].append((row, prompt_version))

    agents: list[AgentCalibration] = []
    for agent_id, agent_rows in sorted(by_agent.items()):
        total = len(agent_rows)
        approved = sum(1 for r, _pv in agent_rows if r.decision == "approved")
        avg_confidence = sum(r.confidence_score for r, _pv in agent_rows) / total
        agent_approval_rate = approved / total

        bucket_rows: dict[str, list[ConfidenceCalibration]] = defaultdict(list)
        for r, _pv in agent_rows:
            bucket_rows[_bucket_label(r.confidence_score)].append(r)

        buckets = [
            BucketStat(
                bucket=label,
                total=len(bucket_rows[label]),
                approved=sum(1 for r in bucket_rows[label] if r.decision == "approved"),
                approval_rate=(
                    sum(1 for r in bucket_rows[label] if r.decision == "approved")
                    / len(bucket_rows[label])
                    if bucket_rows[label]
                    else 0.0
                ),
            )
            for _, _, label in _BUCKETS
            if bucket_rows[label]
        ]

        # Rows with no matching AgentStep (see the outer-join comment above)
        # have no prompt_version to group by and are excluded here — they
        # still count toward the agent's overall stats above, just not
        # toward any specific version's breakdown.
        version_rows: dict[str, list[ConfidenceCalibration]] = defaultdict(list)
        for r, prompt_version in agent_rows:
            if prompt_version is not None:
                version_rows[prompt_version].append(r)

        by_prompt_version = []
        for version, v_rows in sorted(version_rows.items()):
            v_total = len(v_rows)
            v_approved = sum(1 for r in v_rows if r.decision == "approved")
            v_approval_rate = v_approved / v_total
            flagged = (
                v_total >= _MIN_DECISIONS_FOR_FLAGGING
                and abs(v_approval_rate - agent_approval_rate) > _MISCALIBRATION_THRESHOLD
            )
            by_prompt_version.append(
                PromptVersionStat(
                    prompt_version=version,
                    total=v_total,
                    approved=v_approved,
                    approval_rate=v_approval_rate,
                    avg_confidence=sum(r.confidence_score for r in v_rows) / v_total,
                    flagged_miscalibrated=flagged,
                )
            )

        agents.append(
            AgentCalibration(
                agent_id=agent_id,
                total_decisions=total,
                approval_rate=agent_approval_rate,
                avg_confidence=avg_confidence,
                buckets=buckets,
                by_prompt_version=by_prompt_version,
            )
        )

    return CalibrationSummaryResponse(agents=agents)
