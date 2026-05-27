"""Tests for the parallel async fetch step inside `run_ingest`.

What's covered:
  * The async fetch step actually overlaps — N slow sources finish in
    well under N × per-source time.
  * `asyncio.Semaphore` bounds concurrency to `settings.ingest_concurrency`
    — peak in-flight tasks never exceed the configured limit.
  * Per-source isolation: one task raising `SourceUnavailable` or a
    generic exception does NOT cancel the sibling tasks; their results
    still land on `IngestStats` and on their `Source` rows.
  * Native-async overrides on `GreenhouseSource` and `LeverSource`
    actually use the supplied `httpx.AsyncClient` (httpx
    MockTransport-driven, no real network).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models.job import Job
from app.models.source import Source
from app.services.ingest import run_ingest
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable
from app.sources.greenhouse import GreenhouseSource
from app.sources.lever import LeverSource

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        yield s


def _settings(concurrency: int) -> Settings:
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
        INGEST_CONCURRENCY=concurrency,
        SOURCE_FAILURE_THRESHOLD=100,  # high so auto-disable doesn't interfere
    )


def _nj(token: str, idx: int) -> NormalizedJob:
    from datetime import UTC, datetime, timedelta

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


# ── concurrency tracker ──────────────────────────────────────────────────


class _ConcurrencyTracker:
    """Counts active overlapping fetches. Shared by all instances of
    `_SlowAsyncSource` in a single test so the test asserts the
    semaphore's effect across the whole gather, not within one task."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    def enter(self) -> None:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)

    def exit(self) -> None:
        self.in_flight -= 1


class _SlowAsyncSource(JobSource):
    """Native-async test source. Each `fetch_async` sleeps `delay`
    seconds (so the test can verify that N parallel fetches don't take
    N × delay), and records peak overlap in a shared tracker."""

    name = "fake"

    def __init__(
        self,
        tracker: _ConcurrencyTracker,
        per_token_postings: int = 1,
        delay: float = 0.05,
    ) -> None:
        self._tracker = tracker
        self._n = per_token_postings
        self._delay = delay

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        # Sync path unused by these tests, but must satisfy the abstract
        # method.
        raise NotImplementedError

    async def fetch_async(self, token: str, async_client=None):
        self._tracker.enter()
        try:
            await asyncio.sleep(self._delay)
            return [_nj(token, i) for i in range(self._n)]
        finally:
            self._tracker.exit()


def _seed(db, n: int) -> None:
    for i in range(n):
        db.add(Source(source_type="fake", token=f"co-{i}", enabled=True))
    db.commit()


# ── parallelization is real ──────────────────────────────────────────────


def test_async_fetch_overlaps_network_waits(db_session):
    """Ten sources, each sleeping 100 ms. With concurrency=10 the total
    wall time must be much less than 10 × 0.1s = 1.0s — we allow 0.5s
    headroom for scheduling jitter on slow CI."""
    n = 10
    _seed(db_session, n)
    tracker = _ConcurrencyTracker()
    factory = lambda: _SlowAsyncSource(tracker, delay=0.1)  # noqa: E731

    start = time.monotonic()
    stats = run_ingest(
        db_session,
        _settings(concurrency=10),
        source_factories={"fake": factory},
    )
    elapsed = time.monotonic() - start

    assert stats.inserted == n
    assert elapsed < 0.6, (
        f"ingest took {elapsed:.2f}s for {n} sources at concurrency=10; "
        "the network phase isn't overlapping"
    )
    assert tracker.peak >= 2, "fetches never ran in parallel"


def test_semaphore_caps_in_flight_at_configured_limit(db_session):
    """20 sources, concurrency=3 — the tracker's peak in-flight count
    must never exceed 3."""
    n = 20
    _seed(db_session, n)
    tracker = _ConcurrencyTracker()
    factory = lambda: _SlowAsyncSource(tracker, delay=0.02)  # noqa: E731

    run_ingest(
        db_session,
        _settings(concurrency=3),
        source_factories={"fake": factory},
    )

    assert tracker.peak <= 3, f"peak in-flight {tracker.peak} exceeded concurrency=3"
    assert tracker.peak >= 2, "expected at least some overlap"


def test_concurrency_one_serializes(db_session):
    """`INGEST_CONCURRENCY=1` is a documented escape hatch (e.g. for
    rate-limited vendors). Verify it actually serialises."""
    _seed(db_session, 5)
    tracker = _ConcurrencyTracker()
    factory = lambda: _SlowAsyncSource(tracker, delay=0.01)  # noqa: E731

    run_ingest(
        db_session,
        _settings(concurrency=1),
        source_factories={"fake": factory},
    )

    assert tracker.peak == 1


# ── per-source isolation ─────────────────────────────────────────────────


class _MixedAsyncSource(JobSource):
    """Healthy tokens return postings; unhealthy ones raise. Models the
    "one slow board times out while the rest succeed" path under the
    async orchestrator. The whole point: SourceUnavailable on one task
    must NOT cancel sibling tasks."""

    name = "fake"

    def __init__(self, healthy: dict[str, int], unhealthy: set[str]) -> None:
        self._healthy = healthy
        self._unhealthy = unhealthy

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        raise NotImplementedError

    async def fetch_async(self, token: str, async_client=None):
        await asyncio.sleep(0.005)
        if token in self._unhealthy:
            raise SourceUnavailable(f"fake:{token} ConnectTimeout")
        return [_nj(token, i) for i in range(self._healthy.get(token, 0))]


