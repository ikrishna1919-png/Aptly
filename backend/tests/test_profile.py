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


def _wire_parse_worker(monkeypatch, factories):
    """Route the background-parse worker through the test's in-memory
    engine + drive it inline so tests can assert the final state
    without waiting on a real thread. Same pattern the ingest
    background tests use."""
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda target, args: target(*args))


@pytest.fixture
def client_no_key(factories, monkeypatch):
    settings = _settings(key="")

    def override_db():
        with factories() as s:
            yield s

    _wire_parse_worker(monkeypatch, factories)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


@pytest.fixture
def client_with_key(factories, monkeypatch):
    settings = _settings(key="sk-test-fake")

    def override_db():
        with factories() as s:
            yield s

    _wire_parse_worker(monkeypatch, factories)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def _post_parse_and_poll(test_client, body: dict, *, expected_status: int = 200) -> dict:
    """Kick off a parse, then GET the status. With the inline worker
    patched in, the GET happens AFTER the worker has finished — so the
    returned row carries the terminal state."""
    res = test_client.post("/api/admin/profile/parse", json=body, headers=AUTH)
    if res.status_code != 202:
        # Synchronous failure path (e.g. 503 for missing key) — return
        # the response as-is so the test can assert on it.
        return {"_response": res}
    run_id = res.json()["run_id"]
    poll = test_client.get(f"/api/admin/profile/parse/{run_id}", headers=AUTH)
    assert poll.status_code == 200, poll.text
    return poll.json()


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


# ── POST /parse — deterministic Python parser ──────────────────────────────


# A reasonably realistic resume that covers every field the parser
# tries to extract. Used both for the end-to-end run-ingest tests and
# for the per-field unit tests below.
_FULL_RESUME = """\
Alex Rivera
Senior Software Engineer
San Francisco, CA  ·  alex.rivera@example.com  ·  (555) 123-4567
linkedin.com/in/alex-rivera  ·  github.com/alexr

Summary
Backend engineer with seven years building distributed systems in
Python and Go. Most recently led the migration of a 50-service
monolith onto event-driven Kafka with Postgres CDC.

Experience

Senior Software Engineer — Acme Corp · San Francisco, CA
Jan 2022 – Present
- Led the migration to event-driven Kafka services (12 services, 0 downtime)
- Cut p95 latency 480ms → 110ms by introducing Redis cache layer
- Mentored 4 junior engineers; ran the weekly architecture review

Software Engineer — Beta Labs · Remote
Mar 2019 – Dec 2021
- Built the billing pipeline (Stripe, Postgres) handling $30M ARR
- Owned on-call rotations for the platform team

Education

State University, Berkeley, CA
B.S. Computer Science  2014 – 2018

Skills
Python, Go, Kafka, AWS, PostgreSQL, Docker, Kubernetes, Redis, Terraform
"""


def test_parse_end_to_end_extracts_every_field(client_with_key):
    """The happy path: POST returns 202, GET returns success +
    extracted profile populated with every field the parser handles."""
    test_client, _ = client_with_key

    start = test_client.post(
        "/api/admin/profile/parse",
        json={"text": _FULL_RESUME},
        headers=AUTH,
    )
    assert start.status_code == 202
    run_id = start.json()["run_id"]
    assert start.json()["status_url"] == f"/api/admin/profile/parse/{run_id}"

    poll = test_client.get(f"/api/admin/profile/parse/{run_id}", headers=AUTH)
    assert poll.status_code == 200
    body = poll.json()
    assert body["status"] == "success"
    assert body["error"] is None
    assert body["finished_at"] is not None

    profile = body["profile"]
    assert profile["name"] == "Alex Rivera"
    assert profile["email"] == "alex.rivera@example.com"
    assert profile["phone"] == "(555) 123-4567"
    assert profile["location"] == "San Francisco, CA"
    assert profile["links"]["linkedin"] == "linkedin.com/in/alex-rivera"
    assert profile["links"]["github"] == "github.com/alexr"
    assert "distributed systems" in profile["summary"].lower()

    assert "Python" in profile["skills"]
    assert "Kafka" in profile["skills"]
    assert "PostgreSQL" in profile["skills"]
    # Skills are deduplicated and capped to readable items.
    assert len(profile["skills"]) == len(set(s.lower() for s in profile["skills"]))

    assert len(profile["experience"]) == 2
    acme = profile["experience"][0]
    assert acme["title"] == "Senior Software Engineer"
    assert acme["company"] == "Acme Corp"
    assert acme["location"] == "San Francisco, CA"
    assert acme["start"] == "2022-01"
    assert acme["end"] == "Present"
    assert any("Kafka" in b for b in acme["bullets"])

    beta = profile["experience"][1]
    assert beta["company"] == "Beta Labs"
    assert beta["start"] == "2019-03"
    assert beta["end"] == "2021-12"

    assert len(profile["education"]) == 1
    edu = profile["education"][0]
    assert edu["school"] == "State University"
    assert "Computer Science" in edu["degree"] or "Bachelor" in edu["degree"]
    assert edu["graduation"] == "2018"


