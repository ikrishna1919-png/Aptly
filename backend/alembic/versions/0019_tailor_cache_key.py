"""add cache_key to tailor_runs

Revision ID: 0019_tailor_cache_key
Revises: f51a26bd17a9
Create Date: 2026-05-29

Result cache for the tailoring flow: a SHA-256 over (prompt version +
candidate fingerprint + normalized JD). On a tailor request we look for a
recent `done` run with the same key for the same user and return its
resume immediately — no Anthropic call. Additive single nullable column +
index; touches nothing existing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_tailor_cache_key"
down_revision: str | None = "f51a26bd17a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tailor_runs", sa.Column("cache_key", sa.String(length=64), nullable=True))
    op.create_index("ix_tailor_runs_cache_key", "tailor_runs", ["cache_key"])


def downgrade() -> None:
    op.drop_index("ix_tailor_runs_cache_key", table_name="tailor_runs")
    op.drop_column("tailor_runs", "cache_key")
