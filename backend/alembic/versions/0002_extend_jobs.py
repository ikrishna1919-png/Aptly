"""extend jobs table for ingestion

Revision ID: 0002_extend_jobs
Revises: 0001_create_jobs
Create Date: 2026-05-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_extend_jobs"
down_revision: str | None = "0001_create_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("remote", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("employment_type", sa.String(length=64), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("skills", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column("jobs", sa.Column("sponsors_visa", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_content_hash", "jobs", ["content_hash"])
    op.create_index("ix_jobs_source_updated_at", "jobs", ["source_updated_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_source_updated_at", table_name="jobs")
    op.drop_index("ix_jobs_content_hash", table_name="jobs")
    op.drop_column("jobs", "source_updated_at")
    op.drop_column("jobs", "content_hash")
    op.drop_column("jobs", "sponsors_visa")
    op.drop_column("jobs", "skills")
    op.drop_column("jobs", "employment_type")
    op.drop_column("jobs", "remote")
