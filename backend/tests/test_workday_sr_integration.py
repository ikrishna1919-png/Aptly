"""End-to-end check that the two new/refreshed source types
(`workday`, `smartrecruiters`) integrate cleanly with the existing
`run_ingest` orchestrator: seed a row, point it at a fake adapter, run
the ingest, and confirm the row lands at `last_status='success'` with
a real `jobs_found_last_run` count.

These tests intentionally don't probe the network paths (the per-adapter
test files do that with `httpx.MockTransport`). They guard the wiring —
the `SOURCES` registry, the `Source` row contract, and the per-source
telemetry / auto-prune machinery — for the two source types the latest
task added (Workday) and verified (SmartRecruiters).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models.source import Source
from app.services.ingest import run_ingest
from app.sources import SOURCES
from app.sources.base import JobSource, NormalizedJob


def _nj(token: str, idx: int) -> NormalizedJob:
    ts = datetime.now(UTC) - timedelta(hours=1)
    return NormalizedJob(
        source="placeholder",  # overwritten below to match the adapter
        external_id=f"{token}-{idx}",
        company=token,
        title=f"Engineer {idx}",
        url=f"https://example.com/{token}/{idx}",
        source_updated_at=ts,
        posted_at=ts,
        skills=["Python"],
    )


class _FakeFromRegistry(JobSource):
    """Reads the registered adapter's `name` so the same fake works
    for the workday + smartrecruiters cases. Returns 3 normalized
    postings per fetch."""

    def __init__(self, source_name: str) -> None:
        self.name = source_name

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        out = []
        for i in range(3):
            nj = _nj(token, i)
            nj.source = self.name
            out.append(nj)
        return out


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


# ── Registry wiring ────────────────────────────────────────────────────────


def test_workday_and_smartrecruiters_are_registered_in_sources():
    """Both source_type strings used in the seed migrations must map
    to a real adapter class — otherwise the ingest loop logs
    "unknown source" and silently skips every row of that type."""
    assert "workday" in SOURCES
    assert "smartrecruiters" in SOURCES


# ── Workday GM end-to-end ──────────────────────────────────────────────────


def test_workday_gm_reaches_success_via_run_ingest(db_session, settings):
    """A `workday:generalmotors:wd5:Careers_GM` row drives one
    successful pass through `run_ingest`. Telemetry on the row
    matches the postings the adapter returned."""
    db_session.add(
        Source(
            source_type="workday",
            token="generalmotors:wd5:Careers_GM",
            display_name="General Motors",
            enabled=True,
        )
    )
    db_session.commit()

    stats = run_ingest(
        db_session,
        settings,
        source_factories={"workday": lambda: _FakeFromRegistry("workday")},
    )

    row = db_session.query(Source).filter_by(source_type="workday").one()
    assert row.last_status == "success"
    assert row.last_error is None
    assert row.jobs_found_last_run == 3
    assert row.last_run_at is not None
    assert stats.inserted == 3


# ── SmartRecruiters Versant3 end-to-end ────────────────────────────────────


def test_smartrecruiters_versant3_reaches_success_via_run_ingest(db_session, settings):
    """A `smartrecruiters:Versant3` row drives one successful pass.
    Mirrors the Workday check so a future SR refactor can't silently
    break the existing seed."""
    db_session.add(
        Source(
            source_type="smartrecruiters",
            token="Versant3",
            display_name="Versant3",
            enabled=True,
        )
    )
    db_session.commit()

    stats = run_ingest(
        db_session,
        settings,
        source_factories={"smartrecruiters": lambda: _FakeFromRegistry("smartrecruiters")},
    )

    row = db_session.query(Source).filter_by(source_type="smartrecruiters").one()
    assert row.last_status == "success"
    assert row.jobs_found_last_run == 3
    assert stats.inserted == 3
