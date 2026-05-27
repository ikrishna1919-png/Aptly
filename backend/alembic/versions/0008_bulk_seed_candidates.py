"""add location + consecutive_failures columns and bulk-seed candidates

Revision ID: 0008_bulk_seed_candidates
Revises: 0007_sources_table
Create Date: 2026-05-27

Bulk-loads ~400 company names from `infra/company_seed.tsv` as
*candidate* sources. We don't know which ATS each company uses, so for
every name we slugify (lowercase + strip non-[a-z0-9]) and insert TWO
candidate rows: one `greenhouse`, one `lever`. The next ingest probes
each candidate; non-resolving tokens land at `last_status='error'` and
the per-source auto-disable threshold parks them so they stop costing
us a per-board timeout on every pass.

Schema-wise:
  * `location` (nullable string) — copied from the seed TSV, useful for
    later US-only filtering.
  * `consecutive_failures` (int, default 0) — drives the auto-disable
    counter in `app.services.ingest._record_source_result`.

SmartRecruiters is NOT candidate-expanded: its tokens are identifiers
(e.g. "Versant3"), not slugs, so guessing them would be noise.
"""

from __future__ import annotations

import logging
import pathlib
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op
from app.sources.seed_loader import candidate_rows

log = logging.getLogger("alembic.runtime.migration")

revision: str = "0008_bulk_seed_candidates"
down_revision: str | None = "0007_sources_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Resolve the seed file relative to this migration so it works the same
# in CI, local dev, and on deploy (assuming the repo's `infra/` dir is
# present alongside `backend/`).
_SEED_PATH = pathlib.Path(__file__).resolve().parents[3] / "infra" / "company_seed.tsv"


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("location", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # Bulk-seed. If the TSV is missing on deploy, log loudly + skip
    # the seed — the schema change should still land so future seed
    # attempts have a target table.
    if not _SEED_PATH.exists():
        log.warning("seed file %s not found — skipping bulk seed", _SEED_PATH)
        return

    rows = candidate_rows(_SEED_PATH)
    if not rows:
        log.warning("seed file %s is empty — nothing to bulk-seed", _SEED_PATH)
        return

    sources = sa.table(
        "sources",
        sa.column("source_type", sa.String),
        sa.column("token", sa.String),
        sa.column("display_name", sa.String),
        sa.column("location", sa.String),
    )

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
        # SQLite: parameterised bulk insert, dedup-on-conflict via
        # INSERT OR IGNORE. Same idempotency contract as the Postgres
        # path.
        bind.execute(
            sa.text(
                "INSERT OR IGNORE INTO sources "
                "(source_type, token, display_name, location) "
                "VALUES (:source_type, :token, :display_name, :location)"
            ),
            rows,
        )

    log.info("bulk-seeded %d candidate source rows from %s", len(rows), _SEED_PATH)


def downgrade() -> None:
    # Drop the columns; we don't try to surgically remove only the seeded
    # rows because there's no clean marker distinguishing them from
    # hand-added ones.
    op.drop_column("sources", "consecutive_failures")
    op.drop_column("sources", "location")
