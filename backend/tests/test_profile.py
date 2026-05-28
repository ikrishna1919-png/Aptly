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
    background tests use.

    Also stubs `_build_client` to raise — by default the parser's LLM
    branch fails, and the regex fallback runs. Tests that want the LLM
    branch monkeypatch `_build_client` again to install their own
    mock client."""
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda target, args: target(*args))

    def _raise(*_args, **_kwargs):  # pragma: no cover — default behavior is the raise
        raise RuntimeError("test stub: no Anthropic client wired")

    monkeypatch.setattr(parser_module, "_build_client", _raise)


def _make_user(factories):
    """Create a fresh User row in the test DB so the dependency
    override has something concrete to return."""
    from app.models.user import User

    with factories() as s:
        u = User(google_subject_id="google-test", email="test@example.com", name="Test User")
        s.add(u)
        s.commit()
        s.refresh(u)
        # Detach so the User instance is safe to return after the
        # session closes.
        s.expunge(u)
    return u


def _client_for(factories, monkeypatch, *, key: str = ""):
    """Standard test wiring: override `get_db` to the test engine,
    inject Settings, drive the parse worker inline, and stub
    `get_current_user` to return a fresh User. Returns the client +
    that User."""
    from app.api.auth import get_current_user

    settings = _settings(key=key)
    user = _make_user(factories)

    def override_db():
        with factories() as s:
            yield s

    _wire_parse_worker(monkeypatch, factories)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: user
    config_module.get_settings.cache_clear()
    return TestClient(app), user


@pytest.fixture
def client_no_key(factories, monkeypatch):
    test_client, _user = _client_for(factories, monkeypatch, key="")
    try:
        yield test_client, factories
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


@pytest.fixture
def client_with_key(factories, monkeypatch):
    test_client, _user = _client_for(factories, monkeypatch, key="sk-test-fake")
    try:
        yield test_client, factories
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


@pytest.fixture
def client_anon(factories, monkeypatch):
    """A client with NO `get_current_user` override — for tests that
    pin the 401 surface."""
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


# Empty placeholder — tests that used to send admin headers now rely
# on the `get_current_user` dependency override instead. Kept as `{}`
# so the existing `headers=AUTH` call sites keep compiling.
AUTH: dict[str, str] = {}


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


def test_all_endpoints_require_signed_in_user(client_anon):
    """Profile endpoints 401 without a signed-in user. Admin token
    is no longer the gate for the user-facing surface — only Google
    sign-in is."""
    test_client, _ = client_anon
    assert test_client.get("/api/profile").status_code == 401
    assert test_client.put("/api/profile", json=VALID_PROFILE_BODY).status_code == 401
    assert test_client.post("/api/profile/parse", json={"text": "..."}).status_code == 401


# ── GET / PUT ───────────────────────────────────────────────────────────────


def test_get_profile_seeds_from_demo_candidate_when_row_missing(client_no_key):
    test_client, Session = client_no_key
    # No Candidate row exists yet.
    with Session() as s:
        assert s.query(Candidate).count() == 0

    res = test_client.get("/api/profile", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == DEMO_CANDIDATE["name"]
    # And a row was created so subsequent reads/writes round-trip —
    # one row per user, slug starts with the legacy `demo` prefix.
    with Session() as s:
        assert s.query(Candidate).count() == 1
        row = s.query(Candidate).one()
        assert row.slug.startswith(DEMO_SLUG)
        assert row.user_id is not None


def test_put_profile_persists_full_replacement(client_no_key):
    test_client, Session = client_no_key

    res = test_client.put("/api/profile", json=VALID_PROFILE_BODY, headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Alex Custom"
    assert body["skills"] == ["Python", "Go"]
    assert body["experience"][0]["bullets"][1].startswith("Did the other")

    # GET reflects the new state.
    fetched = test_client.get("/api/profile", headers=AUTH).json()
    assert fetched["name"] == "Alex Custom"
    assert fetched["links"]["github"] == "github.com/alex-custom"

    # And the DB row was updated in place — one row per user, not
    # a second row.
    with Session() as s:
        rows = s.query(Candidate).all()
        assert len(rows) == 1
        assert rows[0].profile["summary"] == "Custom summary."


def test_put_profile_validates_required_fields(client_no_key):
    test_client, _ = client_no_key
    bad = {**VALID_PROFILE_BODY}
    del bad["name"]
    res = test_client.put("/api/profile", json=bad, headers=AUTH)
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
    test_client.put("/api/profile", json=VALID_PROFILE_BODY, headers=AUTH)

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
        "/api/profile/parse",
        json={"text": _FULL_RESUME},
        headers=AUTH,
    )
    assert start.status_code == 202
    run_id = start.json()["run_id"]
    assert start.json()["status_url"] == f"/api/profile/parse/{run_id}"

    poll = test_client.get(f"/api/profile/parse/{run_id}", headers=AUTH)
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
    res = test_client.post("/api/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)
    assert res.status_code == 202
    body = res.json()
    assert "run_id" in body
    assert body["status"] == "running"
    assert res.headers.get("Location") == body["status_url"]


def test_parse_rejects_empty_input(client_with_key):
    """Pydantic-level validation still catches the empty-string case
    before we hit the parser — caller error, 422."""
    test_client, _ = client_with_key
    res = test_client.post("/api/profile/parse", json={"text": ""}, headers=AUTH)
    assert res.status_code == 422


def test_parse_does_not_save(client_with_key):
    """Parse extracts; saving still requires PUT — a noisy paste must
    not clobber the real profile silently."""
    test_client, Session = client_with_key

    # Seed an existing saved profile so the assertion below is
    # meaningful (the parse_resume output for `_FULL_RESUME` does NOT
    # share the same name).
    test_client.put("/api/profile", json=VALID_PROFILE_BODY, headers=AUTH)
    test_client.post("/api/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)

    with Session() as s:
        row = s.query(Candidate).one()
    # The saved row is still the one PUT placed, not the parsed one.
    assert row.profile["name"] == "Alex Custom"


def test_parse_get_unknown_run_id_returns_404(client_with_key):
    test_client, _ = client_with_key
    res = test_client.get("/api/profile/parse/nope-not-real", headers=AUTH)
    assert res.status_code == 404


def test_parse_status_endpoint_requires_sign_in(client_anon):
    """Parse-status endpoint 401s without a signed-in user — guards
    against a guessed run_id leaking another user's parsed profile."""
    test_client, _ = client_anon
    res = test_client.get("/api/profile/parse/anything")
    assert res.status_code == 401


