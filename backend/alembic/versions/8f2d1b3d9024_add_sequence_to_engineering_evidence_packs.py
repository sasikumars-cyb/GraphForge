"""add sequence to engineering_evidence_packs (KAN-24)

Revision ID: 8f2d1b3d9024
Revises: e3fd40c111cf
Create Date: 2026-08-07 00:00:00.000000

Adds a monotonic `sequence` identity column so evidence-pack retention
pruning (`EngineeringMemoryRepository.prune_evidence_packs`) has a real
"newest N" ordering to prune against — `created_at` cannot serve that role
(Postgres's `now()` is transaction-scoped, not per-statement; see
`app.models.engineering_evidence_pack`'s own column comment and
`app.models.knowledge_relationship`'s module docstring, which fixed the
identical problem the same way for that sibling table).

Purely additive: no existing column changes, no data migration, no row
touched. Parented on `e3fd40c111cf` (the revision this dev database is
actually stamped at) rather than `d600ead2cff3` — the two are pre-existing,
already-unmerged heads from a `background_jobs` table that was created via
`Base.metadata.create_all` and never actually run through `alembic upgrade`
(confirmed: the table exists with live data, but `alembic_version` was
never advanced past `e3fd40c111cf`). Reconciling that drift is a separate
concern from this ticket and out of scope here.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f2d1b3d9024"
down_revision: str | None = "e3fd40c111cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "engineering_evidence_packs",
        sa.Column(
            "sequence",
            sa.Integer(),
            sa.Identity(always=True),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_engineering_evidence_packs_sequence", "engineering_evidence_packs", ["sequence"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_engineering_evidence_packs_sequence", "engineering_evidence_packs", type_="unique"
    )
    op.drop_column("engineering_evidence_packs", "sequence")
