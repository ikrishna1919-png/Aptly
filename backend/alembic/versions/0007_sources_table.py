"""create sources table + seed it from the hardcoded token lists

Revision ID: 0007_sources_table
Revises: 0006_ingest_runs
Create Date: 2026-05-27

Promotes the hardcoded `(source_type, token)` list in
`app/sources/companies.py` to a real DB table so:

  * `enabled=False` rows can be parked without losing observability
    history;
  * each token gets per-source telemetry (`last_run_at`, `last_status`,
    `last_error`, `jobs_found_last_run`) written by `run_ingest`;
  * future additions land via new Alembic migrations or admin tooling,
    not by hand-editing a Python list.

Seed is idempotent: a unique constraint on `(source_type, token)` makes
the Postgres `ON CONFLICT DO NOTHING` / SQLite `INSERT OR IGNORE` insert
safe to re-run.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op
from app.sources.companies import (
    GREENHOUSE_TOKENS,
    LEVER_TOKENS,
    SMARTRECRUITERS_TOKENS,
)

revision: str = "0007_sources_table"
down_revision: str | None = "0006_ingest_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sources = op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("jobs_found_last_run", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("source_type", "token", name="uq_sources_type_token"),
    )
    op.create_index("ix_sources_source_type", "sources", ["source_type"])

    rows = (
        [{"source_type": "greenhouse", "token": t} for t in GREENHOUSE_TOKENS]
        + [{"source_type": "lever", "token": t} for t in LEVER_TOKENS]
        + [{"source_type": "smartrecruiters", "token": t} for t in SMARTRECRUITERS_TOKENS]
    )
    if not rows:
        return

    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        stmt = (
            pg_insert(sources)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["source_type", "token"])
        )
        bind.execute(stmt)
    else:
        bind.execute(
            sa.text(
                "INSERT OR IGNORE INTO sources (source_type, token) "
                "VALUES (:source_type, :token)"
            ),
            rows,
        )


def downgrade() -> None:
    op.drop_index("ix_sources_source_type", table_name="sources")
    op.drop_table("sources")
