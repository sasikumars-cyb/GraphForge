"""merge ai_profiles and external_context_settings heads

Revision ID: 1e9fc26fd818
Revises: 5f2c5a7d0b1d, b8e2d40a91c7
Create Date: 2026-07-25 13:16:21.945389

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e9fc26fd818'
down_revision: Union[str, None] = ('5f2c5a7d0b1d', 'b8e2d40a91c7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
