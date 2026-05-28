"""add raw_llm_output column to parse_runs

Revision ID: 0016_parse_run_raw_output
Revises: 0015_resolved_companies
Create Date: 2026-05-29

Adds a nullable JSON column to `parse_runs` so the worker can store
the verbatim structured-output JSON that the Anthropic API returned,
before the parser maps it into the Profile shape. Lets the operator
triage a bad parse by inspecting the row directly — extraction vs.
mapping vs. display becomes a one-query distinction.

Postgres-valid:
  * Column is nullable so old rows decode cleanly.
  * No `server_default` (the column is JSON; defaulting JSON via
    `server_default` is dialect-specific and unnecessary when the
    column is nullable).
  * `op.batch_alter_table` keeps the SQLite ALTER path working too
    so local + CI tests don't need Postgres.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_parse_run_raw_output"
down_revision: str | None = "0015_resolved_companies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("parse_runs") as batch:
        batch.add_column(sa.Column("raw_llm_output", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("parse_runs") as batch:
        batch.drop_column("raw_llm_output")
