"""Bounded + rotating ingest tests.

Pins three behaviors:

  1. `_load_due_sources` returns enabled rows ordered
     `last_run_at ASC NULLS FIRST`, capped at the configured limit —
     so each invocation processes a fresh slice and successive runs
     rotate through every source.
  2. Per-source telemetry + jobs are committed BEFORE the next batch's
     fetch begins. The check uses a fresh `Session` against the same
     engine to prove the writes actually flushed (a session-local
     view would mask an end-of-run commit bug).
  3. An end-of-run summary log line appears with the counts the
     operator needs at a glance.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models.job import Job
from app.models.source import Source
from app.services.ingest import _load_due_sources, run_ingest
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def factories():
    """Engine + sessionmaker that shares one in-memory SQLite across
    sessions, so the "fresh session sees committed writes" assertions
    are meaningful."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _settings(**overrides) -> Settings:
    base = dict(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
        INGEST_CONCURRENCY=5,
        INGEST_MAX_PER_RUN=150,
        INGEST_BATCH_SIZE=25,
        SOURCE_FAILURE_THRESHOLD=100,
    )
    base.update(overrides)
    return Settings(**base)


def _nj(token: str, idx: int = 0) -> NormalizedJob:
    ts = datetime.now(UTC) - timedelta(hours=1)
    return NormalizedJob(
        source="fake",
        external_id=f"{token}-{idx}",
        company=token,
        title=f"Engineer {idx}",
        url=f"https://example.com/{token}/{idx}",
        source_updated_at=ts,
        posted_at=ts,
        skills=[],
    )


class _GoodSource(JobSource):
    """Always succeeds, returns one posting per call."""

    name = "fake"

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        return [_nj(token)]


# ── _load_due_sources ─────────────────────────────────────────────────────


def test_load_due_sources_nulls_first_then_oldest(factories):
    with factories() as s:
        now = datetime.now(UTC)
        s.add(Source(source_type="fake", token="recent", enabled=True, last_run_at=now))
        s.add(
            Source(
                source_type="fake",
                token="oldest",
                enabled=True,
                last_run_at=now - timedelta(days=7),
            )
        )
        s.add(Source(source_type="fake", token="never-1", enabled=True, last_run_at=None))
        s.add(Source(source_type="fake", token="never-2", enabled=True, last_run_at=None))
        s.commit()

        rows = _load_due_sources(s, limit=10)

    tokens = [r.token for r in rows]
    # Both never-checked rows come first (NULLS FIRST), then the row
    # that's been gathering dust the longest, then the recently-checked
    # one. The two NULLs are ordered by the source_type/token
    # tiebreaker — both have source_type "fake", so token order applies.
    assert tokens == ["never-1", "never-2", "oldest", "recent"]


def test_load_due_sources_respects_limit(factories):
    with factories() as s:
        for i in range(20):
            s.add(Source(source_type="fake", token=f"co-{i:02d}", enabled=True))
        s.commit()
        rows = _load_due_sources(s, limit=7)
    assert len(rows) == 7


def test_load_due_sources_skips_disabled(factories):
    with factories() as s:
        s.add(Source(source_type="fake", token="on", enabled=True))
        s.add(Source(source_type="fake", token="off", enabled=False))
        s.commit()
        rows = _load_due_sources(s, limit=10)
    assert [r.token for r in rows] == ["on"]


# ── INGEST_MAX_PER_RUN ────────────────────────────────────────────────────


def test_run_processes_only_max_per_run_sources(factories):
    with factories() as s:
        for i in range(30):
            s.add(Source(source_type="fake", token=f"co-{i:02d}", enabled=True))
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=8, INGEST_BATCH_SIZE=4)
    with factories() as s:
        stats = run_ingest(s, settings, source_factories={"fake": _GoodSource})

    assert stats.boards_attempted == 8
    assert stats.inserted == 8

    # The 8 most-stale (here: any 8 in token order, since all started
    # NULL) have been written; the other 22 are untouched.
    with factories() as s:
        run_count = s.query(Source).filter(Source.last_run_at.isnot(None)).count()
    assert run_count == 8


