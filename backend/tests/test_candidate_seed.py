"""Tests for the demo candidate seed + the DB-backed accessor."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.candidate import DEMO_SLUG, Candidate
from app.services.demo_candidate import DEMO_CANDIDATE, candidate_fingerprint, get_candidate


@pytest.fixture
def Session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def test_get_candidate_returns_db_row_when_seeded(Session):
    with Session() as s:
        s.add(
            Candidate(
                slug=DEMO_SLUG,
                profile={
                    "name": "Custom Person",
                    "summary": "Custom summary.",
                    "skills": ["Custom skill"],
                    "experience": [],
                    "education": [],
                },
            )
        )
        s.commit()
        c = get_candidate(s)
        assert c["name"] == "Custom Person"
        assert c["summary"] == "Custom summary."


def test_get_candidate_falls_back_to_constant_when_not_seeded(Session):
    with Session() as s:
        c = get_candidate(s)
        assert c["name"] == DEMO_CANDIDATE["name"]
        assert c is DEMO_CANDIDATE  # falls back to the literal, not a copy


def test_fingerprint_changes_when_candidate_changes():
    other = {**DEMO_CANDIDATE, "name": "Different Person"}
    assert candidate_fingerprint(DEMO_CANDIDATE) != candidate_fingerprint(other)


def test_migration_seeds_demo_row_idempotently(tmp_path, monkeypatch):
    """End-to-end: a fresh SQLite DB upgraded by Alembic ends up with
    exactly one `demo` candidate row, and re-running the migration chain
    against the same DB doesn't duplicate it."""
    import os
    import subprocess

    from sqlalchemy import create_engine, text

    db_path = tmp_path / "smoke.db"
    url = f"sqlite+pysqlite:///{db_path}"
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(["alembic", "upgrade", "head"], cwd=backend_dir, env=env, check=True)

    engine = create_engine(url, future=True)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT slug FROM candidates")).fetchall()
        assert [r[0] for r in rows] == [DEMO_SLUG]

    # Re-running the migration chain is a no-op (no duplicate row).
    subprocess.run(["alembic", "upgrade", "head"], cwd=backend_dir, env=env, check=True)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT slug FROM candidates")).fetchall()
        assert [r[0] for r in rows] == [DEMO_SLUG]
