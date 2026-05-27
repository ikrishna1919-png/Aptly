"""seed the first Workday source: General Motors

Revision ID: 0009_seed_workday_gm
Revises: 0008_bulk_seed_candidates
Create Date: 2026-05-27

Adds one row to the `sources` table:
    source_type='workday'
    token='generalmotors:wd5:Careers_GM'   (tenant:dc:site)
    display_name='General Motors'

Workday's per-company identity is a triple — tenant + data center +
site — so we pack all three into the existing `token` column rather
than adding new columns to `sources`. The `WorkdaySource` adapter
parses `token.split(":")` at the top of every fetch.

Seed is idempotent: the unique constraint on `(source_type, token)`
makes the Postgres `ON CONFLICT DO NOTHING` / SQLite `INSERT OR
IGNORE` insert safe to re-run.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op

revision: str = "0009_seed_workday_gm"
down_revision: str | None = "0008_bulk_seed_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GM_ROW = {
    "source_type": "workday",
    "token": "generalmotors:wd5:Careers_GM",
    "display_name": "General Motors",
}


def upgrade() -> None:
    sources = sa.table(
        "sources",
        sa.column("source_type", sa.String),
        sa.column("token", sa.String),
        sa.column("display_name", sa.String),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        stmt = (
            pg_insert(sources)
            .values([_GM_ROW])
            .on_conflict_do_nothing(index_elements=["source_type", "token"])
        )
        bind.execute(stmt)
    else:
        bind.execute(
            sa.text(
                "INSERT OR IGNORE INTO sources "
                "(source_type, token, display_name) "
                "VALUES (:source_type, :token, :display_name)"
            ),
            _GM_ROW,
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM sources WHERE source_type = :st AND token = :tok"),
        {"st": _GM_ROW["source_type"], "tok": _GM_ROW["token"]},
    )