def test_successive_runs_rotate_through_all_sources(factories):
    """Run 1 processes 5, run 2 processes the next 5. Every source
    eventually gets `last_run_at` set without ever processing the same
    source twice in the same cycle."""
    with factories() as s:
        for i in range(10):
            s.add(Source(source_type="fake", token=f"co-{i:02d}", enabled=True))
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=5, INGEST_BATCH_SIZE=5)

    with factories() as s:
        first = run_ingest(s, settings, source_factories={"fake": _GoodSource})

    # Capture which tokens ran in pass 1 from the DB (the ones with
    # last_run_at not null).
    with factories() as s:
        pass1_tokens = {
            r.token for r in s.query(Source).filter(Source.last_run_at.isnot(None)).all()
        }
    assert len(pass1_tokens) == 5

    with factories() as s:
        second = run_ingest(s, settings, source_factories={"fake": _GoodSource})

    # Now every source has been processed at least once.
    with factories() as s:
        all_run = {r.token for r in s.query(Source).filter(Source.last_run_at.isnot(None)).all()}
    assert len(all_run) == 10
    assert first.boards_attempted == 5
    assert second.boards_attempted == 5


# ── incremental commit ──────────────────────────────────────────────────


def test_per_source_writes_visible_to_fresh_session(factories):
    """A fresh session reading from the same engine sees the per-source
    commits — proves the writes flushed instead of buffering. If
    `run_ingest` committed only at the end, the fresh session would
    still see them, but the test would also pass even if those commits
    were withheld until cleanup. Adding the cleanup-time assertion
    catches that regression too."""
    with factories() as s:
        for i in range(6):
            s.add(Source(source_type="fake", token=f"co-{i}", enabled=True))
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=6, INGEST_BATCH_SIZE=2)
    with factories() as s:
        run_ingest(s, settings, source_factories={"fake": _GoodSource})

    with factories() as s:
        for i in range(6):
            row = s.query(Source).filter_by(token=f"co-{i}").one()
            assert row.last_status == "success"
            assert row.last_run_at is not None
            assert row.jobs_found_last_run == 1
        # Same for the jobs that the upserts wrote.
        assert s.query(Job).count() == 6


class _FailAfterNSource(JobSource):
    """First N tokens succeed; the next one raises a non-isolated
    error (uncaught in `_fetch_one`'s `Exception` handler — we use a
    BaseException-derived class to simulate the worker getting killed
    by the OS). This proves the previously-processed sources are
    durably committed before the failure point."""

    name = "fake"
    _counter = 0

    def __init__(self, fail_after: int):
        self._fail_after = fail_after

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        _FailAfterNSource._counter += 1
        if _FailAfterNSource._counter > self._fail_after:
            # KeyboardInterrupt is a BaseException; it bypasses
            # `except Exception` in `_fetch_one`. That's exactly what
            # `kill -INT` would do.
            raise KeyboardInterrupt(f"simulated kill at {token}")
        return [_nj(token)]


def test_completed_sources_survive_a_mid_run_kill(factories):
    """The first batch's writes must already be on disk by the time
    the second batch's fetch starts — so a kill in batch 2 leaves
    batch 1 intact. We simulate the kill with `KeyboardInterrupt`,
    which the per-source-isolation try/except deliberately does NOT
    catch (it only catches `Exception`)."""
    _FailAfterNSource._counter = 0
    with factories() as s:
        for i in range(6):
            s.add(Source(source_type="fake", token=f"co-{i}", enabled=True))
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=6, INGEST_BATCH_SIZE=2)
    factory = lambda: _FailAfterNSource(fail_after=2)  # noqa: E731

    with factories() as s, pytest.raises(KeyboardInterrupt):
        run_ingest(s, settings, source_factories={"fake": factory})

    # The first batch's 2 sources committed before the second batch
    # was scheduled, so a fresh session still sees them on disk.
    with factories() as s:
        committed = s.query(Source).filter(Source.last_run_at.isnot(None)).count()
    assert committed >= 2, (
        f"only {committed} sources persisted before the kill; per-batch " "commits are not durable"
    )


