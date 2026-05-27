"""bulk-seed Ashby sources (known list + TSV-expanded candidates)

Revision ID: 0010_seed_ashby_candidates
Revises: 0009_seed_workday_gm
Create Date: 2026-05-27

Adds Ashby (`source_type='ashby'`) to the sources table in two batches:

  1. `ASHBY_KNOWN_TOKENS` — a hand-curated list of companies known to
     publish on Ashby (Linear, PostHog, Notion, Ramp, Vanta, Replicate,
     Modal, Anthropic, Hex, Census, Cohere, Anrok, Replit, Browserbase,
     Together AI, Mercury, Coda). These rows carry `display_name` so
     the admin UI surfaces them legibly even before the first ingest.

  2. Slugified candidates from `infra/company_seed.tsv` re-expanded
     for `source_type='ashby'` — same approach migration 0008 used for
     Greenhouse + Lever. Non-resolving boards land at
     `last_status='error'` on the next ingest and the per-source
     auto-disable threshold parks them, so it's safe to bulk-load
     aspirational entries.

Seed is idempotent: the unique constraint on `(source_type, token)`
makes the Postgres `ON CONFLICT DO NOTHING` / SQLite `INSERT OR
IGNORE` insert safe to re-run. The known-list rows take precedence
when their slug also appears in the TSV-expanded candidate set
because they're inserted first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op
from app.sources.companies import ASHBY_KNOWN_TOKENS
from app.sources.seed_loader import candidate_rows

revision: str = "0010_seed_ashby_candidates"
down_revision: str | None = "0009_seed_workday_gm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _known_rows() -> list[dict]:
    return [
        {
            "source_type": "ashby",
            "token": token,
            "display_name": display_name,
            "location": None,
        }
        for token, display_name in ASHBY_KNOWN_TOKENS
    ]


def upgrade() -> None:
    # Known list first so its display_names "win" on slug collisions
    # with the TSV-expanded candidates (e.g. "ramp" — both lists).
    known = _known_rows()
    candidates = candidate_rows(source_types=("ashby",))

    sources = sa.table(
        "sources",
        sa.column("source_type", sa.String),
        sa.column("token", sa.String),
        sa.column("display_name", sa.String),
        sa.column("location", sa.String),
    )

    bind = op.get_bind()
    dialect = bind.dialect.name
    rows = known + candidates
    if not rows:
        return

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
                "INSERT OR IGNORE INTO sources "
                "(source_type, token, display_name, location) "
                "VALUES (:source_type, :token, :display_name, :location)"
            ),
            rows,
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM sources WHERE source_type = :st"),
        {"st": "ashby"},
    )