def test_one_failure_doesnt_cancel_siblings(db_session):
    db_session.add(Source(source_type="fake", token="good-a", enabled=True))
    db_session.add(Source(source_type="fake", token="bad", enabled=True))
    db_session.add(Source(source_type="fake", token="good-b", enabled=True))
    db_session.commit()

    factory = lambda: _MixedAsyncSource(  # noqa: E731
        healthy={"good-a": 2, "good-b": 3},
        unhealthy={"bad"},
    )
    stats = run_ingest(
        db_session,
        _settings(concurrency=5),
        source_factories={"fake": factory},
    )

    assert stats.boards_attempted == 3
    assert stats.boards_failed == 1
    assert stats.inserted == 5  # 2 + 3

    bad = db_session.query(Source).filter_by(token="bad").one()
    assert bad.last_status == "error"
    assert bad.last_error and "bad" in bad.last_error

    good_a = db_session.query(Source).filter_by(token="good-a").one()
    assert good_a.last_status == "success"
    assert good_a.jobs_found_last_run == 2


class _UnexpectedAsyncSource(JobSource):
    """One token raises a non-SourceUnavailable exception (programmer
    error class). Must be caught and reported as error on the row, not
    propagate out and abort the run."""

    name = "fake"

    def fetch(self, token):
        raise NotImplementedError

    async def fetch_async(self, token, async_client=None):
        if token == "boom":
            raise RuntimeError("simulated kaboom")
        return [_nj(token, 1)]


def test_unexpected_exception_caught_and_isolated(db_session):
    db_session.add(Source(source_type="fake", token="ok", enabled=True))
    db_session.add(Source(source_type="fake", token="boom", enabled=True))
    db_session.commit()

    stats = run_ingest(
        db_session,
        _settings(concurrency=5),
        source_factories={"fake": _UnexpectedAsyncSource},
    )

    assert stats.boards_attempted == 2
    assert stats.boards_failed == 1
    boom = db_session.query(Source).filter_by(token="boom").one()
    assert boom.last_status == "error"
    assert boom.last_error and "simulated kaboom" in boom.last_error
    # And the healthy board completed normally.
    assert db_session.query(Job).filter_by(external_id="ok-1").one_or_none() is not None


# ── native-async adapter wiring ──────────────────────────────────────────


def test_greenhouse_async_uses_supplied_async_client():
    """The GreenhouseSource's `fetch_async` must use the provided
    `httpx.AsyncClient` — proved by routing the client through a
    `MockTransport` that the test owns."""

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "Senior Engineer",
                        "absolute_url": "https://example.com/1",
                        "updated_at": "2026-05-25T10:00:00Z",
                        "location": {"name": "Remote, US"},
                        "content": "<p>Python</p>",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    source = GreenhouseSource()
    try:

        async def go():
            async with httpx.AsyncClient(transport=transport) as ac:
                return await source.fetch_async("stripe", async_client=ac)

        jobs = list(asyncio.run(go()))
    finally:
        source.close()

    assert len(jobs) == 1
    assert "boards-api.greenhouse.io" in calls[0]
    assert "/stripe/jobs" in calls[0]


def test_lever_async_uses_supplied_async_client():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                {
                    "id": "lev-1",
                    "text": "Backend Engineer",
                    "hostedUrl": "https://jobs.lever.co/acme/lev-1",
                    "applyUrl": "https://jobs.lever.co/acme/lev-1/apply",
                    "createdAt": 1716200000000,
                    "updatedAt": 1716300000000,
                    "categories": {"location": "Remote", "commitment": "Full-time"},
                    "descriptionPlain": "Build APIs in Python.",
                    "workplaceType": "remote",
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    source = LeverSource()
    try:

        async def go():
            async with httpx.AsyncClient(transport=transport) as ac:
                return await source.fetch_async("acme", async_client=ac)

        jobs = list(asyncio.run(go()))
    finally:
        source.close()

    assert len(jobs) == 1
    assert jobs[0].external_id == "lev-1"
    assert "api.lever.co" in calls[0]
    assert "/postings/acme" in calls[0]


def test_native_async_adapter_propagates_404_as_source_unavailable():
    """A 404 from the upstream ATS must surface as `SourceUnavailable`
    on the async path — same contract as the sync path so the
    orchestrator's per-source isolation works for both."""

    transport = httpx.MockTransport(lambda req: httpx.Response(404, text="Not Found"))
    source = GreenhouseSource()
    try:

        async def go():
            async with httpx.AsyncClient(transport=transport) as ac:
                await source.fetch_async("missing", async_client=ac)

        with pytest.raises(SourceUnavailable):
            asyncio.run(go())
    finally:
        source.close()


# ── default fetch_async fallback (sync adapter, no override) ─────────────


class _SyncOnlySource(JobSource):
    """Adapter that hasn't been ported to native-async. The base class'
    `fetch_async` default must still let it run under the orchestrator
    by offloading the sync `fetch` to a worker thread."""

    name = "fake"

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        return [_nj(token, 0)]


def test_default_fetch_async_falls_back_to_thread_for_sync_adapters(db_session):
    """Ensures the existing sync-only adapters (e.g. the test sources
    used elsewhere) still parallelise correctly via the base-class
    `to_thread` fallback."""
    _seed(db_session, 4)
    stats = run_ingest(
        db_session,
        _settings(concurrency=4),
        source_factories={"fake": _SyncOnlySource},
    )
    assert stats.inserted == 4
