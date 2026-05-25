"""End-to-end ingest service tests using an in-memory SQLite DB and a
fake JobSource — covers dedupe, upsert, 48h expiry, and unreachable boards.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models.job import Job
from app.services.ingest import run_ingest
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _nj(
    *,
    source: str = "fake",
    external_id: str,
    company: str = "acme",
    title: str = "Engineer",
    age_hours: float = 1.0,
    description: str | None = None,
    skills: list[str] | None = None,
) -> NormalizedJob:
    ts = _utcnow() - timedelta(hours=age_hours)
    return NormalizedJob(
        source=source,
        external_id=external_id,
        company=company,
        title=title,
        url=f"https://example.com/{external_id}",
        source_updated_at=ts,
        posted_at=ts,
        description=description,
        skills=skills or [],
    )


class StaticSource(JobSource):
    """Test double — returns whatever postings are handed to it."""

    name = "fake"

    def __init__(
        self,
        by_token: dict[str, list[NormalizedJob]] | None = None,
        unavailable: set[str] | None = None,
    ) -> None:
        self._by_token = by_token or {}
        self._unavailable = unavailable or set()

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        if token in self._unavailable:
            raise SourceUnavailable(f"fake:{token} not found")
        return list(self._by_token.get(token, []))


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        yield s


@pytest.fixture
def settings() -> Settings:
    return Settings(DATABASE_URL="sqlite+pysqlite:///:memory:", ADMIN_TOKEN="t", HOURS_WINDOW=48)


def test_inserts_new_postings(db_session, settings):
    source_cls = lambda: StaticSource(  # noqa: E731
        by_token={"acme": [_nj(external_id="1"), _nj(external_id="2")]}
    )
    stats = run_ingest(
        db_session, settings, companies=[("fake", "acme")], source_factories={"fake": source_cls}
    )
    assert stats.inserted == 2
    assert stats.updated == 0
    assert stats.boards_failed == 0
    assert db_session.query(Job).count() == 2


def test_skips_postings_outside_window(db_session, settings):
    inside = _nj(external_id="fresh", age_hours=1)
    outside = _nj(external_id="stale", age_hours=72)  # > 48h window
    source_cls = lambda: StaticSource(by_token={"acme": [inside, outside]})  # noqa: E731
    stats = run_ingest(
        db_session, settings, companies=[("fake", "acme")], source_factories={"fake": source_cls}
    )
    assert stats.inserted == 1
    assert stats.skipped_outside_window == 1
    assert db_session.query(Job).count() == 1
    assert db_session.query(Job).one().external_id == "fresh"


def test_deletes_expired_rows_on_cleanup(db_session, settings):
    # Pre-seed an old row directly.
    old = Job(
        source="fake",
        external_id="ancient",
        company="acme",
        title="Old role",
        url="https://example.com/ancient",
        source_updated_at=_utcnow() - timedelta(hours=100),
        skills=[],
    )
    db_session.add(old)
    db_session.commit()
    assert db_session.query(Job).count() == 1

    source_cls = lambda: StaticSource(by_token={"acme": [_nj(external_id="new")]})  # noqa: E731
    stats = run_ingest(
        db_session, settings, companies=[("fake", "acme")], source_factories={"fake": source_cls}
    )
    assert stats.deleted_expired == 1
    assert stats.inserted == 1
    titles = [j.external_id for j in db_session.query(Job).all()]
    assert titles == ["new"]


def test_dedupes_across_runs_and_updates_changed_content(db_session, settings):
    # Use a fixed timestamp so the "no-op on second run" assertion is meaningful
    # (otherwise source_updated_at moves every call and an update fires).
    ts = _utcnow() - timedelta(hours=1)
    common = dict(
        source="fake",
        external_id="job-1",
        company="acme",
        url="https://example.com/job-1",
        source_updated_at=ts,
        posted_at=ts,
        description="Python and React",
    )
    nj_v1 = NormalizedJob(title="Engineer", skills=[], **common)
    nj_v2 = NormalizedJob(title="Senior Engineer", skills=[], **common)

    source_cls = lambda postings: lambda: StaticSource(by_token={"acme": postings})  # noqa: E731

    # First run inserts.
    stats1 = run_ingest(
        db_session,
        settings,
        companies=[("fake", "acme")],
        source_factories={"fake": source_cls([nj_v1])},
    )
    assert stats1.inserted == 1

    # Second run with same content + same timestamp → no-op.
    stats2 = run_ingest(
        db_session,
        settings,
        companies=[("fake", "acme")],
        source_factories={"fake": source_cls([nj_v1])},
    )
    assert stats2.inserted == 0
    assert stats2.updated == 0

    # Third run with changed title → update.
    stats3 = run_ingest(
        db_session,
        settings,
        companies=[("fake", "acme")],
        source_factories={"fake": source_cls([nj_v2])},
    )
    assert stats3.inserted == 0
    assert stats3.updated == 1
    assert db_session.query(Job).one().title == "Senior Engineer"


def test_unreachable_board_is_skipped_not_fatal(db_session, settings):
    source_cls = lambda: StaticSource(  # noqa: E731
        by_token={"good": [_nj(external_id="1")]},
        unavailable={"bad"},
    )
    stats = run_ingest(
        db_session,
        settings,
        companies=[("fake", "good"), ("fake", "bad")],
        source_factories={"fake": source_cls},
    )
    assert stats.inserted == 1
    assert stats.boards_attempted == 2
    assert stats.boards_failed == 1
    assert any("bad" in f["board"] for f in stats.boards_failures)


def test_dedupes_within_a_single_run(db_session, settings):
    # Same (source, external_id) appearing twice in one batch — second is dropped.
    dup_a = _nj(external_id="dup")
    dup_b = _nj(external_id="dup", title="Different title")
    source_cls = lambda: StaticSource(by_token={"acme": [dup_a, dup_b]})  # noqa: E731
    stats = run_ingest(
        db_session, settings, companies=[("fake", "acme")], source_factories={"fake": source_cls}
    )
    assert stats.inserted == 1
    assert stats.skipped_duplicates == 1
    assert db_session.query(Job).count() == 1
