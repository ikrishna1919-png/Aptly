"""Ingest survivability + observability: heartbeat, stale reporting, reaper.

The worker writes its terminal status only at the END of a full pass. On a
sleepy free-tier host the thread can be killed mid-run, leaving the IngestRun
row stuck at `running` with an empty `{}` forever. These tests pin the three
mechanisms that fix that:

  (a) a run that dies mid-pass keeps its last per-batch heartbeat on the row;
  (b) the next trigger reaps an abandoned `running` row to `failed`;
  (c) INGEST_MAX_PER_RUN<=0 loads ALL enabled sources in one pass;

plus the read-only `effective_run_status` that reports `stale` without mutating.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models.ingest_run import (
    INGEST_STATUS_FAILED,
    INGEST_STATUS_RUNNING,
    IngestRun,
)
from app.models.source import Source
from app.services import ingest as ingest_module
from app.services.ingest import (
    IngestStats,
    _FetchOutcome,
    _process_outcome,
    _SourceRef,
    effective_run_status,
    run_ingest,
)
from app.sources.base import JobSource, NormalizedJob

# ── fixtures / helpers ──────────────────────────────────────────────────────


@pytest.fixture
def factories():
    """Engine + sessionmaker sharing one in-memory SQLite across sessions, so
    'fresh session sees committed writes' assertions are meaningful."""
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
        STALE_RUN_MINUTES=15,
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
    name = "fake"

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        return [_nj(token)]


class _FailAfterNSource(JobSource):
    """First N fetches succeed; the next raises KeyboardInterrupt — a
    BaseException that bypasses the `except Exception` per-source isolation
    in `_fetch_one`, exactly like the OS killing the worker mid-run."""

    name = "fake"
    _counter = 0

    def __init__(self, fail_after: int):
        self._fail_after = fail_after

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        _FailAfterNSource._counter += 1
        if _FailAfterNSource._counter > self._fail_after:
            raise KeyboardInterrupt(f"simulated kill at {token}")
        return [_nj(token)]


# ── (a) heartbeat survives a mid-run kill ───────────────────────────────────


def test_heartbeat_persists_when_run_dies_mid_pass(factories):
    """Batch 1 commits a heartbeat; batch 2 is killed. The row must still
    show batch-1 progress (not the empty {} it started with)."""
    _FailAfterNSource._counter = 0
    run_id = "hb-dies"
    with factories() as s:
        for i in range(4):
            s.add(Source(source_type="fake", token=f"co-{i}", enabled=True))
        s.add(IngestRun(run_id=run_id, status=INGEST_STATUS_RUNNING, stats={}))
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=4, INGEST_BATCH_SIZE=2)
    factory = lambda: _FailAfterNSource(fail_after=2)  # noqa: E731

    # batch 1 (co-0, co-1) succeeds + heartbeats; batch 2 raises mid-fetch.
    with factories() as s, pytest.raises(KeyboardInterrupt):
        run_ingest(s, settings, run_id=run_id, source_factories={"fake": factory})

    # A fresh session proves the heartbeat actually committed to disk.
    with factories() as s:
        run = s.execute(select(IngestRun).where(IngestRun.run_id == run_id)).scalar_one()

    # The worker never reached its terminal write, so the row is still
    # `running` — but it is no longer an empty snapshot.
    assert run.status == INGEST_STATUS_RUNNING
    assert run.stats, "heartbeat snapshot missing — row still shows empty {}"
    assert run.stats.get("last_progress_at"), "heartbeat must stamp last_progress_at"
    assert run.stats.get("inserted", 0) >= 1, "heartbeat must carry batch-1 progress"


def test_no_heartbeat_written_without_run_id(factories):
    """run_ingest called outside the worker (run_id=None) must not touch any
    IngestRun row — CLI/direct callers don't have one."""
    with factories() as s:
        s.add(Source(source_type="fake", token="co", enabled=True))
        s.commit()
    settings = _settings(INGEST_MAX_PER_RUN=5, INGEST_BATCH_SIZE=5)
    with factories() as s:
        run_ingest(s, settings, source_factories={"fake": _GoodSource})  # no run_id
    with factories() as s:
        assert s.execute(select(IngestRun)).scalars().all() == []


# ── (b) reaper self-heals abandoned runs on the next trigger ─────────────────


