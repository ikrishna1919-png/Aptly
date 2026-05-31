"""Background-ingest tests — the kind that would've caught the 502 hang.

Covers:
  - POST /api/admin/ingest returns 202 + a run_id IMMEDIATELY, no matter
    how slow the underlying ingest is.
  - The background worker writes terminal status onto the IngestRun row.
  - A slow / failing board does NOT abort the run; the run still
    completes with `status=success` and the failure is reported in
    `stats.boards_failed`.
  - GET /api/admin/ingest and GET /api/admin/ingest/{run_id} return the
    run record.
  - Auth: every endpoint 403s without X-Admin-Token.

We monkey-patch the background runner to execute inline so we don't
need to wait on a real thread in tests — the same end state is reached
in either case (write IngestRun + run + finish_run).
"""

from __future__ import annotations

import time
from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config as config_module
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models.job import Job
from app.models.source import Source
from app.services import ingest as ingest_module
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable

AUTH = {"X-Admin-Token": "t"}


@pytest.fixture
def factories():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


@pytest.fixture
def settings():
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
        ANTHROPIC_API_KEY="",
    )


@pytest.fixture
def client(factories, settings, monkeypatch):
    # Point both the request-handling session and the background worker
    # at the same in-memory engine, so the IngestRun row the POST writes
    # is visible to the subsequent GETs.
    def override_db():
        with factories() as s:
            yield s

    monkeypatch.setattr(ingest_module, "SessionLocal", factories)
    # Run the worker INLINE so the test can assert the terminal state
    # without sleeping or joining a real thread.
    monkeypatch.setattr(
        ingest_module,
        "_launch_worker",
        lambda target, args: target(*args),
    )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


# ── Test doubles for the source layer ──────────────────────────────────────


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
        description="Build with Python.",
        skills=["Python"],
    )


class _MixedSource(JobSource):
    """Returns postings for healthy tokens; raises for unhealthy ones.

    Models the "one slow board fails its HTTP timeout while the rest
    succeed" path without an actual network call. The point of the test
    is that a 20-second-per-board timeout (or any SourceUnavailable)
    does NOT stop the run."""

    name = "fake"

    def __init__(
        self,
        *,
        healthy: dict[str, int] | None = None,
        unhealthy: dict[str, str] | None = None,
    ) -> None:
        self._healthy = healthy or {}
        self._unhealthy = unhealthy or {}
        self.fetched_tokens: list[str] = []

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        self.fetched_tokens.append(token)
        if token in self._unhealthy:
            raise SourceUnavailable(self._unhealthy[token])
        n = self._healthy.get(token, 0)
        return [_nj(token, i) for i in range(n)]


@pytest.fixture
def mixed_companies(monkeypatch, factories):
    """Seed the sources table + patch SOURCES so the background ingest
    runs against deterministic in-memory boards instead of the real
    internet."""
    with factories() as s:
        s.add(Source(source_type="fake", token="fastco", enabled=True))
        s.add(Source(source_type="fake", token="slowco", enabled=True))
        s.add(Source(source_type="fake", token="happyco", enabled=True))
        s.commit()

    mixed = _MixedSource(
        healthy={"fastco": 2, "happyco": 3},
        unhealthy={
            "slowco": "fake:slowco request failed: ConnectTimeout after 20s",
        },
    )
    monkeypatch.setattr(ingest_module, "SOURCES", {"fake": lambda: mixed})
    return mixed


# ── Auth ───────────────────────────────────────────────────────────────────


def test_endpoints_require_admin_token(client):
    test_client, _ = client
    assert test_client.post("/api/admin/ingest").status_code == 403
    assert test_client.get("/api/admin/ingest").status_code == 403
    assert test_client.get("/api/admin/ingest/anything").status_code == 403


# ── Core behavior ──────────────────────────────────────────────────────────


def test_post_returns_202_immediately_with_run_id(client, mixed_companies):
    test_client, _ = client

    started = time.monotonic()
    res = test_client.post("/api/admin/ingest", headers=AUTH)
    elapsed = time.monotonic() - started

    assert res.status_code == 202
    body = res.json()
    assert "run_id" in body
    assert body["status"] == "running"
    assert body["status_url"] == f"/api/admin/ingest/{body['run_id']}"
    # The response carries a Location header pointing at the same URL.
    assert res.headers.get("Location") == body["status_url"]

    # The whole round-trip must NOT have taken anywhere near the old
    # Render 100s budget — that was the bug. Even with the inline runner
    # finishing the full ingest first, this is fast for in-memory work.
    assert elapsed < 5.0, f"POST took {elapsed:.1f}s — should return in <1s"