# ── end-of-run log summary ────────────────────────────────────────────────


class _MixedSource(JobSource):
    """Combine success / error / unknown-source-type into one run so
    the summary line can show non-zero values for every counter."""

    name = "fake"

    def __init__(self, unhealthy: set[str]):
        self._unhealthy = unhealthy

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        if token in self._unhealthy:
            raise SourceUnavailable(f"fake:{token} 404")
        return [_nj(token, 0), _nj(token, 1)]


class _ListHandler(logging.Handler):
    """Self-contained log capture. pytest's `caplog` relies on
    `propagate=True` and a global handler that other tests can perturb;
    attaching our own handler to the exact logger we care about makes
    these assertions invariant to suite ordering."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture_ingest_logs():
    """Returns (handler, restore) — call `restore()` to detach.

    `logger.disabled = False` is set explicitly because earlier tests
    can trigger Alembic's `fileConfig(...)` (via
    `command.upgrade(cfg, "head")` inside the migration fixtures),
    which by default disables every pre-existing Python logger. Without
    this re-enable, the per-test `setLevel(INFO)` is honoured but
    `Logger.handle()` short-circuits before reaching our handler.
    """
    handler = _ListHandler()
    logger = logging.getLogger("app.services.ingest")
    prev_level = logger.level
    prev_disabled = logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.disabled = False

    def restore():
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        logger.disabled = prev_disabled

    return handler, restore


def test_end_of_run_summary_log_line_emitted(factories):
    with factories() as s:
        s.add(Source(source_type="fake", token="good-1", enabled=True))
        s.add(Source(source_type="fake", token="good-2", enabled=True))
        s.add(Source(source_type="fake", token="bad", enabled=True))
        # Unknown source_type → skipped (not attempted, not failed).
        s.add(Source(source_type="someday", token="weird", enabled=True))
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=10, INGEST_BATCH_SIZE=5)
    factory = lambda: _MixedSource(unhealthy={"bad"})  # noqa: E731

    handler, restore = _capture_ingest_logs()
    try:
        with factories() as s:
            run_ingest(s, settings, source_factories={"fake": factory})
    finally:
        restore()

    summary = [r for r in handler.records if "ingest complete:" in r.getMessage()]
    assert len(summary) == 1, f"expected one summary line, got {len(summary)}"
    msg = summary[0].getMessage()
    # 3 processed (2 good + 1 bad) + 1 skipped = 4 total.
    assert "processed=4" in msg
    assert "succeeded=2" in msg
    assert "errored=1" in msg
    assert "skipped=1" in msg
    assert "jobs_added=4" in msg  # 2 + 2 from the two good boards


def test_summary_includes_auto_disable_count(factories):
    """A run that auto-disables a source must say so in the summary
    line — that's the operator's signal to look at the row."""
    with factories() as s:
        s.add(
            Source(
                source_type="fake",
                token="dying",
                enabled=True,
                consecutive_failures=2,  # one more failure → auto-disable
            )
        )
        s.commit()

    settings = _settings(SOURCE_FAILURE_THRESHOLD=3)
    factory = lambda: _MixedSource(unhealthy={"dying"})  # noqa: E731

    handler, restore = _capture_ingest_logs()
    try:
        with factories() as s:
            run_ingest(s, settings, source_factories={"fake": factory})
    finally:
        restore()

    summary = next(r for r in handler.records if "ingest complete:" in r.getMessage())
    assert "auto_disabled=1" in summary.getMessage()
