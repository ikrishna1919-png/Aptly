"""create extension_sessions + saved_qa_pairs (Chrome extension v1.0)

Revision ID: 0021_extension_tables
Revises: 0020_ats_runs_columns
Create Date: 2026-05-30

Two NEW tables for the browser extension. Purely additive — no changes to
existing tables, no FK changes beyond the new tables' own user_id references.

  extension_sessions  per-device bearer tokens (we store only the SHA-256
                      hash, never the raw token; revocable from /profile).
  saved_qa_pairs      the learning loop: canonical application questions +
                      the variants we've clustered onto them + the user's
                      saved answer, reused across application sites.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_extension_tables"
down_revision: str | None = "0020_ats_runs_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extension_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SHA-256 of the raw token. Raw token is shown to the extension ONCE
        # (in the connect redirect) and never persisted server-side.
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("device_name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_extension_sessions_user_id", "extension_sessions", ["user_id"])
    op.create_index("ix_extension_sessions_token_hash", "extension_sessions", ["token_hash"])

    op.create_table(
        "saved_qa_pairs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The canonical question form + the variants clustered onto it.
        sa.Column("question_canonical", sa.Text(), nullable=False),
        sa.Column("question_examples", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("field_type", sa.String(length=16), nullable=False, server_default="text"),
        sa.Column("source_ats", sa.String(length=32), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_saved_qa_pairs_user_id", "saved_qa_pairs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_qa_pairs_user_id", table_name="saved_qa_pairs")
    op.drop_table("saved_qa_pairs")
    op.drop_index("ix_extension_sessions_token_hash", table_name="extension_sessions")
    op.drop_index("ix_extension_sessions_user_id", table_name="extension_sessions")
    op.drop_table("extension_sessions")
