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
from app.models.confidence_calibration import ConfidenceCalibration
from app.models.user import User

router = APIRouter(prefix="/calibration", tags=["calibration"])

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


class AgentCalibration(BaseModel):
    agent_id: str
    total_decisions: int
    approval_rate: float
    avg_confidence: float
    buckets: list[BucketStat]


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
    result = await db.execute(
        select(ConfidenceCalibration).order_by(ConfidenceCalibration.decided_at.desc()).limit(5000)
    )
    rows = list(result.scalars().all())

    by_agent: dict[str, list[ConfidenceCalibration]] = defaultdict(list)
    for row in rows:
        by_agent[row.agent_id].append(row)

    agents: list[AgentCalibration] = []
    for agent_id, agent_rows in sorted(by_agent.items()):
        total = len(agent_rows)
        approved = sum(1 for r in agent_rows if r.decision == "approved")
        avg_confidence = sum(r.confidence_score for r in agent_rows) / total

        bucket_rows: dict[str, list[ConfidenceCalibration]] = defaultdict(list)
        for r in agent_rows:
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

        agents.append(
            AgentCalibration(
                agent_id=agent_id,
                total_decisions=total,
                approval_rate=approved / total,
                avg_confidence=avg_confidence,
                buckets=buckets,
            )
        )

    return CalibrationSummaryResponse(agents=agents)
