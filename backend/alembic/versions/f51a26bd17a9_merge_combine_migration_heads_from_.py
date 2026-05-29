"""merge: combine migration heads from parallel PRs

Revision ID: f51a26bd17a9
Revises: 0018_seed_more_sources, 0018_tailor_runs
Create Date: 2026-05-29 00:28:01.340284

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f51a26bd17a9'
down_revision: Union[str, None] = ('0018_seed_more_sources', '0018_tailor_runs')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
