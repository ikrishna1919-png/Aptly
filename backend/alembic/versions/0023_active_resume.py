"""add active-resume columns to candidates

Revision ID: 0023_active_resume
Revises: 0022_ats_hub
Create Date: 2026-05-30

Additive only — one saved resume per user, kept on the candidate row. All
columns nullable so existing rows are untouched. DEFAULT-format storage
(default_resume_format / default_cover_letter_format) already exists from
0022, so no new columns are needed for that.

  active_resume_filename      original upload name
  active_resume_content_type  DOCX or PDF mime
  active_resume_uploaded_at   when it was saved
  active_resume_blob          the bytes (served back on download)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_active_resume"
down_revision: str | None = "0022_ats_hub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidates", sa.Column("active_resume_filename", sa.String(length=256), nullable=True)
    )
    op.add_column(
        "candidates", sa.Column("active_resume_content_type", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "candidates",
        sa.Column("active_resume_uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("candidates", sa.Column("active_resume_blob", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidates", "active_resume_blob")
    op.drop_column("candidates", "active_resume_uploaded_at")
    op.drop_column("candidates", "active_resume_content_type")
    op.drop_column("candidates", "active_resume_filename")
