"""create candidates table + seed the demo candidate

Revision ID: 0005_candidates
Revises: 0004_job_analyses
Create Date: 2026-05-25

This migration is the single source of truth for the demo candidate that
the resume-tailoring endpoints use. It is idempotent: the seed insert is
guarded by "WHERE NOT EXISTS" so re-running against a database that
already has a `demo` row is a no-op.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op
from app.models.candidate import DEMO_SLUG
from app.services.demo_candidate import DEMO_CANDIDATE

revision: str = "0005_candidates"
down_revision: str | None = "0004_job_analyses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    candidates = op.create_table(
        "candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False, unique=True),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Idempotent seed — works on both Postgres (ON CONFLICT) and SQLite
    # (INSERT OR IGNORE). The `slug` unique constraint makes both safe.
    bind = op.get_bind()
    dialect = bind.dialect.name
    payload = {"slug": DEMO_SLUG, "profile": DEMO_CANDIDATE}

    if dialect == "postgresql":
        stmt = (
            pg_insert(candidates).values(**payload).on_conflict_do_nothing(index_elements=["slug"])
        )
        bind.execute(stmt)
    else:
        # SQLite path (used by tests): wrap the dict in json.dumps so the
        # bound parameter binds as a string. SQLAlchemy's JSON column does
        # this for ORM inserts but bind.execute(text(...)) is parameter-level.
        bind.execute(
            sa.text("INSERT OR IGNORE INTO candidates (slug, profile) VALUES (:slug, :profile)"),
            {"slug": DEMO_SLUG, "profile": json.dumps(DEMO_CANDIDATE)},
        )


def downgrade() -> None:
    op.drop_table("candidates")
