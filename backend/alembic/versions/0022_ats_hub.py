"""ats hub: default formats + cover_letters

Revision ID: 0022_ats_hub
Revises: 0021_extension_tables
Create Date: 2026-05-30

Additive only, for the /ats 5-feature hub:
  * candidates.default_resume_format        jsonb (format name + custom opts)
  * candidates.default_cover_letter_format  jsonb
  * cover_letters table                     generated cover letters

No changes to existing columns/rows. Unset defaults fall back to "modern"
in app code, so existing users are unaffected.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_ats_hub"
down_revision: str | None = "0021_extension_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("default_resume_format", sa.JSON(), nullable=True))
    op.add_column("candidates", sa.Column("default_cover_letter_format", sa.JSON(), nullable=True))

    op.create_table(
        "cover_letters",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("jd_text", sa.Text(), nullable=True),
        sa.Column("company_name", sa.String(length=256), nullable=True),
        # The generated letter as structured JSON (date, recipient, greeting,
        # paragraphs[], signature). Written on `done`.
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("format", sa.String(length=32), nullable=True),
        sa.Column("questions_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="generating"),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cover_letters_user_id", "cover_letters", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_cover_letters_user_id", table_name="cover_letters")
    op.drop_table("cover_letters")
    op.drop_column("candidates", "default_cover_letter_format")
    op.drop_column("candidates", "default_resume_format")
