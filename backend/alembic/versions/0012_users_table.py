"""create users table + add user_id to per-user tables + backfill

Revision ID: 0012_users_table
Revises: 0011_parse_runs
Create Date: 2026-05-27

Phase 5 introduces multi-user accounts via Google sign-in.

  * `users` table — one row per authenticated person. Email is
    unique; `google_subject_id` is unique-when-present (nullable
    initially because the migration seeds the owner row before
    they've signed in).
  * Seeds the owner row using `INITIAL_USER_EMAIL` from the
    environment (default `owner@example.com` for local dev). On the
    owner's first Google sign-in the auth handler matches the row by
    email and writes the `google_subject_id` so subsequent sign-ins
    look up by `sub`.
  * Adds `user_id` to `candidates`, `parse_runs`, and `job_analyses`,
    then backfills every existing row to point at the owner row.
    `user_id` is nullable on the column to keep the SQLite ALTER path
    simple — the application enforces non-null on every write.
  * `job_analyses` switches its uniqueness from `(job_id)` to
    `(user_id, job_id)` so two users can have independent cached
    analyses for the same job.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_users_table"
down_revision: str | None = "0011_parse_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _initial_owner_email() -> str:
    """Reads `INITIAL_USER_EMAIL` from the environment at migration
    time. Falls back to `owner@example.com` so local dev / tests
    don't need an explicit env var to run the migration; production
    deploys must set this to the operator's real Google address."""
    return os.environ.get("INITIAL_USER_EMAIL", "owner@example.com").strip().lower()


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("google_subject_id", sa.String(length=128), nullable=True, unique=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_google_subject_id", "users", ["google_subject_id"])

    # Seed the owner row. Idempotent on Postgres (ON CONFLICT) and
    # SQLite (INSERT OR IGNORE) so re-running this migration on an
    # environment that already has a matching email is a no-op.
    bind = op.get_bind()
    owner_email = _initial_owner_email()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        users = sa.table(
            "users",
            sa.column("email", sa.String),
            sa.column("name", sa.String),
        )
        bind.execute(
            pg_insert(users)
            .values({"email": owner_email, "name": "Owner"})
            .on_conflict_do_nothing(index_elements=["email"])
        )
    else:
        bind.execute(
            sa.text("INSERT OR IGNORE INTO users (email, name) VALUES (:email, :name)"),
            {"email": owner_email, "name": "Owner"},
        )

    # Look up the owner id; we use it to backfill every existing
    # per-user row in one statement per table.
    owner_id = bind.execute(
        sa.text("SELECT id FROM users WHERE email = :email"),
        {"email": owner_email},
    ).scalar_one()

    # candidates.user_id
    with op.batch_alter_table("candidates") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_candidates_user_id",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint("uq_candidates_user_id", ["user_id"])
    bind.execute(
        sa.text("UPDATE candidates SET user_id = :uid WHERE user_id IS NULL"),
        {"uid": owner_id},
    )

    # parse_runs.user_id
    with op.batch_alter_table("parse_runs") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_parse_runs_user_id",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_parse_runs_user_id", ["user_id"])
    bind.execute(
        sa.text("UPDATE parse_runs SET user_id = :uid WHERE user_id IS NULL"),
        {"uid": owner_id},
    )

    # job_analyses.user_id + switch uniqueness from (job_id) to
    # (user_id, job_id). Two users can now have independent caches
    # for the same job.
    with op.batch_alter_table("job_analyses") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_job_analyses_user_id",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_job_analyses_user_id", ["user_id"])
        batch.drop_constraint("uq_job_analyses_job_id", type_="unique")
        batch.create_unique_constraint("uq_job_analyses_user_job", ["user_id", "job_id"])
    bind.execute(
        sa.text("UPDATE job_analyses SET user_id = :uid WHERE user_id IS NULL"),
        {"uid": owner_id},
    )


def downgrade() -> None:
    with op.batch_alter_table("job_analyses") as batch:
        batch.drop_constraint("uq_job_analyses_user_job", type_="unique")
        batch.create_unique_constraint("uq_job_analyses_job_id", ["job_id"])
        batch.drop_index("ix_job_analyses_user_id")
        batch.drop_constraint("fk_job_analyses_user_id", type_="foreignkey")
        batch.drop_column("user_id")
    with op.batch_alter_table("parse_runs") as batch:
        batch.drop_index("ix_parse_runs_user_id")
        batch.drop_constraint("fk_parse_runs_user_id", type_="foreignkey")
        batch.drop_column("user_id")
    with op.batch_alter_table("candidates") as batch:
        batch.drop_constraint("uq_candidates_user_id", type_="unique")
        batch.drop_constraint("fk_candidates_user_id", type_="foreignkey")
        batch.drop_column("user_id")
    op.drop_index("ix_users_google_subject_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
