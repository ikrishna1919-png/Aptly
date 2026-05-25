"""add salary to jobs

Revision ID: 0003_add_salary
Revises: 0002_extend_jobs
Create Date: 2026-05-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_add_salary"
down_revision: str | None = "0002_extend_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("salary", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "salary")