def test_get_by_run_id_returns_terminal_status(client, mixed_companies):
    """After the inline worker runs, the IngestRun row is `success` with
    stats reflecting the fast/happy boards and one boards_failed for
    `slowco`. The slow board did NOT abort the run."""
    test_client, _ = client

    start = test_client.post("/api/admin/ingest", headers=AUTH).json()
    run_id = start["run_id"]

    res = test_client.get(f"/api/admin/ingest/{run_id}", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["run_id"] == run_id
    assert body["status"] == "success"
    assert body["finished_at"] is not None
    assert body["error"] is None

    stats = body["stats"]
    assert stats["boards_attempted"] == 3
    # The slow board failed but the run still finished.
    assert stats["boards_failed"] == 1
    failed_boards = [f["board"] for f in stats["boards_failures"]]
    assert failed_boards == ["fake:slowco"]
    # The other two boards ingested their postings.
    assert stats["inserted"] == 5  # 2 fastco + 3 happyco


def test_latest_endpoint_returns_most_recent(client, mixed_companies):
    test_client, _ = client

    first = test_client.post("/api/admin/ingest", headers=AUTH).json()
    second = test_client.post("/api/admin/ingest", headers=AUTH).json()

    res = test_client.get("/api/admin/ingest", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["run_id"] in {first["run_id"], second["run_id"]}
    # The second run must NOT be older than the first.
    if body["run_id"] == first["run_id"]:
        # That can only happen if the second somehow took a backwards
        # started_at, which would mean we have a bug.
        raise AssertionError("latest endpoint returned the older run — ordering is wrong")


def test_get_unknown_run_id_404(client, mixed_companies):
    test_client, _ = client
    res = test_client.get("/api/admin/ingest/nope-not-real", headers=AUTH)
    assert res.status_code == 404


def test_latest_404_when_no_runs(client):
    test_client, _ = client
    res = test_client.get("/api/admin/ingest", headers=AUTH)
    assert res.status_code == 404


def test_worker_failure_recorded_as_failed_status(client, monkeypatch):
    """If something inside the worker raises (DB unreachable, programmer
    error, etc.), the IngestRun row must record `status=failed` with the
    error message — never get stuck at `running` forever."""
    test_client, _ = client

    def boom(db, settings, **kwargs):
        raise RuntimeError("simulated worker explosion")

    monkeypatch.setattr(ingest_module, "run_ingest", boom)

    start = test_client.post("/api/admin/ingest", headers=AUTH).json()
    res = test_client.get(f"/api/admin/ingest/{start['run_id']}", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert body["finished_at"] is not None
    assert "simulated worker explosion" in body["error"]


def test_rolling_window_cleanup_still_runs_in_background(client, mixed_companies, factories):
    """Pre-seed a stale ATS row and verify the background ingest deletes
    it as part of the same run — the rolling-window guarantee holds for
    the new shape too."""
    from datetime import UTC, datetime, timedelta

    with factories() as s:
        s.add(
            Job(
                source="greenhouse",
                external_id="stale-x",
                company="oldco",
                title="Stale role",
                url="https://example.com/stale",
                source_updated_at=datetime.now(UTC) - timedelta(hours=100),
                skills=[],
            )
        )
        s.commit()

    test_client, _ = client
    start = test_client.post("/api/admin/ingest", headers=AUTH).json()
    res = test_client.get(f"/api/admin/ingest/{start['run_id']}", headers=AUTH)
    body = res.json()
    assert body["status"] == "success"
    assert body["stats"]["deleted_expired"] >= 1
    with factories() as s:
        assert s.query(Job).filter(Job.external_id == "stale-x").one_or_none() is None


def test_running_status_visible_before_worker_finishes(monkeypatch, factories, settings):
    """When the worker hasn't completed yet, the IngestRun row is still
    queryable and reports `status=running`. Use a worker that we don't
    actually launch — the POST writes the running row, we GET it before
    the worker runs, then run it manually and re-GET."""
    test_client = TestClient(app)
    pending: list[tuple] = []

    monkeypatch.setattr(ingest_module, "SessionLocal", factories)
    # Capture the worker invocation instead of running it.
    monkeypatch.setattr(
        ingest_module, "_launch_worker", lambda target, args: pending.append((target, args))
    )
    app.dependency_overrides[get_db] = lambda: iter([factories()])
    app.dependency_overrides[get_settings] = lambda: settings

    def db_dep():
        with factories() as s:
            yield s

    app.dependency_overrides[get_db] = db_dep
    config_module.get_settings.cache_clear()

    try:
        start = test_client.post("/api/admin/ingest", headers=AUTH).json()
        run_id = start["run_id"]

        # Before we run the worker, the row is `running`.
        mid = test_client.get(f"/api/admin/ingest/{run_id}", headers=AUTH).json()
        assert mid["status"] == "running"
        assert mid["finished_at"] is None

        # Now drive the worker to completion. We've seeded zero Source
        # rows on this DB so the worker is a no-op pass.
        target, args = pending.pop()
        target(*args)

        end = test_client.get(f"/api/admin/ingest/{run_id}", headers=AUTH).json()
        assert end["status"] == "success"
        assert end["finished_at"] is not None
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()
