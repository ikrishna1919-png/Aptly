"""Tests for the `sources` table + migration + per-source telemetry.

Covers:
  * The Alembic 0007 migration creates the table and seeds every token
    that lives in `companies.py` — including the 11 new Greenhouse
    tokens the sources-table change shipped with — and does NOT seed
    `smxtech` (deferred because its ATS platform is unconfirmed).
  * The seed is idempotent: running the migration on a DB that already
    has the table + rows is a no-op (no duplicate-key crash, no extra
    rows).
  * Per-source observability columns (`last_run_at`, `last_status`,
    `last_error`, `jobs_found_last_run`) get filled in by `run_ingest`
    on the happy path, on per-board failures, and on rows that point at
    an unknown source_type.

The migration is run via Alembic against a temporary SQLite file so the
seed is exercised end-to-end (the seed branch differs between Postgres
and SQLite, so testing the SQLite branch is the achievable half — the
Postgres branch is reviewed by eye + covered by deploy).
"""

from __future__ import annotations

import pathlib
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
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable
from app.sources.companies import (
    GREENHOUSE_TOKENS,
    LEVER_TOKENS,
    SMARTRECRUITERS_TOKENS,
)

# ── Migration end-to-end ───────────────────────────────────────────────────


def _alembic_config(db_url: str) -> Config:
    backend_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    """Spin up a fresh SQLite file + run every migration to head against it.

    The settings cache is reset because `alembic/env.py` reads from
    `get_settings()` for the DB URL — without the cache flush, the
    migration would target whatever URL the test process started with.
    """
    from app import config as config_module

    db_path = tmp_path / "sources.sqlite"
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


def test_migration_creates_table_and_seeds_all_tokens(migrated_db):
    Session, _ = migrated_db
    with Session() as s:
        rows = list(s.execute(select(Source)).scalars())

    by_type: dict[str, set[str]] = {}
    for r in rows:
        by_type.setdefault(r.source_type, set()).add(r.token)

    assert set(GREENHOUSE_TOKENS).issubset(by_type.get("greenhouse", set()))
    assert set(LEVER_TOKENS).issubset(by_type.get("lever", set()))
    assert set(SMARTRECRUITERS_TOKENS).issubset(by_type.get("smartrecruiters", set()))


def test_migration_seeds_the_new_greenhouse_tokens(migrated_db):
    """The 11 Greenhouse tokens that shipped with the sources-table change
    are reachable from the DB after the migration runs."""
    Session, _ = migrated_db
    expected_new = {
        "greenthumbindustries",
        "assystinc",
        "atek",
        "sayari",
        "torcrobotics",
        "lovelytics",
        "virtru",
        "amendconsulting",
        "cleerlyhealth",
        "orioninnovation",
        "vectorusa",
    }
    with Session() as s:
        gh = set(
            s.execute(select(Source.token).where(Source.source_type == "greenhouse")).scalars()
        )
    missing = expected_new - gh
    assert not missing, f"new tokens missing from seeded sources table: {missing}"


def test_migration_does_not_seed_smxtech(migrated_db):
    """smxtech was intentionally held back — its ATS platform was
    unconfirmed. Anyone re-adding it must do so via a new migration."""
    Session, _ = migrated_db
    with Session() as s:
        present = s.execute(select(Source).where(Source.token == "smxtech")).scalar_one_or_none()
    assert present is None


def test_seeded_rows_default_to_enabled(migrated_db):
    """Operators expect every seeded source to participate in the next
    ingest unless they explicitly disable it."""
    Session, _ = migrated_db
    with Session() as s:
        disabled = list(s.execute(select(Source).where(Source.enabled.is_(False))).scalars())
    assert disabled == []


def test_seed_is_idempotent(migrated_db):
    """Re-running the migration's seed step must NOT duplicate rows or
    crash on the unique constraint."""
    Session, cfg = migrated_db
    with Session() as s:
        first = s.execute(select(Source)).scalars().all()
        first_count = len(first)

    # Re-run the seed by downgrading + upgrading.
    command.downgrade(cfg, "0006_ingest_runs")
    command.upgrade(cfg, "head")

    with Session() as s:
        second = s.execute(select(Source)).scalars().all()
    assert len(second) == first_count


