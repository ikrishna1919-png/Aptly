"""bulk-load ATS company tokens across every supported platform

Revision ID: 0014_bulk_load_ats_tokens
Revises: 0013_employer_sponsorship
Create Date: 2026-05-28

Expands `sources` coverage by inserting curated bulk lists of company
board tokens for Greenhouse, Lever, Ashby, SmartRecruiters, and
Workday. The lists are in `app.sources.bulk_tokens` — see that file
for provenance and per-platform shape rules.

Idempotent: `INSERT OR IGNORE` on SQLite and `ON CONFLICT DO NOTHING`
on Postgres, both keyed on the `(source_type, token)` unique
constraint. Re-running the migration on a DB that already has these
rows is a no-op; running it after the next migration adds more
tokens just adds the deltas.

`enabled=true` is the default on the column, so the bulk rows go
straight into rotation. Tokens that don't resolve land at
`last_status='error'` on first ingest and the existing
`SOURCE_FAILURE_THRESHOLD` (default 3) auto-disables them. The
`INGEST_MAX_PER_RUN` rotation (default 150, least-recently-checked
first) bounds each pass — newly-seeded rows have `last_run_at=NULL`
which sorts first, so they get worked through across successive
scheduled runs without stalling any one pass.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op
from app.sources.bulk_tokens import all_bulk_rows

revision: str = "0014_bulk_load_ats_tokens"
down_revision: str | None = "0013_employer_sponsorship"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.0014_bulk_load_ats_tokens")


def upgrade() -> None:
    bind = op.get_bind()
    by_platform = all_bulk_rows()

    sources = sa.table(
        "sources",
        sa.column("source_type", sa.String),
        sa.column("token", sa.String),
        sa.column("display_name", sa.String),
        sa.column("location", sa.String),
    )

    dialect = bind.dialect.name
    totals: dict[str, dict[str, int]] = {}
    for source_type, rows in by_platform.items():
        # Count what's already there so the migration's log line tells
        # the operator how many rows were genuinely new. The unique
        # constraint handles the dedupe in SQL — this count is for
        # human consumption only.
        existing = bind.execute(
            sa.text("SELECT COUNT(*) FROM sources WHERE source_type = :st"),
            {"st": source_type},
        ).scalar_one()

        if rows:
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

        after = bind.execute(
            sa.text("SELECT COUNT(*) FROM sources WHERE source_type = :st"),
            {"st": source_type},
        ).scalar_one()
        totals[source_type] = {
            "found": len(rows),
            "inserted": int(after - existing),
            "existed": int(len(rows) - (after - existing)),
            "total_after": int(after),
        }

    # Surface per-platform counts in the migration log. The operator
    # captures this from the Render deploy log + the test suite asserts
    # the same shape under `bulk_tokens.py`.
    for st, stats in totals.items():
        log.info(
            "bulk-load %s: found=%d inserted=%d already_existed=%d total_after=%d",
            st,
            stats["found"],
            stats["inserted"],
            stats["existed"],
            stats["total_after"],
        )


def downgrade() -> None:
    """Remove ONLY the rows this migration inserted. We can't easily
    identify them after the fact (no marker column), so the
    downgrade re-imports the same bulk lists and deletes by
    `(source_type, token)`. Rows added between the bulk-load and the
    downgrade are preserved."""
    bind = op.get_bind()
    by_platform = all_bulk_rows()
    for source_type, rows in by_platform.items():
        tokens = [r["token"] for r in rows]
        if not tokens:
            continue
        # Driver-agnostic chunked DELETE — bind a parameter per token.
        # SQLite caps placeholders at ~999; chunking at 200 keeps
        # plenty of headroom.
        for i in range(0, len(tokens), 200):
            chunk = tokens[i : i + 200]
            placeholders = ", ".join(f":t{j}" for j in range(len(chunk)))
            params = {"st": source_type, **{f"t{j}": tok for j, tok in enumerate(chunk)}}
            bind.execute(
                sa.text(
                    f"DELETE FROM sources WHERE source_type = :st AND token IN ({placeholders})"
                ),
                params,
            )
