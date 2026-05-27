"""create parse_runs table

Revision ID: 0011_parse_runs
Revises: 0010_seed_ashby_candidates
Create Date: 2026-05-27

`POST /api/admin/profile/parse` now returns immediately with a
`run_id`; the Anthropic call runs in a background thread. Status +
the parsed profile (or error) are written here so the frontend can
poll `GET /api/admin/profile/parse/{run_id}` for completion. Same
shape as `ingest_runs`, just with a `profile` JSON payload instead
of `stats`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_parse_runs"
down_revision: str | None = "0010_seed_ashby_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parse_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="running",
        ),
        sa.Column("profile", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_parse_runs_run_id", "parse_runs", ["run_id"])
    op.create_index("ix_parse_runs_started_at", "parse_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_parse_runs_started_at", table_name="parse_runs")
    op.drop_index("ix_parse_runs_run_id", table_name="parse_runs")
    op.drop_table("parse_runs")
