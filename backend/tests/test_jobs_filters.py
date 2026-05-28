"""Query-time filters for the Jobs page: `work_model` (derived + filter),
`posted_within`, and `job_type` (employment_type + JD-regex fallback). All
are computed at serve time — no stored columns / migrations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models.job import Job


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
def client(factories):
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:", ADMIN_TOKEN="t", HOURS_WINDOW=720
    )

    def override_db():
        with factories() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()


def _job(session, **kw):
    now = datetime.now(UTC)
    defaults = dict(
        source="greenhouse",
        external_id=f"ext-{kw.get('title', 'x')}",
        company="Acme",
        title="Engineer",
        url="https://example.com/apply",
        skills=[],
        source_updated_at=now - timedelta(hours=1),
        posted_at=now - timedelta(hours=1),
    )
    defaults.update(kw)
    job = Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _titles(body):
    return {j["title"] for j in body["jobs"]}


def test_work_model_derived_field(client):
    test_client, Session = client
    with Session() as s:
        _job(s, title="RemoteRole", remote=True)
        _job(s, title="OnsiteRole", remote=False)
        _job(s, title="HybridRole", remote=False, description="This role is hybrid: 3 days onsite.")
    body = test_client.get("/api/jobs").json()
    wm = {j["title"]: j["work_model"] for j in body["jobs"]}
    assert wm["RemoteRole"] == "remote"
    assert wm["OnsiteRole"] == "onsite"
    assert wm["HybridRole"] == "hybrid"  # JD "hybrid" wins over the remote flag


def test_work_model_filter(client):
    test_client, Session = client
    with Session() as s:
        _job(s, title="RemoteRole", remote=True)
        _job(s, title="OnsiteRole", remote=False)
        _job(s, title="HybridRole", remote=False, description="Hybrid schedule, 2 days in office.")

    assert _titles(test_client.get("/api/jobs?work_model=remote").json()) == {"RemoteRole"}
    assert _titles(test_client.get("/api/jobs?work_model=onsite").json()) == {"OnsiteRole"}
    hybrid = test_client.get("/api/jobs?work_model=hybrid").json()
    assert _titles(hybrid) == {"HybridRole"}
    assert hybrid["total"] == 1  # total reflects the python-filtered count


def test_job_type_filter_matches_employment_type_and_jd(client):
    test_client, Session = client
    with Session() as s:
        _job(s, title="FT", employment_type="Full-time")
        _job(s, title="Contract", employment_type="Contract")
        _job(s, title="InternJD", description="This is a summer internship for students.")
        # No employment_type, no JD signal → indeterminate ("any").
        _job(s, title="Unknown", description="Build great software with the team.")

    ft = test_client.get("/api/jobs?job_type=full-time").json()
    # Full-time match PLUS the indeterminate job (kept as "any").
    assert "FT" in _titles(ft)
    assert "Unknown" in _titles(ft)
    assert "Contract" not in _titles(ft)
    assert "InternJD" not in _titles(ft)

    intern = test_client.get("/api/jobs?job_type=internship").json()
    assert "InternJD" in _titles(intern)  # classified from the JD text
    assert "Unknown" in _titles(intern)  # indeterminate kept
    assert "FT" not in _titles(intern)

    # Unknown job type value → treated as no filter (all jobs returned).
    allj = test_client.get("/api/jobs?job_type=bogus").json()
    assert allj["total"] == 4


def test_posted_within_filters_by_recency(client):
    test_client, Session = client
    now = datetime.now(UTC)
    with Session() as s:
        _job(
            s,
            title="Fresh",
            posted_at=now - timedelta(hours=5),
            source_updated_at=now - timedelta(hours=5),
        )
        _job(
            s,
            title="Old",
            posted_at=now - timedelta(days=20),
            source_updated_at=now - timedelta(days=20),
        )
    within24 = test_client.get("/api/jobs?posted_within=24h").json()
    assert _titles(within24) == {"Fresh"}
    within30 = test_client.get("/api/jobs?posted_within=30d").json()
    assert _titles(within30) == {"Fresh", "Old"}
