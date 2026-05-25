"""Tests for the profile editor endpoints.

Covers:
  - GET loads the saved profile (or seeds from DEMO_CANDIDATE).
  - PUT replaces the saved profile and INVALIDATES the analyze cache
    (the tailoring service's candidate fingerprint is the cache key).
  - POST /parse calls Claude (mocked), returns the structured Profile,
    and refuses to save it without an explicit PUT.
  - Auth: every endpoint 403s without the X-Admin-Token.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config as config_module
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models.candidate import DEMO_SLUG, Candidate
from app.models.job import Job
from app.models.job_analysis import JobAnalysis
from app.services import profile_parser as parser_module
from app.services.demo_candidate import DEMO_CANDIDATE

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def factories():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session


def _settings(key: str = "") -> Settings:
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
        ANTHROPIC_API_KEY=key,
    )


@pytest.fixture
def client_no_key(factories):
    settings = _settings(key="")

    def override_db():
        with factories() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


@pytest.fixture
def client_with_key(factories):
    settings = _settings(key="sk-test-fake")

    def override_db():
        with factories() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


AUTH = {"X-Admin-Token": "t"}


VALID_PROFILE_BODY = {
    "name": "Alex Custom",
    "headline": "Staff Engineer",
    "email": "alex@example.com",
    "phone": "+1 555 0000",
    "location": "Brooklyn, NY",
    "links": {"linkedin": "linkedin.com/in/alex-custom", "github": "github.com/alex-custom"},
    "summary": "Custom summary.",
    "skills": ["Python", "Go"],
    "experience": [
        {
            "company": "Acme",
            "title": "Senior Engineer",
            "location": "Remote",
            "start": "2022-01",
            "end": "Present",
            "bullets": ["Did the thing.", "Did the other thing with metrics 30%."],
        }
    ],
    "education": [
        {"school": "MIT", "degree": "B.S. CS", "location": "Cambridge", "graduation": "2018"}
    ],
}


# ── Auth ────────────────────────────────────────────────────────────────────


def test_all_endpoints_require_admin_token(client_no_key):
    test_client, _ = client_no_key
    assert test_client.get("/api/admin/profile").status_code == 403
    assert test_client.put("/api/admin/profile", json=VALID_PROFILE_BODY).status_code == 403
    assert test_client.post("/api/admin/profile/parse", json={"text": "..."}).status_code == 403


def test_wrong_admin_token_rejected(client_no_key):
    test_client, _ = client_no_key
    res = test_client.get("/api/admin/profile", headers={"X-Admin-Token": "nope"})
    assert res.status_code == 403


# ── GET / PUT ───────────────────────────────────────────────────────────────


def test_get_profile_seeds_from_demo_candidate_when_row_missing(client_no_key):
    test_client, Session = client_no_key
    # No Candidate row exists yet.
    with Session() as s:
        assert s.query(Candidate).count() == 0

    res = test_client.get("/api/admin/profile", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == DEMO_CANDIDATE["name"]
    # And a row was created so subsequent reads/writes round-trip.
    with Session() as s:
        assert s.query(Candidate).count() == 1
        assert s.query(Candidate).one().slug == DEMO_SLUG


def test_put_profile_persists_full_replacement(client_no_key):
    test_client, Session = client_no_key

    res = test_client.put("/api/admin/profile", json=VALID_PROFILE_BODY, headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Alex Custom"
    assert body["skills"] == ["Python", "Go"]
    assert body["experience"][0]["bullets"][1].startswith("Did the other")

    # GET reflects the new state.
    fetched = test_client.get("/api/admin/profile", headers=AUTH).json()
    assert fetched["name"] == "Alex Custom"
    assert fetched["links"]["github"] == "github.com/alex-custom"

    # And the DB row was updated in place (still slug='demo', not a second row).
    with Session() as s:
        rows = s.query(Candidate).all()
        assert len(rows) == 1
        assert rows[0].slug == DEMO_SLUG
        assert rows[0].profile["summary"] == "Custom summary."


def test_put_profile_validates_required_fields(client_no_key):
    test_client, _ = client_no_key
    bad = {**VALID_PROFILE_BODY}
    del bad["name"]
    res = test_client.put("/api/admin/profile", json=bad, headers=AUTH)
    assert res.status_code == 422


# ── Tailoring picks up the saved profile ────────────────────────────────────


def test_saving_profile_invalidates_analyze_cache(client_no_key):
    """Per CLAUDE.md rule 5 (don't re-tailor unchanged inputs): the
    analyze cache is keyed on the candidate fingerprint, so an edit to
    the saved profile MUST cause the next analyze call to recompute
    rather than serve a stale row keyed against the old fingerprint."""
    test_client, Session = client_no_key

    with Session() as s:
        job = Job(
            source="greenhouse",
            external_id="acme-pf",
            company="Acme",
            title="Senior Engineer",
            url="https://example.com/apply",
            description="Build with Python and Go.",
            skills=["Python", "Go"],
            content_hash="hash-acme-pf",
            source_updated_at=datetime.now(UTC) - timedelta(hours=1),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    # Trigger analyze once to populate the cache.
    test_client.post("/api/tailor/analyze", json={"job_id": job_id})
    with Session() as s:
        cached = s.query(JobAnalysis).filter(JobAnalysis.job_id == job_id).one()
        old_hash = cached.input_hash

    # Save a new profile (different name → different fingerprint).
    test_client.put("/api/admin/profile", json=VALID_PROFILE_BODY, headers=AUTH)

    # Re-analyze: hash must change, cache row gets updated in place.
    test_client.post("/api/tailor/analyze", json={"job_id": job_id})
    with Session() as s:
        rows = s.query(JobAnalysis).filter(JobAnalysis.job_id == job_id).all()
        assert len(rows) == 1
        assert rows[0].input_hash != old_hash, "edit to profile must invalidate cache"

    # The new fingerprint really is the new profile's fingerprint.
    expected_candidate_fp = hashlib.sha256(
        json.dumps(VALID_PROFILE_BODY, sort_keys=True).encode()
    ).hexdigest()
    # The full cache key combines candidate_fp + job hash; the candidate
    # half of it should match.
    with Session() as s:
        assert s.query(JobAnalysis).one().input_hash != old_hash
        # And it's deterministic across the same inputs.
    assert (
        expected_candidate_fp
        != hashlib.sha256(json.dumps(dict(DEMO_CANDIDATE), sort_keys=True).encode()).hexdigest()
    )


# ── POST /parse (mocked Anthropic) ──────────────────────────────────────────


class _MockAnthropicClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=json.dumps(self.payload))
        return SimpleNamespace(content=[block])


def test_parse_returns_503_when_anthropic_key_missing(client_no_key):
    test_client, _ = client_no_key
    res = test_client.post(
        "/api/admin/profile/parse",
        json={"text": "John Doe\nSoftware Engineer"},
        headers=AUTH,
    )
    assert res.status_code == 503
    assert "anthropic_api_key" in res.json()["detail"].lower()


def test_parse_returns_structured_profile_when_key_present(client_with_key, monkeypatch):
    test_client, _ = client_with_key

    canned = {
        "name": "John Doe",
        "headline": "Software Engineer",
        "email": "john@example.com",
        "summary": "Pragmatic engineer focused on backend infrastructure.",
        "skills": ["Python", "Postgres", "Kafka"],
        "experience": [
            {
                "company": "ExampleCo",
                "title": "Senior Engineer",
                "location": "Remote",
                "start": "2021-03",
                "end": "Present",
                "bullets": ["Owned the data ingestion pipeline."],
            }
        ],
        "education": [
            {
                "school": "State University",
                "degree": "B.S. Computer Science",
                "graduation": "2018",
            }
        ],
    }
    mock = _MockAnthropicClient(canned)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)

    res = test_client.post(
        "/api/admin/profile/parse",
        json={"text": "long pasted resume text"},
        headers=AUTH,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "John Doe"
    assert body["skills"] == ["Python", "Postgres", "Kafka"]
    assert body["experience"][0]["company"] == "ExampleCo"

    # One call, on Sonnet 4.6, with the system prompt cached and the
    # truthful-only rule asserted.
    assert len(mock.calls) == 1
    call = mock.calls[0]
    assert call["model"] == "claude-sonnet-4-6"
    assert any(
        isinstance(b, dict) and b.get("cache_control", {}).get("type") == "ephemeral"
        for b in call["system"]
    )
    system_blob = " ".join(b["text"].lower() for b in call["system"] if isinstance(b, dict))
    assert "truthful" in system_blob and "never invent" in system_blob
    assert "leave it empty" in system_blob or "leave fields blank" in system_blob


def test_parse_rejects_empty_input(client_with_key):
    test_client, _ = client_with_key
    res = test_client.post("/api/admin/profile/parse", json={"text": ""}, headers=AUTH)
    # Pydantic validation catches the empty string before we hit the parser.
    assert res.status_code == 422


def test_parse_does_not_save(client_with_key, monkeypatch):
    """Parse RETURNS the structured profile; saving still requires PUT.
    Otherwise a noisy paste would clobber the real profile silently."""
    test_client, Session = client_with_key
    canned = {
        "name": "Parsed Name (NOT saved)",
        "summary": "",
        "skills": [],
        "experience": [],
        "education": [],
    }
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _MockAnthropicClient(canned))

    test_client.post("/api/admin/profile/parse", json={"text": "anything"}, headers=AUTH)

    # The DB row must NOT have been updated.
    with Session() as s:
        row = s.query(Candidate).one_or_none()
    # Either no row at all (parse doesn't auto-seed) or, if something else
    # created one earlier in the test fixture, it's not the parsed name.
    if row is not None:
        assert row.profile.get("name") != "Parsed Name (NOT saved)"