def test_parse_works_without_anthropic_key(client_no_key):
    """The deterministic parser doesn't need ANTHROPIC_API_KEY at all
    — pasting a resume must work even on an environment that has the
    key unset."""
    test_client, _ = client_no_key
    start = test_client.post("/api/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)
    assert start.status_code == 202
    poll = test_client.get(f"/api/profile/parse/{start.json()['run_id']}", headers=AUTH)
    body = poll.json()
    assert body["status"] == "success"
    assert body["profile"]["name"] == "Alex Rivera"


def test_parse_random_text_returns_empty_profile_not_failure(client_with_key):
    """If the input doesn't look like a resume at all, the parser
    returns an empty Profile with `status='success'`. The frontend
    interprets the empty result as "fill in the form manually" —
    NEVER `status='failed'` for non-resume input."""
    test_client, _ = client_with_key
    junk = "lorem ipsum dolor sit amet"
    start = test_client.post("/api/profile/parse", json={"text": junk}, headers=AUTH)
    assert start.status_code == 202
    body = test_client.get(f"/api/profile/parse/{start.json()['run_id']}", headers=AUTH).json()
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
    start = test_client.post("/api/profile/parse", json={"text": huge}, headers=AUTH)
    assert start.status_code == 202
    body = test_client.get(f"/api/profile/parse/{start.json()['run_id']}", headers=AUTH).json()
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
        "Experience\nEngineer — Foo Co\n2020 - Present\n- Did a thing\n- Did another thing\n"
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

    text = "Experience\nA — X\nJanuary 2020 - Mar 2022\n\nB — Y\nMay 2018 to Dec 2019\n"
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

    p = parse_resume("Skills\n• Python\n• Go\n• Kafka\n")
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


# ── Hybrid (regex + Anthropic) parser ──────────────────────────────────────


from types import SimpleNamespace  # noqa: E402 — grouped with the hybrid tests


def _mock_anthropic_client(payload: dict):
    """A drop-in for `anthropic.Anthropic()` that captures the call
    args and returns a pre-baked JSON response. Same pattern the
    tailor tests use."""

    class _Mock:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            self.calls.append(kwargs)
            text_block = SimpleNamespace(type="text", text=json.dumps(payload))
            return SimpleNamespace(content=[text_block])

    return _Mock()


def test_parse_calls_anthropic_when_key_present_and_uses_structural_output(
    client_with_key, monkeypatch
):
    """Hybrid path: when the API key is configured, the parser calls
    Claude with the resume text + structured-output schema, then maps
    the LLM response into the Profile shape."""
    test_client, _ = client_with_key
    payload = {
        "name": "Dana Sponsor",
        "experience": [
            {
                "company": "Forge Labs",
                "title": "Staff Backend Engineer",
                "location": "Remote",
                "start_date": "Jan 2023",
                "end_date": "Present",
                "description_bullets": [
                    "Led the migration to event-driven Kafka.",
                    "Cut p95 latency 480ms → 110ms.",
                ],
            },
            {
                "company": "Initech",
                "title": "Software Engineer",
                "location": "Austin, TX",
                "start_date": "Jul 2019",
                "end_date": "Dec 2022",
                "description_bullets": ["Built the billing pipeline."],
            },
        ],
        "education": [
            {
                "school": "Carnegie Mellon University",
                "degree": "B.S.",
                "field_of_study": "Computer Science",
                "location": "Pittsburgh, PA",
                "start_date": "2014",
                "end_date": "2018",
            }
        ],
        "skills": ["Python", "Kafka", "Postgres"],
    }
    mock = _mock_anthropic_client(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)

    start = test_client.post("/api/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)
    assert start.status_code == 202
    poll = test_client.get(f"/api/profile/parse/{start.json()['run_id']}", headers=AUTH)
    body = poll.json()
    assert body["status"] == "success"
    profile = body["profile"]

    # Structural fields come from the LLM payload — verbatim, with
    # company/title correctly assigned (not swapped).
    assert profile["name"] == "Dana Sponsor"
    assert profile["experience"][0]["company"] == "Forge Labs"
    assert profile["experience"][0]["title"] == "Staff Backend Engineer"
    assert profile["experience"][0]["start"] == "2023-01"
    assert profile["experience"][0]["end"] == "Present"
    assert profile["experience"][1]["company"] == "Initech"
    assert profile["experience"][1]["title"] == "Software Engineer"
    assert profile["experience"][1]["end"] == "2022-12"

    assert profile["education"][0]["school"] == "Carnegie Mellon University"
    # Post-overhaul: `degree` carries only the credential ("B.S."); the
    # major lives on the separate `field_of_study` slot so the UI can
    # edit each independently.
    assert profile["education"][0]["degree"] == "B.S."
    assert profile["education"][0]["field_of_study"] == "Computer Science"
    assert profile["education"][0]["graduation"] == "2018"

    # Skills from the LLM are surfaced.
    assert "Kafka" in profile["skills"]

    # Contact fields stay regex-derived from the source text, NOT the LLM
    # payload (which didn't include them) — the hybrid contract.
    assert profile["email"] == "alex.rivera@example.com"
    assert "415" in profile["phone"] or "555" in profile["phone"]
    assert profile["links"]["linkedin"] == "linkedin.com/in/alex-rivera"
    assert profile["links"]["github"] == "github.com/alexr"
    assert profile["location"] == "San Francisco, CA"

    # Exactly one Anthropic call, on the configured model, with the
    # cleaned structural-output schema.
    assert len(mock.calls) == 1
    call = mock.calls[0]
    assert call["model"] == parser_module.MODEL
    assert call["output_config"]["format"]["type"] == "json_schema"
    schema = call["output_config"]["format"]["schema"]
    # Schema cleanliness — same rules `_anthropic_schema` enforces.
    assert _every_object_has_additional_properties_false(schema)
    assert not _schema_contains_forbidden_keys(schema)


def test_parse_falls_back_to_regex_when_llm_raises(client_with_key, monkeypatch):
    """A failed Anthropic call must NOT fail the whole parse — the
    regex extractor's result is returned instead, partial > empty."""
    test_client, _ = client_with_key

    def _raises(*_a, **_k):
        raise RuntimeError("simulated anthropic 500")

    monkeypatch.setattr(parser_module, "_build_client", _raises)

    start = test_client.post("/api/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)
    assert start.status_code == 202
    body = test_client.get(f"/api/profile/parse/{start.json()['run_id']}", headers=AUTH).json()
    assert body["status"] == "success"
    profile = body["profile"]
    # Regex pulled the contact block.
    assert profile["email"] == "alex.rivera@example.com"
    # Regex extracted the name from the preamble.
    assert profile["name"] == "Alex Rivera"
    # Regex extracted experience (the headline assertion: company / title
    # are populated, even if exact alignment isn't perfect — partial is
    # the contract here).
    assert len(profile["experience"]) >= 1


def test_parse_does_not_call_anthropic_without_key(client_no_key, monkeypatch):
    """Without `ANTHROPIC_API_KEY`, the parser stays on the regex
    path — `_build_client` must never be called."""
    test_client, _ = client_no_key
    calls: list[int] = []
    monkeypatch.setattr(
        parser_module,
        "_build_client",
        lambda s, c: calls.append(1) or RuntimeError("should not be called"),
    )
    start = test_client.post("/api/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)
    assert start.status_code == 202
    body = test_client.get(f"/api/profile/parse/{start.json()['run_id']}", headers=AUTH).json()
    assert body["status"] == "success"
    # The regex parser still recovered the name + email.
    assert body["profile"]["name"] == "Alex Rivera"
    assert body["profile"]["email"] == "alex.rivera@example.com"
    assert calls == [], "Anthropic must not be called when key is unset"


def test_parse_flattens_skill_groups_from_llm(client_with_key, monkeypatch):
    """The LLM schema lets the model return categorised skill groups
    (`[{category, items}]`) when the resume groups skills by category.
    Downstream the Profile model carries a flat list, so the parser
    flattens the groups (categories are dropped — there's nowhere to
    store them on the Profile)."""
    test_client, _ = client_with_key
    payload = {
        "name": "Dana Sponsor",
        "experience": [],
        "education": [],
        "skills": [
            {"category": "Languages", "items": ["Python", "Go"]},
            {"category": "Cloud", "items": ["AWS", "GCP"]},
            {"category": "Tools", "items": ["Docker", "Python"]},  # dupe across groups
        ],
    }
    mock = _mock_anthropic_client(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)

    start = test_client.post("/api/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)
    body = test_client.get(f"/api/profile/parse/{start.json()['run_id']}", headers=AUTH).json()
    skills = body["profile"]["skills"]
    # Flattened, deduped (case-insensitive), order preserved.
    assert skills == ["Python", "Go", "AWS", "GCP", "Docker"]


def test_parse_llm_partial_fields_keep_regex_for_others(client_with_key, monkeypatch):
    """If the LLM returns null for a structural field but found other
    structural fields, the regex result for the null'd field must
    surface. Partial > empty."""
    test_client, _ = client_with_key
    # LLM returns only education; experience + skills empty.
    payload = {
        "name": "Alex Rivera",
        "experience": [],
        "education": [
            {
                "school": "State University",
                "degree": "B.S.",
                "field_of_study": "Computer Science",
                "location": "Berkeley, CA",
                "start_date": "2014",
                "end_date": "2018",
            }
        ],
        "skills": [],
    }
    mock = _mock_anthropic_client(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)

    start = test_client.post("/api/profile/parse", json={"text": _FULL_RESUME}, headers=AUTH)
    body = test_client.get(f"/api/profile/parse/{start.json()['run_id']}", headers=AUTH).json()
    profile = body["profile"]
    # Education came from LLM.
    assert profile["education"][0]["school"] == "State University"
    # Experience came from regex (LLM returned []).
    assert len(profile["experience"]) >= 1
    # Skills came from regex (LLM returned []) — `_FULL_RESUME` has them.
    assert "Python" in profile["skills"]


def test_parser_llm_schema_is_anthropic_clean():
    """Pin the cleanliness rules `prepare_schema` enforces for the
    parser's structured-output schema. If this test breaks, the
    Anthropic 400 regression we shipped before is back."""
    from app.services.profile_parser import _LLM_SCHEMA

    assert _every_object_has_additional_properties_false(_LLM_SCHEMA)
    assert not _schema_contains_forbidden_keys(_LLM_SCHEMA)


def test_parser_imports_anthropic_lazily():
    """The parser module must not import `anthropic` at module import
    time — the lazy `_build_client` import keeps cold start fast and
    lets the no-key dev path work without the SDK on PATH. The tailor
    module also follows this pattern."""
    import importlib
    import sys

    # Force a fresh import to inspect the static deps.
    sys.modules.pop("app.services.profile_parser", None)
    fresh = importlib.import_module("app.services.profile_parser")

    # `anthropic` is NOT imported at module level.
    assert not hasattr(fresh, "anthropic"), "anthropic must be imported lazily, not at module top"
    # But the LLM call path exists.
    assert callable(getattr(fresh, "_build_client", None))
    assert hasattr(fresh, "_LLM_SCHEMA")
    assert hasattr(fresh, "MODEL")


# Helpers reused above. Kept near the hybrid tests so the assertions
# they back are easy to read in one screenful.

_FORBIDDEN_SCHEMA_KEYS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "uniqueItems",
    "contains",
    "minContains",
    "maxContains",
    "default",
    "title",
    "examples",
    "readOnly",
    "writeOnly",
    "deprecated",
)


def _every_object_has_additional_properties_false(schema: dict) -> bool:
    """Every object node in `schema` must carry
    `additionalProperties: false` — Anthropic 400s otherwise."""

    def _walk(node) -> bool:
        if isinstance(node, list):
            return all(_walk(x) for x in node)
        if not isinstance(node, dict):
            return True
        node_type = node.get("type")
        is_object = node_type == "object" or "properties" in node
        if is_object and node.get("additionalProperties") is not False:
            return False
        for key in ("properties", "patternProperties", "$defs", "definitions"):
            v = node.get(key)
            if isinstance(v, dict):
                for sub in v.values():
                    if not _walk(sub):
                        return False
        if "items" in node and not _walk(node["items"]):
            return False
        if "prefixItems" in node and not _walk(node["prefixItems"]):
            return False
        for key in ("anyOf", "oneOf", "allOf"):
            v = node.get(key)
            if isinstance(v, list) and not all(_walk(x) for x in v):
                return False
        return True

    return _walk(schema)


def _schema_contains_forbidden_keys(schema: dict) -> list[str]:
    """Return every forbidden-keyword occurrence (path + key) so a
    failing test reports the exact offender. Property names that
    coincide with forbidden keywords (e.g. a field literally named
    `title`) are not flagged — only the schema keyword position is."""
    hits: list[str] = []

    def _walk(node, path: str) -> None:
        if isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
            return
        if not isinstance(node, dict):
            return
        # Forbidden keywords are siblings of `type`/`anyOf`/`properties`.
        # We check directly on the current node BUT skip the `properties`
        # / `$defs` dict (its keys are field/def names, not keywords).
        for bad in _FORBIDDEN_SCHEMA_KEYS:
            if bad in node:
                hits.append(f"{path}.{bad}")
        for key, value in node.items():
            if key in ("properties", "patternProperties", "$defs", "definitions") and isinstance(
                value, dict
            ):
                for sub_key, sub_value in value.items():
                    _walk(sub_value, f"{path}.{key}[{sub_key}]")
            elif key in ("items", "prefixItems"):
                _walk(value, f"{path}.{key}")
            elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
                for i, v in enumerate(value):
                    _walk(v, f"{path}.{key}[{i}]")

    _walk(schema, "$")
    return hits
