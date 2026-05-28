"""Migration 0008 + auto-disable threshold tests.

End-to-end:
  * Running every migration to head populates the `sources` table with
    the existing seed AND the ~800 candidate rows from
    `infra/company_seed.tsv`.
  * `location` and `consecutive_failures` columns exist with the
    expected defaults.
  * The candidate seed is idempotent across a downgrade/upgrade cycle.

Auto-disable:
  * Each successive `STATUS_ERROR` increments `consecutive_failures`;
    once the configured threshold is hit the row gets `enabled=False`
    and shows up in `stats.boards_auto_disabled`.
  * A success in between resets the counter — a flaky board doesn't
    get parked.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterable

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
from app.sources.seed_loader import candidate_rows

_SEED_PATH = pathlib.Path(__file__).resolve().parents[2] / "infra" / "company_seed.tsv"


def _alembic_config(db_url: str) -> Config:
    backend_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    from app import config as config_module

    db_path = tmp_path / "bulk.sqlite"
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


# ── Schema + bulk-seed ─────────────────────────────────────────────────────


def test_migration_adds_location_and_consecutive_failures_columns(migrated_db):
    Session, _ = migrated_db
    with Session() as s:
        # Reach for the underlying connection to ask SQLite what columns
        # the table actually has — guards against the model being out of
        # sync with the migration.
        cols = {row[1] for row in s.connection().exec_driver_sql("PRAGMA table_info(sources)")}
    assert "location" in cols
    assert "consecutive_failures" in cols


def test_migration_seeds_candidate_rows_from_tsv(migrated_db):
    Session, _ = migrated_db
    expected = candidate_rows(_SEED_PATH)
    expected_pairs = {(r["source_type"], r["token"]) for r in expected}

    with Session() as s:
        actual_pairs = set(s.execute(select(Source.source_type, Source.token)).all())

    # Every candidate (source_type, token) is present. Older seed rows
    # from migration 0007 are also present so we check subset, not
    # equality.
    missing = expected_pairs - actual_pairs
    assert not missing, f"{len(missing)} candidate rows missing after migration"


def test_seeded_candidates_default_to_enabled_with_zero_failures(migrated_db):
    Session, _ = migrated_db
    with Session() as s:
        rows = s.execute(select(Source).where(Source.display_name == "23andMe")).scalars().all()
    # Migration 0008 seeds greenhouse + lever for every TSV row;
    # migration 0010 adds ashby. All three are candidates against
    # the same slugified token until auto-prune narrows it.
    assert {r.source_type for r in rows} == {"greenhouse", "lever", "ashby"}
    for r in rows:
        assert r.enabled is True
        assert r.consecutive_failures == 0
        assert r.location == "Mountain View, CA"


def test_total_row_count_matches_seed_plus_existing(migrated_db):
    """Sanity check: the row count after running every migration equals
    the existing seed (from companies.py) plus the candidate seeds,
    minus any overlap. Bounded loosely so one-off additions don't
    require updating the literal."""
    Session, _ = migrated_db
    with Session() as s:
        total = s.execute(select(Source)).scalars().all()
    # 0008 → ~834 candidates (greenhouse + lever)
    # 0009 → 1 row (GM workday)
    # 0010 → ~417 candidates (ashby) + 17 known-Ashby tokens
    # 0014 → ~450 new rows across all 5 platforms (greenhouse + lever
    #        partially overlap with 0008's slugified fan-out)
    # 0007 → 38 existing seed rows (some overlap with 0008/0010)
    # Total lands around 1600-1800 in practice — re-bump when the
    # next bulk-load migration ships.
    assert len(total) > 1500
    assert len(total) < 2000


def test_bulk_seed_is_idempotent_across_downgrade_upgrade(migrated_db):
    """Re-running the seed must not duplicate rows or trip the unique
    constraint."""
    Session, cfg = migrated_db
    with Session() as s:
        first = len(s.execute(select(Source)).scalars().all())

    command.downgrade(cfg, "0006_ingest_runs")
    command.upgrade(cfg, "head")

    with Session() as s:
        second = len(s.execute(select(Source)).scalars().all())
    assert first == second


# ── Auto-disable threshold ─────────────────────────────────────────────────


def _nj(external_id: str) -> NormalizedJob:
    from datetime import UTC, datetime, timedelta

    ts = datetime.now(UTC) - timedelta(hours=1)
    return NormalizedJob(
        source="fake",
        external_id=external_id,
        company="acme",
        title="Engineer",
        url=f"https://example.com/{external_id}",
        source_updated_at=ts,
        posted_at=ts,
        skills=[],
    )


class _ToggleSource(JobSource):
    """Returns postings on the runs where `healthy=True`, raises otherwise.
    `healthy` is a list whose i-th element controls the i-th call."""

    name = "fake"

    def __init__(self, schedule: list[bool]) -> None:
        self._schedule = list(schedule)

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        ok = self._schedule.pop(0)
        if not ok:
            raise SourceUnavailable(f"fake:{token} 404")
        return [_nj(f"{token}-1")]


@pytest.fixture
def session():
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
        SOURCE_FAILURE_THRESHOLD=3,
    )


def test_consecutive_failures_resets_on_success(session, settings):
    session.add(Source(source_type="fake", token="acme", enabled=True))
    session.commit()
    schedule = [False, True]  # one error, then a success
    schedules = iter([_ToggleSource(schedule)] * 2)

    def factory():
        return next(schedules)

    # First run errors → consecutive_failures = 1.
    run_ingest(session, settings, source_factories={"fake": factory})
    row = session.query(Source).filter_by(token="acme").one()
    assert row.consecutive_failures == 1
    assert row.enabled is True

    # Second run succeeds → counter resets.
    run_ingest(session, settings, source_factories={"fake": factory})
    session.refresh(row)
    assert row.consecutive_failures == 0
    assert row.enabled is True


def test_auto_disabled_after_threshold_consecutive_failures(session, settings):
    session.add(Source(source_type="fake", token="acme", enabled=True))
    session.commit()
    # Threshold is 3 (from the settings fixture). After three consecutive
    # error runs, the row must be flipped to `enabled=False`.
    for _ in range(3):
        # New source instance each run so the schedule is fresh.
        run_ingest(
            session,
            settings,
            source_factories={"fake": lambda: _ToggleSource([False])},  # noqa: B023
        )
    row = session.query(Source).filter_by(token="acme").one()
    assert row.consecutive_failures == 3
    assert row.enabled is False


def test_disabled_source_skipped_by_subsequent_run(session, settings):
    """Once a candidate is auto-disabled, the next run must not probe it
    again — that's the whole point of auto-disable."""
    session.add(Source(source_type="fake", token="acme", enabled=True))
    session.commit()
    for _ in range(3):
        run_ingest(
            session,
            settings,
            source_factories={"fake": lambda: _ToggleSource([False])},  # noqa: B023
        )

    # The candidate is now disabled. Run again with a source that would
    # raise if called; if anything reaches it, the assertion fires.
    class _Trip(JobSource):
        name = "fake"

        def fetch(self, token: str):
            raise AssertionError("disabled row should not be fetched")

    stats = run_ingest(session, settings, source_factories={"fake": _Trip})
    assert stats.boards_attempted == 0


def test_boards_auto_disabled_reported_in_stats(session, settings):
    session.add(Source(source_type="fake", token="acme", enabled=True))
    session.commit()

    last_stats = None
    for _ in range(3):
        last_stats = run_ingest(
            session,
            settings,
            source_factories={"fake": lambda: _ToggleSource([False])},  # noqa: B023
        )
    assert last_stats is not None
    assert last_stats.boards_auto_disabled == ["fake:acme"]
