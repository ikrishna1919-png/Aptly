"""Tests for the admin manual-job endpoints and the cleanup-skip rule.

The manual-entry endpoints (`POST /api/admin/jobs`, `DELETE
/api/admin/jobs/{id}`) used to gate on `X-Admin-Token`. They now
gate on the SIGNED-IN USER'S EMAIL being in the `ADMIN_EMAILS`
allowlist — see `app.api.admin.require_admin_user`. This file
covers the new contract:

  * No signed-in user → 401 (handled by `get_current_user`).
  * Signed-in but email not in allowlist → 403.
  * Signed-in admin → 201 / 204 as before.

The cron token still protects `/admin/ingest` (separate test
surface); the two gates are intentionally distinct so a leak of
the cron token can't unlock manual-entry.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import get_current_user
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models.job import MANUAL_SOURCE, Job
from app.models.user import User
from app.services.ingest import _delete_expired

ADMIN_EMAIL = "admin@example.com"
NON_ADMIN_EMAIL = "other@example.com"


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


def _make_client(db_factory, *, user_email: str | None) -> TestClient:
    """Build a TestClient with `ADMIN_EMAILS=admin@example.com` and
    optionally a signed-in user injected via dependency override.

    `user_email=None` simulates an anonymous request; the admin
    endpoints should 401 (no `get_current_user`)."""
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="test-admin-token",
        ADMIN_EMAILS=ADMIN_EMAIL,
        HOURS_WINDOW=48,
    )

    def override_db():
        with db_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    if user_email is not None:
        # Stand-in for what `get_current_user` returns after the
        # session cookie has been resolved.
        fake_user = User(id=1, email=user_email, name="Test", google_subject_id="sub")
        app.dependency_overrides[get_current_user] = lambda: fake_user
    return TestClient(app)


@pytest.fixture
def admin_client(db_factory):
    """Signed-in user whose email IS in `ADMIN_EMAILS`. Manual-job
    endpoints should accept their calls."""
    client = _make_client(db_factory, user_email=ADMIN_EMAIL)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def non_admin_client(db_factory):
    """Signed-in user whose email is NOT in `ADMIN_EMAILS`. Manual-
    job endpoints should 403."""
    client = _make_client(db_factory, user_email=NON_ADMIN_EMAIL)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def anon_client(db_factory):
    """No signed-in user. Manual-job endpoints should 401 (the
    `get_current_user` dependency rejects with `session expired`)."""
    client = _make_client(db_factory, user_email=None)
    try:
        yield client
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


# ── New admin-gating semantics ─────────────────────────────────────────────


def test_create_manual_job_rejects_anonymous_with_401(anon_client):
    """No session = no user = 401. The `get_current_user` dependency
    fires before `require_admin_user` so this fails on the auth gate,
    not the admin gate."""
    res = anon_client.post("/api/admin/jobs", json=VALID_PAYLOAD)
    assert res.status_code == 401


def test_create_manual_job_rejects_non_admin_user_with_403(non_admin_client):
    """Signed-in but email not in `ADMIN_EMAILS` → 403. The endpoint
    MUST NOT distinguish whether the user is signed in but lacks
    privileges vs. signed in as admin — both return a generic
    'admins only' detail."""
    res = non_admin_client.post("/api/admin/jobs", json=VALID_PAYLOAD)
    assert res.status_code == 403


def test_create_manual_job_accepts_admin_user(admin_client):
    """Signed-in admin → 201 + the manual row is persisted with
    `source=manual` regardless of any client-supplied source field."""
    res = admin_client.post("/api/admin/jobs", json=VALID_PAYLOAD)
    assert res.status_code == 201
    body = res.json()
    assert body["source"] == MANUAL_SOURCE
    assert body["title"] == "Staff Engineer"
    assert body["company"] == "Aptly"
    assert body["salary"] == "$200k–$240k"
    assert body["skills"] == ["Python", "FastAPI"]
    assert body["url"] == "https://example.com/apply"
    assert body["external_id"].startswith("manual-")


def test_create_manual_job_ignores_client_supplied_source(admin_client):
    payload = {**VALID_PAYLOAD, "source": "lever"}
    res = admin_client.post("/api/admin/jobs", json=payload)
    assert res.status_code == 201
    assert res.json()["source"] == MANUAL_SOURCE


def test_create_manual_job_validation(admin_client):
    res = admin_client.post(
        "/api/admin/jobs",
        json={"title": "", "company": "x", "apply_url": "x"},
    )
    assert res.status_code == 422


def test_manual_job_shows_in_public_feed(admin_client):
    create = admin_client.post("/api/admin/jobs", json=VALID_PAYLOAD)
    assert create.status_code == 201

    feed = admin_client.get("/api/jobs")
    assert feed.status_code == 200
    titles = [j["title"] for j in feed.json()["jobs"]]
    assert "Staff Engineer" in titles


def test_delete_manual_job(admin_client):
    create = admin_client.post("/api/admin/jobs", json=VALID_PAYLOAD)
    job_id = create.json()["id"]

    res = admin_client.delete(f"/api/admin/jobs/{job_id}")
    assert res.status_code == 204
    assert admin_client.get("/api/jobs").json()["total"] == 0


def test_delete_rejects_non_admin_user_with_403(db_factory, admin_client):
    """Two-step: admin creates a job; a non-admin tries to delete it.
    The non-admin 403s — no peeking, no manipulation. Tear down +
    rebuild the dependency override so the same fake DB sees both
    sides of the test."""
    create = admin_client.post("/api/admin/jobs", json=VALID_PAYLOAD)
    job_id = create.json()["id"]
    # Swap the signed-in user to a non-admin and retry the delete.
    fake_other = User(id=2, email=NON_ADMIN_EMAIL, name="Other", google_subject_id="sub-2")
    app.dependency_overrides[get_current_user] = lambda: fake_other
    res = admin_client.delete(f"/api/admin/jobs/{job_id}")
    assert res.status_code == 403


def test_delete_returns_404_for_missing(admin_client):
    res = admin_client.delete("/api/admin/jobs/9999")
    assert res.status_code == 404


def test_delete_refuses_non_manual_rows(admin_client, db_factory):
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

    res = admin_client.delete(f"/api/admin/jobs/{ats_id}")
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
