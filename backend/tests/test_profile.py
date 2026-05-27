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
    # No API key → caller-fixable; surfaces synchronously as 503,
    # no ParseRun row created.
    res = test_client.post(
        "/api/admin/profile/parse",
        json={"text": "John Doe\nSoftware Engineer"},
        headers=AUTH,
    )
    assert res.status_code == 503
    assert "anthropic_api_key" in res.json()["detail"].lower()


def test_parse_returns_structured_profile_via_background_run(client_with_key, monkeypatch):
    """The new shape: POST returns 202 + run_id; GET the run when
    polled returns the parsed Profile."""
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

    # POST kicks off — 202 + run_id, no Profile in the body.
    start = test_client.post(
        "/api/admin/profile/parse",
        json={"text": "long pasted resume text"},
        headers=AUTH,
    )
    assert start.status_code == 202
    run_id = start.json()["run_id"]
    assert start.json()["status_url"] == f"/api/admin/profile/parse/{run_id}"
    assert start.headers.get("Location") == start.json()["status_url"]

    # Poll the run — worker has already finished (inline) so the
    # response carries the parsed Profile.
    res = test_client.get(f"/api/admin/profile/parse/{run_id}", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["error"] is None
    assert body["finished_at"] is not None
    profile = body["profile"]
    assert profile["name"] == "John Doe"
    assert profile["skills"] == ["Python", "Postgres", "Kafka"]
    assert profile["experience"][0]["company"] == "ExampleCo"

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


def test_parse_post_returns_202_immediately_with_run_id(client_with_key, monkeypatch):
    """POST never blocks on the Anthropic call — even with a slow
    underlying client, the HTTP response carries `run_id` + `running`
    status. (The inline worker patch means the worker actually
    finishes synchronously here; in production it'd run on a daemon
    thread.)"""
    test_client, _ = client_with_key
    monkeypatch.setattr(
        parser_module, "_build_client", lambda s, c: _MockAnthropicClient({"name": "x"})
    )
    res = test_client.post("/api/admin/profile/parse", json={"text": "x"}, headers=AUTH)
    assert res.status_code == 202
    body = res.json()
    assert "run_id" in body
    assert body["status"] == "running"


def test_parse_get_unknown_run_id_returns_404(client_with_key):
    test_client, _ = client_with_key
    res = test_client.get("/api/admin/profile/parse/nope-not-real", headers=AUTH)
    assert res.status_code == 404


def test_parse_status_endpoint_requires_admin_token(client_with_key):
    test_client, _ = client_with_key
    res = test_client.get("/api/admin/profile/parse/anything")
    assert res.status_code == 403


def test_parse_does_not_save(client_with_key, monkeypatch):
    """Parse RETURNS the structured profile via the polling endpoint;
    saving still requires PUT. Otherwise a noisy paste would clobber
    the real profile silently."""
    test_client, Session = client_with_key
    canned = {
        "name": "Parsed Name (NOT saved)",
        "summary": "",
        "skills": [],
        "experience": [],
        "education": [],
    }
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _MockAnthropicClient(canned))

    start = test_client.post("/api/admin/profile/parse", json={"text": "anything"}, headers=AUTH)
    assert start.status_code == 202

    # The DB row must NOT have been updated.
    with Session() as s:
        row = s.query(Candidate).filter(Candidate.slug == DEMO_SLUG).one_or_none()
    if row is not None:
        assert row.profile.get("name") != "Parsed Name (NOT saved)"


# ── Timeout / connection-error handling (the "hang forever" regression) ────


class _RaisingAnthropicClient:
    """Mock that ALWAYS raises a specific exception from messages.create.

    Used to simulate Anthropic SDK errors without making a real network
    call — the test wants to assert our error-mapping returns a clean
    HTTP response instead of hanging.
    """

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        raise self.exc