# ── Per-source telemetry via run_ingest ────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _nj(external_id: str, *, source: str = "fake", company: str = "acme") -> NormalizedJob:
    ts = _utcnow() - timedelta(hours=1)
    return NormalizedJob(
        source=source,
        external_id=external_id,
        company=company,
        title="Engineer",
        url=f"https://example.com/{external_id}",
        source_updated_at=ts,
        posted_at=ts,
        skills=[],
    )


class _ScriptedSource(JobSource):
    name = "fake"

    def __init__(self, healthy=None, unhealthy=None):
        self._healthy = healthy or {}
        self._unhealthy = unhealthy or set()

    def fetch(self, token: str):
        if token in self._unhealthy:
            raise SourceUnavailable(f"fake:{token} 404")
        return list(self._healthy.get(token, []))


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        yield s


@pytest.fixture
def settings():
    return Settings(DATABASE_URL="sqlite+pysqlite:///:memory:", ADMIN_TOKEN="t", HOURS_WINDOW=48)


def test_telemetry_written_for_successful_source(session, settings):
    session.add(Source(source_type="fake", token="acme", enabled=True))
    session.commit()
    src_cls = lambda: _ScriptedSource(  # noqa: E731
        healthy={"acme": [_nj("a"), _nj("b"), _nj("c")]}
    )
    before = _utcnow()
    run_ingest(session, settings, source_factories={"fake": src_cls})

    row = session.query(Source).filter_by(token="acme").one()
    assert row.last_status == "success"
    assert row.last_error is None
    assert row.jobs_found_last_run == 3
    assert row.last_run_at is not None
    # Stored as tz-aware in Postgres; SQLite drops tzinfo. Either way the
    # timestamp must be at-or-after the run start.
    stored = row.last_run_at
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert stored >= before - timedelta(seconds=5)


def test_telemetry_marks_broken_source_as_error_without_aborting_run(session, settings):
    session.add(Source(source_type="fake", token="good", enabled=True))
    session.add(Source(source_type="fake", token="bad", enabled=True))
    session.commit()
    src_cls = lambda: _ScriptedSource(  # noqa: E731
        healthy={"good": [_nj("g")]},
        unhealthy={"bad"},
    )
    stats = run_ingest(session, settings, source_factories={"fake": src_cls})

    bad = session.query(Source).filter_by(token="bad").one()
    assert bad.last_status == "error"
    assert bad.last_error and "404" in bad.last_error
    assert bad.jobs_found_last_run == 0

    good = session.query(Source).filter_by(token="good").one()
    assert good.last_status == "success"
    assert good.jobs_found_last_run == 1

    # And the overall run didn't abort: the good board's posting got in.
    assert stats.inserted == 1


def test_unknown_source_type_marks_row_skipped_and_records_reason(session, settings):
    session.add(Source(source_type="bogus", token="ghost", enabled=True))
    session.commit()
    run_ingest(session, settings, source_factories={"fake": _ScriptedSource})

    row = session.query(Source).filter_by(token="ghost").one()
    assert row.last_status == "skipped"
    assert row.last_error and "unknown" in row.last_error.lower()


def test_disabled_source_telemetry_untouched(session, settings):
    """A parked (`enabled=False`) row keeps whatever telemetry it had —
    ingest must not read or write it."""
    parked = Source(
        source_type="fake",
        token="parked",
        enabled=False,
        last_status="success",
        jobs_found_last_run=42,
    )
    session.add(parked)
    session.add(Source(source_type="fake", token="live", enabled=True))
    session.commit()

    src_cls = lambda: _ScriptedSource(healthy={"live": [_nj("x")]})  # noqa: E731
    run_ingest(session, settings, source_factories={"fake": src_cls})

    refreshed = session.query(Source).filter_by(token="parked").one()
    assert refreshed.last_status == "success"
    assert refreshed.jobs_found_last_run == 42
    # last_run_at was never set on the parked row.
    assert refreshed.last_run_at is None
