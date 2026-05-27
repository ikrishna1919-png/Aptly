"""Migration 0010 + Ashby end-to-end integration check.

Pins three behaviours:

  * The seed migration lands the well-known list (Linear, PostHog,
    Notion, etc.) AND every TSV-expanded candidate as
    `source_type='ashby'`.
  * The `SOURCES` registry maps `ashby` → `AshbySource` so the
    orchestrator dispatches new rows of that type correctly.
  * A seeded Ashby row reaches `last_status='success'` with a real
    `jobs_found_last_run` count via `run_ingest` (using a fake
    adapter so the test stays hermetic).
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.config import Settings
from app.database import Base
from app.models.source import Source
from app.services.ingest import run_ingest
from app.sources import SOURCES
from app.sources.base import JobSource, NormalizedJob
from app.sources.companies import ASHBY_KNOWN_TOKENS
from app.sources.seed_loader import candidate_rows


def _alembic_config(db_url: str) -> Config:
    backend_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    from app import config as config_module

    db_path = tmp_path / "ashby.sqlite"
    db_url = f"sqlite+pysqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    config_module.get_settings.cache_clear()

    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")

    engine = create_engine(db_url, future=True)
    Session = sessionmaker(bind=engine, future=True)
    try:
        yield Session, cfg
    finally:
        engine.dispose()
        config_module.get_settings.cache_clear()


# ── Registry wiring ────────────────────────────────────────────────────────


def test_ashby_registered_in_sources_registry():
    """The source_type string used by the migration must dispatch to a
    real adapter class — otherwise the orchestrator logs "unknown
    source" and silently skips every Ashby row."""
    assert "ashby" in SOURCES
    assert SOURCES["ashby"].__name__ == "AshbySource"


# ── Migration 0010 ─────────────────────────────────────────────────────────


def test_migration_seeds_every_known_ashby_token(migrated_db):
    Session, _ = migrated_db
    expected = {token for token, _ in ASHBY_KNOWN_TOKENS}
    with Session() as s:
        actual = set(s.execute(select(Source.token).where(Source.source_type == "ashby")).scalars())
    missing = expected - actual
    assert not missing, f"known Ashby tokens missing after migration: {missing}"


def test_migration_seeds_known_tokens_with_display_names(migrated_db):
    """The well-known list carries `display_name` so the admin UI can
    show a legible label before the first ingest pass populates more
    detail."""
    Session, _ = migrated_db
    with Session() as s:
        linear = s.execute(
            select(Source).where(Source.source_type == "ashby", Source.token == "linear")
        ).scalar_one()
    assert linear.display_name == "Linear"
    assert linear.enabled is True
    assert linear.consecutive_failures == 0


def test_migration_also_bulk_seeds_tsv_candidates_as_ashby(migrated_db):
    """The TSV-expanded candidate set is reused for Ashby — same
    pattern Greenhouse + Lever used in migration 0008."""
    Session, _ = migrated_db
    expected = {r["token"] for r in candidate_rows(source_types=("ashby",))}
    with Session() as s:
        actual = set(s.execute(select(Source.token).where(Source.source_type == "ashby")).scalars())
    missing = expected - actual
    assert not missing, f"{len(missing)} TSV-expanded ashby candidates missing"


def test_migration_0010_is_idempotent(migrated_db):
    Session, cfg = migrated_db
    with Session() as s:
        first = s.execute(select(Source).where(Source.source_type == "ashby")).scalars().all()

    command.downgrade(cfg, "0009_seed_workday_gm")
    command.upgrade(cfg, "head")

    with Session() as s:
        second = s.execute(select(Source).where(Source.source_type == "ashby")).scalars().all()
    assert len(first) == len(second)


# ── End-to-end via run_ingest ──────────────────────────────────────────────


def _nj(token: str, idx: int) -> NormalizedJob:
    ts = datetime.now(UTC) - timedelta(hours=1)
    return NormalizedJob(
        source="ashby",
        external_id=f"{token}-{idx}",
        company=token,
        title=f"Engineer {idx}",
        url=f"https://jobs.ashbyhq.com/{token}/{idx}",
        source_updated_at=ts,
        posted_at=ts,
        skills=["Python"],
    )


class _FakeAshby(JobSource):
    name = "ashby"

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        return [_nj(token, i) for i in range(4)]


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        yield s


@pytest.fixture
def settings():
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
        INGEST_CONCURRENCY=2,
        SOURCE_FAILURE_THRESHOLD=100,
    )


def test_ashby_linear_reaches_success_via_run_ingest(db_session, settings):
    """A `ashby:linear` row drives one successful pass through
    `run_ingest`; the telemetry on the row reflects the postings the
    fake adapter returned. The real adapter would hit the network — we
    use a fake to keep the test hermetic; the per-adapter test file
    pins the actual HTTP wiring."""
    db_session.add(
        Source(
            source_type="ashby",
            token="linear",
            display_name="Linear",
            enabled=True,
        )
    )
    db_session.commit()

    stats = run_ingest(
        db_session,
        settings,
        source_factories={"ashby": _FakeAshby},
    )

    row = db_session.query(Source).filter_by(source_type="ashby").one()
    assert row.last_status == "success"
    assert row.last_error is None
    assert row.jobs_found_last_run == 4
    assert row.last_run_at is not None
    assert stats.inserted == 4