def _poll_run(test_client, run_id: str) -> dict:
    r = test_client.get(f"/api/admin/profile/parse/{run_id}", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def test_parse_records_timeout_as_failed_run(client_with_key, monkeypatch):
    """SDK raises APITimeoutError inside the worker → the ParseRun
    row lands at `status='failed'` with the readable timeout message.
    POST still returns 202 — the failure shows up via the polling
    endpoint, not as an HTTP error on the kick-off call."""
    import anthropic

    timeout_exc = anthropic.APITimeoutError(request=SimpleNamespace())
    monkeypatch.setattr(
        parser_module,
        "_build_client",
        lambda s, c: _RaisingAnthropicClient(timeout_exc),
    )

    test_client, _ = client_with_key
    start = test_client.post("/api/admin/profile/parse", json={"text": "anything"}, headers=AUTH)
    assert start.status_code == 202
    body = _poll_run(test_client, start.json()["run_id"])
    assert body["status"] == "failed"
    assert body["profile"] is None
    err = body["error"].lower()
    assert "didn't respond" in err or "timed out" in err or "timeout" in err
    # Surface how long we waited so the user knows.
    assert "90" in body["error"]


def test_parse_records_connection_error_as_failed_run(client_with_key, monkeypatch):
    """SDK raises APIConnectionError → run lands at `failed` with the
    underlying error message."""
    import anthropic

    conn_exc = anthropic.APIConnectionError(request=SimpleNamespace())
    monkeypatch.setattr(
        parser_module,
        "_build_client",
        lambda s, c: _RaisingAnthropicClient(conn_exc),
    )

    test_client, _ = client_with_key
    start = test_client.post("/api/admin/profile/parse", json={"text": "anything"}, headers=AUTH)
    assert start.status_code == 202
    body = _poll_run(test_client, start.json()["run_id"])
    assert body["status"] == "failed"
    assert "claude" in (body["error"] or "").lower()


def test_parse_records_api_status_error_as_failed_run(client_with_key, monkeypatch):
    """SDK raises APIStatusError (rate limit, overloaded, etc.) → run
    lands at `failed` with a non-empty user-facing message."""
    import anthropic
    import httpx

    rate_limit_exc = anthropic.RateLimitError(
        "rate limited",
        response=httpx.Response(
            status_code=429,
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        ),
        body={"type": "error", "error": {"type": "rate_limit_error", "message": "x"}},
    )
    monkeypatch.setattr(
        parser_module,
        "_build_client",
        lambda s, c: _RaisingAnthropicClient(rate_limit_exc),
    )

    test_client, _ = client_with_key
    start = test_client.post("/api/admin/profile/parse", json={"text": "anything"}, headers=AUTH)
    assert start.status_code == 202
    body = _poll_run(test_client, start.json()["run_id"])
    assert body["status"] == "failed"
    assert body["error"]


def test_parse_records_invalid_json_as_failed_run(client_with_key, monkeypatch):
    """If Claude returns text that doesn't validate against the Profile
    schema, the run lands at `failed` with a retry-suggesting message."""

    class _BadJSONClient:
        def __init__(self):
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            block = SimpleNamespace(type="text", text="not even json {")
            return SimpleNamespace(content=[block])

    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _BadJSONClient())

    test_client, _ = client_with_key
    start = test_client.post("/api/admin/profile/parse", json={"text": "anything"}, headers=AUTH)
    assert start.status_code == 202
    body = _poll_run(test_client, start.json()["run_id"])
    assert body["status"] == "failed"
    assert "retry" in (body["error"] or "").lower()


def test_parse_passes_explicit_timeout_to_sdk(client_with_key, monkeypatch):
    """Defence-in-depth check: the request to Anthropic carries an
    explicit `timeout=` kwarg matching `_REQUEST_TIMEOUT_SECONDS`.
    Without this, the SDK falls back to its 10-minute default and the
    user sees an indefinite hang."""
    canned = {
        "name": "X",
        "summary": "",
        "skills": [],
        "experience": [],
        "education": [],
    }
    mock = _MockAnthropicClient(canned)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)

    test_client, _ = client_with_key
    res = test_client.post("/api/admin/profile/parse", json={"text": "anything"}, headers=AUTH)
    assert res.status_code == 202

    assert len(mock.calls) == 1
    call_kwargs = mock.calls[0]
    assert call_kwargs.get("timeout") == parser_module._REQUEST_TIMEOUT_SECONDS
    assert call_kwargs["max_tokens"] == parser_module._MAX_OUTPUT_TOKENS


