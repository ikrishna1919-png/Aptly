"""seed additional public ATS board tokens (greenhouse / lever / smartrecruiters / ashby)

Revision ID: 0018_seed_more_sources
Revises: 0017_profile_saved_at
Create Date: 2026-05-28

Expands the `sources` table with the curated per-source token lists in
`app.sources.companies` (which gained a batch of well-known public
boards). Re-seeds ALL four supported source types from those lists; the
unique constraint on `(source_type, token)` makes the insert idempotent
(Postgres `ON CONFLICT DO NOTHING` / SQLite `INSERT OR IGNORE`), so on an
already-seeded DB only the genuinely-new `(source_type, token)` rows are
added — existing rows are untouched.

New rows insert with the model's default `enabled=True`. The next ingest
run probes each token; non-resolving boards land at `last_status='error'`
and the per-source auto-disable threshold parks them. So it's safe to
bulk-load convention-based candidates here — a wrong slug is
self-healing, not harmful (same contract as migrations 0008/0010).

These are all PUBLIC ATS job-board endpoints (Greenhouse, Lever,
SmartRecruiters, Ashby) — no scraping, no aggregators, no auth.

downgrade() is intentionally a no-op: deleting by `source_type` would
also remove tokens seeded by earlier migrations (0007/0008/0010), and we
can't safely distinguish this batch's additions from pre-existing rows.
To back out a specific token, disable it in the `sources` table instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op
from app.sources.companies import (
    ASHBY_KNOWN_TOKENS,
    GREENHOUSE_TOKENS,
    LEVER_TOKENS,
    SMARTRECRUITERS_TOKENS,
)

revision: str = "0018_seed_more_sources"
down_revision: str | None = "0017_profile_saved_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rows() -> list[dict]:
    rows: list[dict] = []
    for token in GREENHOUSE_TOKENS:
        rows.append(
            {"source_type": "greenhouse", "token": token, "display_name": None, "location": None}
        )
    for token in LEVER_TOKENS:
        rows.append(
            {"source_type": "lever", "token": token, "display_name": None, "location": None}
        )
    for token in SMARTRECRUITERS_TOKENS:
        rows.append(
            {
                "source_type": "smartrecruiters",
                "token": token,
                "display_name": None,
                "location": None,
            }
        )
    for token, display_name in ASHBY_KNOWN_TOKENS:
        rows.append(
            {"source_type": "ashby", "token": token, "display_name": display_name, "location": None}
        )
    return rows


def upgrade() -> None:
    rows = _rows()
    if not rows:
        return

    sources = sa.table(
        "sources",
        sa.column("source_type", sa.String),
        sa.column("token", sa.String),
        sa.column("display_name", sa.String),
        sa.column("location", sa.String),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
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
    # No-op — see module docstring. Seeded source rows are additive and
    # indistinguishable from earlier-migration seeds; disable rows rather
    # than downgrade to remove a source.
    pass
