"""create employer_sponsorship table for H-1B LCA aggregates

Revision ID: 0013_employer_sponsorship
Revises: 0012_users_table
Create Date: 2026-05-28

Adds the table the sponsorship-intelligence layer reads from. One row
per *normalised* employer name; populated by the
`sponsorship-ingest` CLI from public DOL LCA disclosure files. See
`app/models/employer_sponsorship.py` for the data model and signal
semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_employer_sponsorship"
down_revision: str | None = "0012_users_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employer_sponsorship",
        sa.Column("normalized_name", sa.String(length=255), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "lca_count_12mo",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "lca_count_3yr",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("most_recent_filing", sa.Date(), nullable=True),
        sa.Column(
            "distinct_titles_12mo",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "source_file",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "last_loaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Index lets the conservative-signal filter on /api/jobs scan
    # candidate rows without a full sequential scan once the table
    # has a few hundred thousand rows. Same shape for the inclusive
    # signal — both filters share this index.
    op.create_index(
        "ix_employer_sponsorship_lca_12mo",
        "employer_sponsorship",
        ["lca_count_12mo"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_employer_sponsorship_lca_12mo",
        table_name="employer_sponsorship",
    )
    op.drop_table("employer_sponsorship")
