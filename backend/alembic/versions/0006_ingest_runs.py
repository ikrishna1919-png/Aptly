"""create ingest_runs table

Revision ID: 0006_ingest_runs
Revises: 0005_candidates
Create Date: 2026-05-26

`POST /api/admin/ingest` now returns immediately with a run_id; the actual
ingest pass runs in a background thread and writes its status + final
stats here. Lets the scheduled workflow + ad-hoc operators see whether
the last run succeeded without holding a long HTTP connection open.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_ingest_runs"
down_revision: str | None = "0005_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="running",
        ),
        sa.Column("stats", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ingest_runs_run_id", "ingest_runs", ["run_id"])
    op.create_index("ix_ingest_runs_started_at", "ingest_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_ingest_runs_started_at", table_name="ingest_runs")
    op.drop_index("ix_ingest_runs_run_id", table_name="ingest_runs")
    op.drop_table("ingest_runs")