def test_start_background_ingest_reaps_stale_running(factories, monkeypatch):
    """A `running` row whose start is older than STALE_RUN_MINUTES gets
    flipped to `failed` when the next run is triggered; the new run is live."""
    monkeypatch.setattr(ingest_module, "SessionLocal", factories)
    # Don't actually launch the worker — we're only testing the reaper.
    monkeypatch.setattr(ingest_module, "_launch_worker", lambda target, args: None)

    settings = _settings(STALE_RUN_MINUTES=15)
    stale_start = datetime.now(UTC) - timedelta(minutes=30)
    fresh_start = datetime.now(UTC) - timedelta(minutes=2)
    with factories() as s:
        s.add(
            IngestRun(
                run_id="old-dead", status=INGEST_STATUS_RUNNING, stats={}, started_at=stale_start
            )
        )
        # A genuinely-fresh running row must NOT be reaped.
        s.add(
            IngestRun(
                run_id="young-live", status=INGEST_STATUS_RUNNING, stats={}, started_at=fresh_start
            )
        )
        s.commit()

    new_id = ingest_module.start_background_ingest(settings)

    with factories() as s:
        old = s.execute(select(IngestRun).where(IngestRun.run_id == "old-dead")).scalar_one()
        young = s.execute(select(IngestRun).where(IngestRun.run_id == "young-live")).scalar_one()
        new = s.execute(select(IngestRun).where(IngestRun.run_id == new_id)).scalar_one()

    assert old.status == INGEST_STATUS_FAILED
    assert "stale" in (old.error or "")
    assert old.finished_at is not None
    assert young.status == INGEST_STATUS_RUNNING  # too young to reap
    assert new.status == INGEST_STATUS_RUNNING  # the freshly-created run


# ── (c) INGEST_MAX_PER_RUN<=0 → all enabled sources ─────────────────────────


def test_max_per_run_zero_loads_all_enabled_sources(factories):
    with factories() as s:
        for i in range(12):
            s.add(Source(source_type="fake", token=f"co-{i:02d}", enabled=True))
        s.add(Source(source_type="fake", token="off", enabled=False))  # excluded
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=0, INGEST_BATCH_SIZE=5)
    with factories() as s:
        stats = run_ingest(s, settings, source_factories={"fake": _GoodSource})

    # All 12 enabled sources processed in one pass; the disabled one skipped.
    assert stats.boards_attempted == 12
    assert stats.inserted == 12
    with factories() as s:
        ran = s.query(Source).filter(Source.last_run_at.isnot(None)).count()
        off = s.query(Source).filter_by(token="off").one()
    assert ran == 12
    assert off.last_run_at is None


# ── effective_run_status: read-only stale reporting ─────────────────────────


def _run(status: str, *, started_min_ago: float, last_progress_min_ago: float | None = None):
    stats: dict = {}
    if last_progress_min_ago is not None:
        stats["last_progress_at"] = (
            datetime.now(UTC) - timedelta(minutes=last_progress_min_ago)
        ).isoformat()
    return IngestRun(
        run_id="x",
        status=status,
        stats=stats,
        started_at=datetime.now(UTC) - timedelta(minutes=started_min_ago),
    )


def test_effective_status_reports_stale_only_for_quiet_running_rows():
    settings = _settings(STALE_RUN_MINUTES=15)

    # Fresh running run → running.
    assert effective_run_status(_run("running", started_min_ago=2), settings) == "running"
    # Running but silent for 30 min (no heartbeat) → stale.
    assert effective_run_status(_run("running", started_min_ago=30), settings) == "stale"
    # Old start but a recent heartbeat → still running (it's alive).
    assert (
        effective_run_status(_run("running", started_min_ago=60, last_progress_min_ago=1), settings)
        == "running"
    )
    # Old heartbeat → stale even though it once made progress.
    assert (
        effective_run_status(
            _run("running", started_min_ago=60, last_progress_min_ago=40), settings
        )
        == "stale"
    )
    # Terminal statuses pass through untouched.
    assert effective_run_status(_run("success", started_min_ago=99), settings) == "success"
    assert effective_run_status(_run("failed", started_min_ago=99), settings) == "failed"


def test_effective_status_does_not_mutate_the_row():
    settings = _settings(STALE_RUN_MINUTES=15)
    run = _run("running", started_min_ago=30)
    assert effective_run_status(run, settings) == "stale"
    # The persisted column is unchanged — only the reaper mutates.
    assert run.status == "running"


# ── observable from the first source: initial + per-source heartbeats ────────


def _record_heartbeats(monkeypatch):
    """Capture (attempted, total) at each heartbeat call, still invoking the
    real writer so the row is updated too."""
    seen: list[dict] = []
    real = ingest_module._write_heartbeat

    def rec(db, run_id, stats):
        seen.append({"attempted": stats.boards_attempted, "total": stats.total_sources})
        return real(db, run_id, stats)

    monkeypatch.setattr(ingest_module, "_write_heartbeat", rec)
    return seen


def test_initial_heartbeat_before_first_batch_has_total_sources(factories, monkeypatch):
    """(a) The very first heartbeat fires BEFORE any source is processed and
    already carries the planned size — a live run is distinguishable from a
    dead {} within seconds, not after batch 1's 25 fetches resolve."""
    seen = _record_heartbeats(monkeypatch)
    with factories() as s:
        for i in range(3):
            s.add(Source(source_type="fake", token=f"co-{i}", enabled=True))
        s.add(IngestRun(run_id="hb-init", status=INGEST_STATUS_RUNNING, stats={}))
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=3, INGEST_BATCH_SIZE=25)
    with factories() as s:
        run_ingest(s, settings, run_id="hb-init", source_factories={"fake": _GoodSource})

    assert seen, "no heartbeats recorded"
    assert seen[0]["attempted"] == 0
    assert seen[0]["total"] == 3


