"""Tests for the admin manual-job endpoints and the cleanup-skip rule."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models.job import MANUAL_SOURCE, Job
from app.services.ingest import _delete_expired


@pytest.fixture
def db_factory():
    # StaticPool + check_same_thread=False makes the in-memory DB shared
    # across every Session opened against this engine, so the FastAPI
    # request handler sees the same schema/data as the test setup.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


@pytest.fixture
def client(db_factory):
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="test-admin-token",
        HOURS_WINDOW=48,
    )

    def override_db():
        with db_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


VALID_PAYLOAD = {
    "title": "Staff Engineer",
    "company": "Aptly",
    "apply_url": "https://example.com/apply",
    "location": "Remote",
    "remote": True,
    "employment_type": "Full-time",
    "salary": "$200k–$240k",
    "skills": ["Python", "FastAPI"],
    "description": "Build the wedge.",
}


def test_create_manual_job_requires_token(client):
    res = client.post("/api/admin/jobs", json=VALID_PAYLOAD)
    assert res.status_code == 403


def test_create_manual_job_rejects_wrong_token(client):
    res = client.post("/api/admin/jobs", json=VALID_PAYLOAD, headers={"X-Admin-Token": "nope"})
    assert res.status_code == 403


def test_create_manual_job_persists_with_manual_source(client):
    res = client.post(
        "/api/admin/jobs", json=VALID_PAYLOAD, headers={"X-Admin-Token": "test-admin-token"}
    )
    assert res.status_code == 201
    body = res.json()
    assert body["source"] == MANUAL_SOURCE
    assert body["title"] == "Staff Engineer"
    assert body["company"] == "Aptly"
    assert body["salary"] == "$200k–$240k"
    assert body["skills"] == ["Python", "FastAPI"]
    assert body["url"] == "https://example.com/apply"
    assert body["external_id"].startswith("manual-")


def test_create_manual_job_ignores_client_supplied_source(client):
    payload = {**VALID_PAYLOAD, "source": "lever"}
    res = client.post(
        "/api/admin/jobs", json=payload, headers={"X-Admin-Token": "test-admin-token"}
    )
    assert res.status_code == 201
    assert res.json()["source"] == MANUAL_SOURCE


def test_create_manual_job_validation(client):
    res = client.post(
        "/api/admin/jobs",
        json={"title": "", "company": "x", "apply_url": "x"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert res.status_code == 422


def test_manual_job_shows_in_public_feed(client):
    create = client.post(
        "/api/admin/jobs", json=VALID_PAYLOAD, headers={"X-Admin-Token": "test-admin-token"}
    )
    assert create.status_code == 201

    feed = client.get("/api/jobs")
    assert feed.status_code == 200
    titles = [j["title"] for j in feed.json()["jobs"]]
    assert "Staff Engineer" in titles


def test_delete_manual_job(client):
    create = client.post(
        "/api/admin/jobs", json=VALID_PAYLOAD, headers={"X-Admin-Token": "test-admin-token"}
    )
    job_id = create.json()["id"]

    res = client.delete(f"/api/admin/jobs/{job_id}", headers={"X-Admin-Token": "test-admin-token"})
    assert res.status_code == 204
    assert client.get("/api/jobs").json()["total"] == 0


def test_delete_requires_token(client):
    create = client.post(
        "/api/admin/jobs", json=VALID_PAYLOAD, headers={"X-Admin-Token": "test-admin-token"}
    )
    job_id = create.json()["id"]
    res = client.delete(f"/api/admin/jobs/{job_id}")
    assert res.status_code == 403


def test_delete_returns_404_for_missing(client):
    res = client.delete("/api/admin/jobs/9999", headers={"X-Admin-Token": "test-admin-token"})
    assert res.status_code == 404


def test_delete_refuses_non_manual_rows(client, db_factory):
    # Insert a non-manual row directly.
    with db_factory() as s:
        ats_job = Job(
            source="greenhouse",
            external_id="x-1",
            company="acme",
            title="ATS role",
            url="https://example.com/ats",
            source_updated_at=datetime.now().astimezone(),
            skills=[],
        )
        s.add(ats_job)
        s.commit()
        ats_id = ats_job.id

    res = client.delete(f"/api/admin/jobs/{ats_id}", headers={"X-Admin-Token": "test-admin-token"})
    assert res.status_code == 400


def test_cleanup_skips_manual_jobs(db_factory):
    """The ingest's rolling-window cleanup must never touch manual rows."""
    with db_factory() as s:
        # An ATS row well outside the window — should be deleted.
        stale_ats = Job(
            source="greenhouse",
            external_id="stale-1",
            company="acme",
            title="Stale ATS role",
            url="https://example.com/stale",
            source_updated_at=datetime.now().astimezone() - timedelta(hours=100),
            skills=[],
        )
        # A manual row with an equally-old timestamp — must be preserved.
        stale_manual = Job(
            source=MANUAL_SOURCE,
            external_id="manual-abc",
            company="aptly",
            title="Manual role",
            url="https://example.com/manual",
            source_updated_at=datetime.now().astimezone() - timedelta(hours=100),
            skills=[],
        )
        s.add_all([stale_ats, stale_manual])
        s.commit()

        window_start = datetime.now().astimezone() - timedelta(hours=48)
        deleted = _delete_expired(s, window_start)
        s.commit()

        assert deleted == 1
        remaining = [j.source for j in s.query(Job).all()]
        assert remaining == [MANUAL_SOURCE]
