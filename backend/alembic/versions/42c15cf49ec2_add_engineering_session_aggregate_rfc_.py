"""add engineering session aggregate (RFC-001)

Revision ID: 42c15cf49ec2
Revises: e536c0f5976b
Create Date: 2026-07-31 19:32:21.574380

RFC-001 — Architecture v2.1 §2.2 Engineering Session aggregate:
Participant, EngineeringSession, TimelineEntry, EngineeringArtifact (the
joined-table-inheritance base for Belief, Hypothesis, Evidence, Decision,
Recommendation, Contradiction), and ContradictionParty (the N-ary
disputing-party join table). See app/models/engineering_artifact.py and
RFC-001.md for the full reasoning behind the table shapes.

Table creation order is significant and intentional, not alphabetical:
participants -> engineering_sessions -> engineering_artifacts (base) ->
timeline_entries -> beliefs -> evidence -> hypotheses -> recommendations
-> decisions -> contradictions -> contradiction_parties. Each table is
created only after every table its foreign keys reference already
exists; see decision.py's module docstring for why
`recommendations.target_contradiction_id` is deliberately unconstrained
rather than forcing a circular dependency.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "42c15cf49ec2"
down_revision: str | None = "e536c0f5976b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "participants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("agent_role", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'human' AND user_id IS NOT NULL AND agent_role IS NULL) OR "
            "(kind = 'agent' AND agent_role IS NOT NULL AND user_id IS NULL)",
            name="ck_participants_kind_shape",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_participants_user_id"), "participants", ["user_id"], unique=False
    )

    op.create_table(
        "engineering_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="orienting", nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_participant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_participant_id"], ["participants.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_engineering_sessions_mission_id"),
        "engineering_sessions",
        ["mission_id"],
        unique=False,
    )

    op.create_table(
        "engineering_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("retention_state", sa.String(length=16), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "retention_state IN ('active', 'legal_hold', 'redacted')",
            name="ck_engineering_artifacts_retention_state",
        ),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["engineering_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_engineering_artifacts_participant_id"),
        "engineering_artifacts",
        ["participant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engineering_artifacts_session_id"),
        "engineering_artifacts",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "timeline_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["engineering_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "sequence", name="uq_timeline_entries_session_sequence"
        ),
    )
    op.create_index(
        op.f("ix_timeline_entries_artifact_id"), "timeline_entries", ["artifact_id"], unique=False
    )
    op.create_index(
        op.f("ix_timeline_entries_participant_id"),
        "timeline_entries",
        ["participant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_timeline_entries_session_id"), "timeline_entries", ["session_id"], unique=False
    )

    op.create_table(
        "beliefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="formed", nullable=False),
        sa.ForeignKeyConstraint(["id"], ["engineering_artifacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["id"], ["engineering_artifacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evidence_evidence_kind"), "evidence", ["evidence_kind"], unique=False)

    op.create_table(
        "hypotheses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="proposed", nullable=False),
        sa.Column("resolved_belief_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["engineering_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_belief_id"], ["beliefs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="proposed", nullable=False),
        sa.Column("target_belief_id", sa.Uuid(), nullable=True),
        sa.Column("target_contradiction_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["engineering_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_belief_id"], ["beliefs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("decision_kind", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("committed_by_participant_id", sa.Uuid(), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_decision_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["committed_by_participant_id"], ["participants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["id"], ["engineering_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["recommendations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_decision_id"], ["decisions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_decisions_decision_kind"), "decisions", ["decision_kind"], unique=False
    )

    op.create_table(
        "contradictions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="detected", nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by_decision_id", sa.Uuid(), nullable=True),
        sa.Column("owner_scope", sa.String(length=16), server_default="session", nullable=False),
        sa.CheckConstraint("owner_scope IN ('session')", name="ck_contradictions_owner_scope"),
        sa.ForeignKeyConstraint(["id"], ["engineering_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["resolved_by_decision_id"], ["decisions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "contradiction_parties",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contradiction_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["engineering_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["contradiction_id"], ["contradictions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contradiction_id", "artifact_id", name="uq_contradiction_parties_pair"
        ),
    )
    op.create_index(
        op.f("ix_contradiction_parties_artifact_id"),
        "contradiction_parties",
        ["artifact_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contradiction_parties_contradiction_id"),
        "contradiction_parties",
        ["contradiction_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_contradiction_parties_contradiction_id"), table_name="contradiction_parties"
    )
    op.drop_index(op.f("ix_contradiction_parties_artifact_id"), table_name="contradiction_parties")
    op.drop_table("contradiction_parties")
    op.drop_table("contradictions")
    op.drop_index(op.f("ix_decisions_decision_kind"), table_name="decisions")
    op.drop_table("decisions")
    op.drop_table("recommendations")
    op.drop_table("hypotheses")
    op.drop_index(op.f("ix_evidence_evidence_kind"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_table("beliefs")
    op.drop_index(op.f("ix_timeline_entries_session_id"), table_name="timeline_entries")
    op.drop_index(op.f("ix_timeline_entries_participant_id"), table_name="timeline_entries")
    op.drop_index(op.f("ix_timeline_entries_artifact_id"), table_name="timeline_entries")
    op.drop_table("timeline_entries")
    op.drop_index(op.f("ix_engineering_artifacts_session_id"), table_name="engineering_artifacts")
    op.drop_index(
        op.f("ix_engineering_artifacts_participant_id"), table_name="engineering_artifacts"
    )
    op.drop_table("engineering_artifacts")
    op.drop_index(op.f("ix_engineering_sessions_mission_id"), table_name="engineering_sessions")
    op.drop_table("engineering_sessions")
    op.drop_index(op.f("ix_participants_user_id"), table_name="participants")
    op.drop_table("participants")
