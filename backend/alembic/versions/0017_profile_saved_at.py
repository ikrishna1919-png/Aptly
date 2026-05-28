"""add profile_saved_at column to candidates

Revision ID: 0017_profile_saved_at
Revises: 0016_parse_run_raw_output
Create Date: 2026-05-29

Gates job-list visibility behind a real save. The Candidate row is
seeded automatically on first `GET /api/profile` (so the editor has
a shape to render); we need a way to distinguish "row seeded but
never touched" from "user explicitly saved their profile" so the
sign-up flow can route brand-new users to `/profile` first and
unblock `/jobs` only after a real save.

Postgres-valid:
  * Nullable so old rows decode cleanly. NULL means "never saved";
    the application code reads NULL → seeded shape → block jobs.
  * No `server_default` — leaving NULL is the seeded state, and the
    PUT handler will populate the timestamp explicitly on first save.
  * `op.batch_alter_table` keeps the SQLite ALTER path working in
    local + CI test runs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_profile_saved_at"
down_revision: str | None = "0016_parse_run_raw_output"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("candidates") as batch:
        batch.add_column(sa.Column("profile_saved_at", sa.DateTime(timezone=True), nullable=True))
    # Backfill so existing users (the original owner whose seeded
    # demo row has been edited, anyone who'd already saved before
    # this column existed) aren't kicked back to the profile gate
    # on next sign-in. "Row exists" is treated as "user has claimed
    # this profile"; brand-new rows created after this migration
    # leave the column NULL until the first explicit save.
    op.execute("UPDATE candidates SET profile_saved_at = updated_at WHERE profile_saved_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("candidates") as batch:
        batch.drop_column("profile_saved_at")