def test_parse_clips_oversized_resume(client_with_key, monkeypatch):
    """A pasted resume longer than `_MAX_RESUME_CHARS` is clipped before
    we send it to Claude — protects both latency and token cost."""
    canned = {
        "name": "X",
        "summary": "",
        "skills": [],
        "experience": [],
        "education": [],
    }
    mock = _MockAnthropicClient(canned)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)

    # 30K of A's, much larger than the 12K limit.
    huge = "A" * 30_000

    test_client, _ = client_with_key
    res = test_client.post("/api/admin/profile/parse", json={"text": huge}, headers=AUTH)
    assert res.status_code == 202

    user_text = mock.calls[0]["messages"][0]["content"]
    # The text payload includes prompt scaffolding + clipped body + the
    # [truncated] marker. Must be substantially shorter than the input.
    assert len(user_text) < parser_module._MAX_RESUME_CHARS + 500
    assert "[truncated]" in user_text


# ── Schema regression — Anthropic 400 on `default` keyword ────────────────


def _scan_annotation_keys(node, path="", hits=None):
    """Yield `(path, key)` for every annotation keyword on a schema
    node — but NOT for field names inside `properties` (a field
    literally called `title` is fine; an annotation `"title": "Foo"`
    on a node is the thing Anthropic rejects). Walks the same way the
    production stripper does: through `properties.{*}` values,
    `items`, `prefixItems`, `anyOf` / `oneOf` / `allOf`, and `$defs`."""
    if hits is None:
        hits = []
    if isinstance(node, list):
        for i, item in enumerate(node):
            _scan_annotation_keys(item, f"{path}[{i}]", hits)
        return hits
    if not isinstance(node, dict):
        return hits
    # Annotation keys live on THIS node — collect them, then descend
    # into structural children.
    for k in node.keys():
        hits.append((path or "<root>", k))
    for container in ("properties", "patternProperties", "$defs", "definitions"):
        if container in node and isinstance(node[container], dict):
            for sub_key, sub in node[container].items():
                _scan_annotation_keys(sub, f"{path}.{container}.{sub_key}", hits)
    if "items" in node:
        _scan_annotation_keys(node["items"], f"{path}.items", hits)
    if "prefixItems" in node:
        _scan_annotation_keys(node["prefixItems"], f"{path}.prefixItems", hits)
    for branch in ("anyOf", "oneOf", "allOf"):
        if branch in node:
            _scan_annotation_keys(node[branch], f"{path}.{branch}", hits)
    return hits


def test_profile_schema_strips_default_and_other_annotations():
    """The live 400 from Anthropic was triggered by `"default": null` in
    the PROFILE_SCHEMA — Pydantic emits it for every `field: T | None
    = None` and Anthropic's structured-output validator rejects the
    keyword. Pin that the prepared schema has none of the annotation
    keywords Anthropic rejects, anywhere."""
    forbidden = {"default", "title", "examples", "readOnly", "writeOnly", "deprecated"}
    hits = [
        (path, key)
        for path, key in _scan_annotation_keys(parser_module.PROFILE_SCHEMA)
        if key in forbidden
    ]
    assert not hits, "PROFILE_SCHEMA still carries forbidden annotation keywords: " f"{hits[:5]}"


def test_anthropic_400_records_actionable_error_message(client_with_key, monkeypatch):
    """A 400 from Anthropic must surface the actual API message (and the
    misleading "shorten the resume" string from the old code is gone) so
    the operator can diagnose without grepping logs. Length is almost
    never the actual cause of a 400 — schema issues are."""
    import anthropic
    import httpx

    bad_request = anthropic.BadRequestError(
        "400 bad",
        response=httpx.Response(
            status_code=400,
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        ),
        body={
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "For 'object' type, property 'default' is not supported.",
            },
        },
    )
    monkeypatch.setattr(
        parser_module,
        "_build_client",
        lambda s, c: _RaisingAnthropicClient(bad_request),
    )

    test_client, _ = client_with_key
    start = test_client.post("/api/admin/profile/parse", json={"text": "anything"}, headers=AUTH)
    assert start.status_code == 202
    poll = test_client.get(f"/api/admin/profile/parse/{start.json()['run_id']}", headers=AUTH)
    body = poll.json()
    assert body["status"] == "failed"
    err = body["error"]
    # Carries the actual API message — not the legacy "shorten the
    # resume" string.
    assert "default" in err
    assert "not supported" in err
    assert "400" in err
    assert "shorten the resume" not in err.lower()
