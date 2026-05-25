"""Tests for GET /api/jobs/{id} (single-job detail endpoint)."""

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
from app.models.job import Job


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as s:
        s.add(
            Job(
                source="greenhouse",
                external_id="abc-1",
                company="acme",
                title="Senior Engineer",
                url="https://example.com/apply",
                description="Build things with Python.",
                source_updated_at=datetime.now().astimezone() - timedelta(hours=1),
                skills=["Python"],
            )
        )
        s.commit()

    def override_db():
        with Session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_get_job_returns_full_record(client):
    list_res = client.get("/api/jobs").json()
    job_id = list_res["jobs"][0]["id"]

    res = client.get(f"/api/jobs/{job_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Senior Engineer"
    assert body["description"] == "Build things with Python."
    assert body["skills"] == ["Python"]


def test_get_job_404(client):
    res = client.get("/api/jobs/99999")
    assert res.status_code == 404
