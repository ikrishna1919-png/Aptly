"""add job_analyses cache table

Revision ID: 0004_job_analyses
Revises: 0003_add_salary
Create Date: 2026-05-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_job_analyses"
down_revision: str | None = "0003_add_salary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("job_id", name="uq_job_analyses_job_id"),
    )
    op.create_index("ix_job_analyses_input_hash", "job_analyses", ["input_hash"])


def downgrade() -> None:
    op.drop_index("ix_job_analyses_input_hash", table_name="job_analyses")
    op.drop_table("job_analyses")