def test_attempted_advances_per_source(factories, monkeypatch):
    """(b) `attempted` advances one-by-one (initial 0, then +1 per source),
    not in batch-sized jumps."""
    seen = _record_heartbeats(monkeypatch)
    with factories() as s:
        for i in range(4):
            s.add(Source(source_type="fake", token=f"co-{i}", enabled=True))
        s.add(IngestRun(run_id="hb-adv", status=INGEST_STATUS_RUNNING, stats={}))
        s.commit()

    settings = _settings(INGEST_MAX_PER_RUN=4, INGEST_BATCH_SIZE=25)  # one batch
    with factories() as s:
        run_ingest(s, settings, run_id="hb-adv", source_factories={"fake": _GoodSource})

    assert [h["attempted"] for h in seen] == [0, 1, 2, 3, 4]


class _SlowSource(JobSource):
    """Each fetch sleeps comfortably past the 1s test budget, so the budget is
    blown after the first batch — deterministically, with a real sleep and a
    generous margin (budget is integer seconds, so it can't be sub-second)."""

    name = "fake"

    def fetch(self, token):
        time.sleep(1.2)
        return [_nj(token)]


def test_budget_stops_starting_batches_and_still_succeeds(factories):
    """(c) With a soft budget and many slow sources, the run stops starting
    new batches once the budget is exceeded and still ends cleanly (success,
    with budget_truncated) — rotation covers the rest next run."""
    with factories() as s:
        for i in range(6):
            s.add(Source(source_type="fake", token=f"co-{i}", enabled=True))
        s.commit()

    # batch_size=1 → one slow (1.2s) source per batch; budget 1s. Batch 1 runs
    # (elapsed ~0 at its pre-check); the pre-check before batch 2 sees ~1.2s >=
    # 1s and stops. Only one source is ever fetched, so the test is ~1.2s.
    settings = _settings(INGEST_MAX_PER_RUN=6, INGEST_BATCH_SIZE=1, INGEST_RUN_BUDGET_SECONDS=1)
    with factories() as s:
        stats = run_ingest(s, settings, source_factories={"fake": _SlowSource})

    assert stats.budget_truncated is True
    assert stats.total_sources == 6
    assert stats.boards_attempted == 1, "should have stopped after the first batch"
    # Finished cleanly (cleanup ran, finished_at stamped) — the success path,
    # not an exception.
    assert stats.finished_at != ""


# ── transaction hygiene: no idle-in-transaction connection during fetches ────


def test_no_open_transaction_entering_fetch(factories, monkeypatch):
    """The Session must NOT be mid-transaction when the network fetch begins —
    otherwise the connection idles in-transaction through 20s-per-board I/O and
    Neon terminates it (IdleInTransactionSessionTimeout), killing the run before
    any telemetry commits. A probe records the tx state at each fetch entry."""
    with factories() as setup:
        for i in range(5):
            setup.add(Source(source_type="fake", token=f"co-{i}", enabled=True))
        setup.commit()

    settings = _settings(INGEST_MAX_PER_RUN=5, INGEST_BATCH_SIZE=2)  # 3 batches

    tx_at_fetch: list[bool] = []
    holder: dict = {}

    async def probe(source_refs, source_factories, concurrency, timeout):
        # Same Session the run uses — it must be idle (no open tx) right here,
        # before any awaited network work.
        tx_at_fetch.append(holder["db"].in_transaction())
        return [_FetchOutcome(ref=r, status="ok", postings=[_nj(r.token)]) for r in source_refs]

    monkeypatch.setattr(ingest_module, "_fetch_all_async", probe)

    with factories() as db:
        holder["db"] = db
        run_ingest(db, settings, run_id=None, source_factories={"fake": _GoodSource})

    assert tx_at_fetch, "fetch was never entered"
    assert all(
        state is False for state in tx_at_fetch
    ), f"session was mid-transaction entering fetch: {tx_at_fetch}"
    # Per-source telemetry still committed for every source.
    with factories() as s:
        assert s.query(Source).filter(Source.last_run_at.isnot(None)).count() == 5


def test_process_outcome_requeries_source_by_id_after_commit(factories):
    """_process_outcome re-loads the Source by id (it never holds an ORM object
    across the fetch/commit boundary), so the telemetry write can't hit a
    detached/expired instance — and lands on the right row."""
    with factories() as s:
        s.add(Source(source_type="fake", token="co-x", enabled=True))
        s.commit()
        src_id = s.query(Source).filter_by(token="co-x").one().id

    with factories() as s:
        # Commit so any ORM identity is expired (mimics the post-fetch state),
        # then process an outcome that references the source ONLY by id.
        s.commit()
        outcome = _FetchOutcome(
            ref=_SourceRef(id=src_id, source_type="fake", token="co-x"),
            status="ok",
            postings=[_nj("co-x")],
        )
        stats = IngestStats(window_hours=48)
        _process_outcome(
            s,
            outcome,
            stats=stats,
            window_start=datetime.now(UTC) - timedelta(hours=48),
            seen=set(),
            threshold=100,
        )

    with factories() as s:
        row = s.query(Source).filter_by(id=src_id).one()
    assert row.last_status == "success"
    assert row.last_run_at is not None
    assert row.jobs_found_last_run == 1
    assert stats.inserted == 1