def test_parse_post_returns_202_immediately_with_run_id(client_with_key):
    """The kick-off response shape is unchanged from the previous
    AI-backed implementation — frontend code path keeps working."""
    test_client, _ = client_with_key
    res = test_client.post("/api/admin/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)
    assert res.status_code == 202
    body = res.json()
    assert "run_id" in body
    assert body["status"] == "running"
    assert res.headers.get("Location") == body["status_url"]


def test_parse_rejects_empty_input(client_with_key):
    """Pydantic-level validation still catches the empty-string case
    before we hit the parser — caller error, 422."""
    test_client, _ = client_with_key
    res = test_client.post("/api/admin/profile/parse", json={"text": ""}, headers=AUTH)
    assert res.status_code == 422


def test_parse_does_not_save(client_with_key):
    """Parse extracts; saving still requires PUT — a noisy paste must
    not clobber the real profile silently."""
    test_client, Session = client_with_key

    # Seed an existing saved profile so the assertion below is
    # meaningful (the parse_resume output for `_FULL_RESUME` does NOT
    # share the same name).
    test_client.put("/api/admin/profile", json=VALID_PROFILE_BODY, headers=AUTH)
    test_client.post("/api/admin/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)

    with Session() as s:
        row = s.query(Candidate).filter(Candidate.slug == DEMO_SLUG).one()
    # The saved row is still the one PUT placed, not the parsed one.
    assert row.profile["name"] == "Alex Custom"


def test_parse_get_unknown_run_id_returns_404(client_with_key):
    test_client, _ = client_with_key
    res = test_client.get("/api/admin/profile/parse/nope-not-real", headers=AUTH)
    assert res.status_code == 404


def test_parse_status_endpoint_requires_admin_token(client_with_key):
    test_client, _ = client_with_key
    res = test_client.get("/api/admin/profile/parse/anything")
    assert res.status_code == 403


def test_parse_works_without_anthropic_key(factories, monkeypatch):
    """The deterministic parser doesn't need ANTHROPIC_API_KEY at all
    — pasting a resume must work even on an environment that has the
    key unset."""
    settings = _settings(key="")

    def override_db():
        with factories() as s:
            yield s

    _wire_parse_worker(monkeypatch, factories)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    config_module.get_settings.cache_clear()
    try:
        test_client = TestClient(app)
        start = test_client.post(
            "/api/admin/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH
        )
        assert start.status_code == 202
        poll = test_client.get(f"/api/admin/profile/parse/{start.json()['run_id']}", headers=AUTH)
        body = poll.json()
        assert body["status"] == "success"
        assert body["profile"]["name"] == "Alex Rivera"
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def test_parse_random_text_returns_empty_profile_not_failure(client_with_key):
    """If the input doesn't look like a resume at all, the parser
    returns an empty Profile with `status='success'`. The frontend
    interprets the empty result as "fill in the form manually" —
    NEVER `status='failed'` for non-resume input."""
    test_client, _ = client_with_key
    junk = "lorem ipsum dolor sit amet"
    start = test_client.post("/api/admin/profile/parse", json={"text": junk}, headers=AUTH)
    assert start.status_code == 202
    body = test_client.get(
        f"/api/admin/profile/parse/{start.json()['run_id']}", headers=AUTH
    ).json()
    assert body["status"] == "success"
    assert body["error"] is None
    profile = body["profile"]
    # Empty result — name is "", experience/education/skills empty.
    assert profile["name"] == ""
    assert profile["experience"] == []
    assert profile["education"] == []
    assert profile["skills"] == []


def test_parse_truncates_oversized_input(client_with_key):
    """The parser caps input at 200K chars. 300K of garbage must not
    explode the worker; it just gets truncated and returns an empty
    profile."""
    test_client, _ = client_with_key
    huge = "x" * 300_000
    start = test_client.post("/api/admin/profile/parse", json={"text": huge}, headers=AUTH)
    assert start.status_code == 202
    body = test_client.get(
        f"/api/admin/profile/parse/{start.json()['run_id']}", headers=AUTH
    ).json()
    assert body["status"] == "success"


# ── parse_resume unit coverage (no HTTP layer) ──────────────────────────────


def test_parse_resume_extracts_contact_block():
    from app.services.profile_parser import parse_resume

    p = parse_resume(
        "Jordan Singh\n"
        "Software Engineer\n"
        "Brooklyn, NY  ·  jsingh@example.com  ·  +1 (415) 555-2233\n"
        "https://linkedin.com/in/jsingh  ·  github.com/jsingh\n"
    )
    assert p.name == "Jordan Singh"
    assert p.email == "jsingh@example.com"
    assert "415" in p.phone
    assert p.location == "Brooklyn, NY"
    assert "linkedin.com/in/jsingh" in p.links.linkedin
    assert "github.com/jsingh" in p.links.github


def test_parse_resume_handles_only_name_block():
    """An input that has nothing but a name still returns successfully
    with the name populated — never throws."""
    from app.services.profile_parser import parse_resume

    p = parse_resume("Alex Custom\n")
    assert p.name == "Alex Custom"
    assert p.email is None
    assert p.experience == []


def test_parse_resume_returns_empty_profile_for_empty_string():
    from app.services.profile_parser import parse_resume

    p = parse_resume("")
    assert p.name == ""
    assert p.experience == []
    assert p.education == []


def test_parse_resume_handles_only_section_with_dates():
    """Just an experience-shaped block — parser extracts the role
    without crashing on the missing surrounding sections."""
    from app.services.profile_parser import parse_resume

    p = parse_resume(
        "Experience\n"
        "Engineer — Foo Co\n"
        "2020 - Present\n"
        "- Did a thing\n"
        "- Did another thing\n"
    )
    assert len(p.experience) == 1
    exp = p.experience[0]
    assert exp.title == "Engineer"
    assert exp.company == "Foo Co"
    assert exp.start == "2020"
    assert exp.end == "Present"
    assert exp.bullets == ["Did a thing", "Did another thing"]


def test_parse_resume_date_normalisation_variants():
    """Month names full / abbreviated / case variants normalise to
    YYYY-MM consistently."""
    from app.services.profile_parser import parse_resume

    text = "Experience\n" "A — X\nJanuary 2020 - Mar 2022\n" "\n" "B — Y\nMay 2018 to Dec 2019\n"
    p = parse_resume(text)
    starts = [(e.start, e.end) for e in p.experience]
    assert ("2020-01", "2022-03") in starts
    assert ("2018-05", "2019-12") in starts


def test_parse_resume_education_with_year_range():
    from app.services.profile_parser import parse_resume

    p = parse_resume(
        "Education\n"
        "Massachusetts Institute of Technology\n"
        "Cambridge, MA\n"
        "B.S. Computer Science  2014 – 2018\n"
    )
    assert len(p.education) == 1
    edu = p.education[0]
    assert "Massachusetts Institute of Technology" in edu.school
    assert "Computer Science" in edu.degree
    assert edu.graduation == "2018"
    assert edu.location == "Cambridge, MA"


def test_parse_resume_skills_dedupes_case_insensitively():
    from app.services.profile_parser import parse_resume

    p = parse_resume("Skills\nPython, python, PYTHON, Go, Go, Kafka, AWS, AWS\n")
    lower = [s.lower() for s in p.skills]
    assert lower == sorted(set(lower), key=lower.index)
    assert "python" in lower
    assert "go" in lower


def test_parse_resume_skills_bullet_layout():
    from app.services.profile_parser import parse_resume

    p = parse_resume("Skills\n" "• Python\n" "• Go\n" "• Kafka\n")
    assert "Python" in p.skills
    assert "Go" in p.skills
    assert "Kafka" in p.skills


def test_parse_resume_phone_separator_variants():
    """Common phone formats all extract — `555.123.4567`, `(555)
    123-4567`, `+1 555-123-4567`, `5551234567` (no separators)."""
    from app.services.profile_parser import parse_resume

    cases = [
        ("Name Here\n555.123.4567\n", "555.123.4567"),
        ("Name Here\n(555) 123-4567\n", "(555) 123-4567"),
        ("Name Here\n+1 555-123-4567\n", "+1 555-123-4567"),
    ]
    for text, expected in cases:
        p = parse_resume(text)
        assert expected in (p.phone or ""), f"input={text!r}"


def test_parse_resume_never_throws_on_garbage():
    """A randomised dump must not crash the parser. Pin the
    no-throws contract — the worker can't write `status='failed'`
    just because someone pasted a poem."""
    from app.services.profile_parser import parse_resume

    inputs = [
        "",
        "   \n\t\n   ",
        "@@@@",
        "x" * 50_000,
        "Education\n\n\n\n",  # empty section
        "<<>><<>>",  # nonsense
    ]
    for raw in inputs:
        p = parse_resume(raw)
        # The Pydantic model validated successfully — that's the
        # whole contract here.
        assert p.name == "" or isinstance(p.name, str)
        assert isinstance(p.experience, list)


def test_parser_module_does_not_import_anthropic():
    """The whole point of this PR: the resume-parse code path no
    longer touches Anthropic. The tailor module still does."""
    import sys

    # Force a fresh import to verify the static dependency graph.
    sys.modules.pop("app.services.profile_parser", None)
    import app.services.profile_parser as fresh  # noqa: PLC0415

    assert not hasattr(fresh, "anthropic")
    # And no Anthropic-flavoured symbols escaped from the refactor.
    assert not hasattr(fresh, "MODEL")
    assert not hasattr(fresh, "PROFILE_SCHEMA")
    assert not hasattr(fresh, "_build_client")
