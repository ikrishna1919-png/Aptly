"""Tests for the resume-tailoring endpoints.

Covers demo mode (no API key), the real LLM path with a mocked Anthropic
client, the per-job cache, and the DOCX export.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

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
from app.models.job_analysis import JobAnalysis
from app.services import tailor as tailor_module

DEMO_JOB_DESCRIPTION = (
    "We're hiring a Backend Engineer to build event-driven services in "
    "Python and Kafka, deployed on AWS. Bonus: experience with Postgres "
    "and observability tooling."
)


def _seed_job(session) -> Job:
    job = Job(
        source="greenhouse",
        external_id="acme-100",
        company="Acme",
        title="Senior Backend Engineer",
        url="https://example.com/apply",
        description=DEMO_JOB_DESCRIPTION,
        skills=["Python", "Kafka", "AWS", "PostgreSQL"],
        content_hash="hash-acme-100",
        source_updated_at=datetime.now(UTC) - timedelta(hours=2),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


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


@pytest.fixture
def settings_no_key():
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
        ANTHROPIC_API_KEY="",
    )


@pytest.fixture
def settings_with_key():
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
        ANTHROPIC_API_KEY="sk-test-fake",
    )


@pytest.fixture
def client(factories, settings_no_key):
    def override_db():
        with factories() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings_no_key
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


# ── Demo-mode endpoint tests ────────────────────────────────────────────────


def test_analyze_demo_mode(client):
    test_client, Session = client
    with Session() as s:
        job = _seed_job(s)

    res = test_client.post("/api/tailor/analyze", json={"job_id": job.id})
    assert res.status_code == 200
    body = res.json()
    assert body["demo_mode"] is True
    assert body["job_id"] == job.id

    a = body["analysis"]
    assert 0 <= a["match_score"] <= 100
    assert len(a["questions"]) == 3
    # Candidate has Python + AWS + PostgreSQL + Kafka — all 4 should match.
    assert {"Python", "Kafka", "AWS", "PostgreSQL"}.issubset(set(a["matched"]))
    assert any("[demo]" in q for q in a["questions"])


def test_analyze_caches_result(client):
    test_client, Session = client
    with Session() as s:
        job = _seed_job(s)

    test_client.post("/api/tailor/analyze", json={"job_id": job.id})
    with Session() as s:
        assert s.query(JobAnalysis).count() == 1

    # Second call should reuse the cached row.
    test_client.post("/api/tailor/analyze", json={"job_id": job.id})
    with Session() as s:
        assert s.query(JobAnalysis).count() == 1


def test_analyze_404(client):
    test_client, _ = client
    res = test_client.post("/api/tailor/analyze", json={"job_id": 99999})
    assert res.status_code == 404


def test_generate_demo_mode_uses_answers(client):
    test_client, Session = client
    with Session() as s:
        job = _seed_job(s)

    res = test_client.post(
        "/api/tailor/generate",
        json={
            "job_id": job.id,
            "answers": {
                "q1": "Led a 4B-event/day pipeline migration to Kafka",
                "q2": "Cut p95 latency from 480ms to 110ms",
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["demo_mode"] is True
    assert "Kafka" in body["resume"]["summary"]
    assert body["resume"]["experience"]
    assert "demo mode" in body["resume"]["ats_notes"].lower()


def test_docx_export(client):
    test_client, Session = client
    with Session() as s:
        job = _seed_job(s)

    gen = test_client.post("/api/tailor/generate", json={"job_id": job.id, "answers": {}}).json()

    res = test_client.post(
        "/api/tailor/docx",
        json={"resume": gen["resume"], "filename": "alex-acme"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert 'filename="alex-acme.docx"' in res.headers["content-disposition"]
    # DOCX files are ZIP archives — they start with "PK".
    assert res.content[:2] == b"PK"
    assert len(res.content) > 1500


# ── LLM path with mocked Anthropic client ───────────────────────────────────


class _MockAnthropicClient:
    """Stands in for `anthropic.Anthropic()` — captures the call args and
    returns a pre-baked structured JSON response.

    The real SDK returns blocks with a `.type` attribute and a `.text`
    attribute, so we use SimpleNamespace to mimic that shape."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        text_block = SimpleNamespace(type="text", text=json.dumps(self.payload))
        return SimpleNamespace(content=[text_block])


def test_analyze_uses_anthropic_when_key_present(factories, settings_with_key, monkeypatch):
    payload = {
        "match_score": 84,
        "top_skills": ["Python", "Kafka", "AWS"],
        "matched": ["Python", "Kafka"],
        "gaps": ["dbt"],
        "questions": [
            "What's your largest Kafka deployment?",
            "Describe one observability win.",
            "Why Acme?",
        ],
    }
    mock = _MockAnthropicClient(payload)
    monkeypatch.setattr(tailor_module, "_build_client", lambda s, c: mock)

    with factories() as s:
        job = _seed_job(s)
        result = tailor_module.analyze_job(s, job, settings=settings_with_key)

    assert result.match_score == 84
    assert result.questions[2] == "Why Acme?"
    # One call to messages.create, on Sonnet 4.6, with cache_control set.
    assert len(mock.calls) == 1
    call = mock.calls[0]
    assert call["model"] == "claude-sonnet-4-6"
    # System list with cache_control on the candidate block.
    assert any(
        isinstance(b, dict) and b.get("cache_control", {}).get("type") == "ephemeral"
        for b in call["system"]
    )
    # The schema sent to Anthropic must be Anthropic-accepted: every object
    # strict AND no unsupported numeric/string/array range constraints.
    schema = call["output_config"]["format"]["schema"]
    _assert_schema_accepted_by_anthropic(schema)
    # The schema sent to Anthropic must have additionalProperties:false on
    # every object — otherwise the API 400s with
    # "For 'object' type, 'additionalProperties' must be explicitly set to false".
    schema = call["output_config"]["format"]["schema"]
    assert _every_object_has_additional_properties_false(schema)


def test_analyze_cache_hits_avoid_second_llm_call(factories, settings_with_key, monkeypatch):
    payload = {
        "match_score": 70,
        "top_skills": ["Python"],
        "matched": ["Python"],
        "gaps": [],
        "questions": ["Q1?", "Q2?", "Q3?"],
    }
    mock = _MockAnthropicClient(payload)
    monkeypatch.setattr(tailor_module, "_build_client", lambda s, c: mock)

    with factories() as s:
        job = _seed_job(s)
        tailor_module.analyze_job(s, job, settings=settings_with_key)
        tailor_module.analyze_job(s, job, settings=settings_with_key)

    # Cache must short-circuit the second call.
    assert len(mock.calls) == 1


def test_generate_uses_anthropic_when_key_present(factories, settings_with_key, monkeypatch):
    payload = {
        "summary": "Senior backend engineer with Kafka + Python experience...",
        "skills": ["Python", "Kafka", "AWS", "PostgreSQL"],
        "experience": [
            {
                "company": "Forge Labs",
                "title": "Senior Software Engineer",
                "dates": "2023 – Present",
                "bullets": ["Led migration to event-driven Kafka services."],
            }
        ],
        "education": ["B.S. CS, CMU (2018)"],
        "ats_notes": "Re-ordered skills to lead with Python + Kafka per the JD.",
    }
    mock = _MockAnthropicClient(payload)
    monkeypatch.setattr(tailor_module, "_build_client", lambda s, c: mock)

    with factories() as s:
        job = _seed_job(s)
        resume = tailor_module.generate_resume(
            s, job, {"q1": "5 yrs Kafka"}, settings=settings_with_key
        )

    assert resume.skills[:2] == ["Python", "Kafka"]
    assert "Kafka" in resume.ats_notes or "Kafka" in resume.summary
    assert len(mock.calls) == 1
    # Generate must send a strict schema too — the TailoredResume has a
    # nested ExperienceBullet object, so this exercises the recursive walk
    # into $defs / array items.
    schema = mock.calls[0]["output_config"]["format"]["schema"]
    _assert_schema_accepted_by_anthropic(schema)


# ── JSON-schema strictness regression tests ─────────────────────────────────


def _every_object_has_additional_properties_false(schema: dict[str, Any]) -> bool:
    """Walk every object node in the schema and confirm it sets
    `additionalProperties: false`. Returns False with a useful repr in the
    assertion if any object is missing the flag."""
    missing: list[str] = []

    def visit(node: Any, path: str) -> None:
        if isinstance(node, list):
            for i, item in enumerate(node):
                visit(item, f"{path}[{i}]")
            return
        if not isinstance(node, dict):
            return
        is_object = node.get("type") == "object" or "properties" in node
        if is_object and node.get("additionalProperties") is not False:
            missing.append(path or "<root>")
        for key in ("properties", "patternProperties", "$defs", "definitions"):
            if key in node and isinstance(node[key], dict):
                for sub_key, sub in node[key].items():
                    visit(sub, f"{path}.{key}.{sub_key}")
        if "items" in node:
            visit(node["items"], f"{path}.items")
        if "prefixItems" in node:
            visit(node["prefixItems"], f"{path}.prefixItems")
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                visit(node[key], f"{path}.{key}")

    visit(schema, "")
    if missing:
        # Surface the offending paths so a failure points at the exact node.
        raise AssertionError(f"objects missing additionalProperties:false → {missing}")
    return True


def test_analysis_schema_is_strict_at_every_object_level():
    assert _every_object_has_additional_properties_false(tailor_module.ANALYSIS_SCHEMA)


def test_tailored_resume_schema_is_strict_at_every_object_level():
    schema = tailor_module.TAILORED_RESUME_SCHEMA
    assert _every_object_has_additional_properties_false(schema)
    # Sanity: the nested ExperienceBullet definition is present and strict.
    defs = schema.get("$defs") or schema.get("definitions") or {}
    exp_def = defs.get("ExperienceBullet")
    assert exp_def is not None, "ExperienceBullet $def should exist on TailoredResume"
    assert exp_def["additionalProperties"] is False
    # And the parser still accepts well-formed payloads — the strictification
    # didn't drop required/properties.
    parsed = tailor_module.TailoredResume.model_validate(
        {
            "summary": "s",
            "skills": ["Python"],
            "experience": [
                {"company": "C", "title": "T", "dates": "2020 – 2022", "bullets": ["b"]}
            ],
            "education": ["edu"],
            "ats_notes": "n",
        }
    )
    assert parsed.experience[0].company == "C"


# Constraints Anthropic's structured-output validator rejects (kept in sync
# with tailor._UNSUPPORTED_KEYS — change one, change the other).
_FORBIDDEN_KEYS = (
    # Numeric
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    # String
    "minLength",
    "maxLength",
    "pattern",
    # Array
    "minItems",
    "maxItems",
    "uniqueItems",
    "contains",
    "minContains",
    "maxContains",
)


def _scan_for_forbidden_keys(schema: dict[str, Any]) -> list[str]:
    """Walk the schema and return the (path, key) pairs for every
    forbidden keyword found. Empty list ⇒ schema is Anthropic-acceptable."""
    hits: list[str] = []

    def visit(node: Any, path: str) -> None:
        if isinstance(node, list):
            for i, item in enumerate(node):
                visit(item, f"{path}[{i}]")
            return
        if not isinstance(node, dict):
            return
        for key in _FORBIDDEN_KEYS:
            if key in node:
                hits.append(f"{path or '<root>'}.{key}")
        for key in ("properties", "patternProperties", "$defs", "definitions"):
            if key in node and isinstance(node[key], dict):
                for sub_key, sub in node[key].items():
                    visit(sub, f"{path}.{key}.{sub_key}")
        if "items" in node:
            visit(node["items"], f"{path}.items")
        if "prefixItems" in node:
            visit(node["prefixItems"], f"{path}.prefixItems")
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                visit(node[key], f"{path}.{key}")

    visit(schema, "")
    return hits


def _assert_schema_accepted_by_anthropic(schema: dict[str, Any]) -> None:
    """Assert the schema satisfies both Anthropic constraints we care about:
      1. every object node sets additionalProperties:false (the previous
         regression that landed in #10)
      2. no unsupported range/length/pattern keywords anywhere (this one)
    Surfaces precise paths so a regression points at the offending node."""
    _every_object_has_additional_properties_false(schema)
    hits = _scan_for_forbidden_keys(schema)
    if hits:
        raise AssertionError(
            "schema contains keys Anthropic's validator rejects "
            f"(For 'integer'/'string'/'array' type, properties ... are not supported): {hits}"
        )


def test_analysis_schema_has_no_unsupported_range_constraints():
    """Regression for 400: For 'integer' type, properties maximum,
    minimum are not supported. The match_score field is `ge=0, le=100` in
    Pydantic, so the rendered schema would include minimum/maximum unless
    we strip them on the way out."""
    assert _scan_for_forbidden_keys(tailor_module.ANALYSIS_SCHEMA) == []
    # And specifically: match_score has no range keys, but its description
    # still tells the model "0-100" so the constraint is preserved as prose.
    match_score = tailor_module.ANALYSIS_SCHEMA["properties"]["match_score"]
    assert "minimum" not in match_score and "maximum" not in match_score
    assert "0-100" in match_score["description"]
    # And: questions has no minItems/maxItems but still says "Three" in
    # the description and the system prompt names the exact count.
    questions = tailor_module.ANALYSIS_SCHEMA["properties"]["questions"]
    assert "minItems" not in questions and "maxItems" not in questions
    assert "Three" in questions["description"]
    assert "exactly three" in tailor_module._SYSTEM_ANALYZE.lower()


def test_tailored_resume_schema_has_no_unsupported_range_constraints():
    assert _scan_for_forbidden_keys(tailor_module.TAILORED_RESUME_SCHEMA) == []


def test_pydantic_models_still_validate_responses_against_their_constraints():
    """The Pydantic constraints (ge/le/min_length/max_length) are stripped
    from the wire schema but they MUST stay on the model — they're how we
    catch model output that's out of range when we parse the response."""
    # match_score ≤ 100 still enforced by the Pydantic model on parse.
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        tailor_module.Analysis.model_validate(
            {
                "match_score": 150,
                "top_skills": ["x"],
                "matched": ["x"],
                "gaps": [],
                "questions": ["a", "b", "c"],
            }
        )
    # Exactly three questions still enforced.
    with pytest.raises(pydantic.ValidationError):
        tailor_module.Analysis.model_validate(
            {
                "match_score": 50,
                "top_skills": ["x"],
                "matched": ["x"],
                "gaps": [],
                "questions": ["only one"],
            }
        )


# ── HTML-residue and empty-description regression tests ────────────────────


HTML_LADEN_DESCRIPTION = (
    "<div><h2>About Us</h2>"
    "<p>We&apos;re hiring a Backend Engineer.</p>"
    "<h3>What you&apos;ll do</h3>"
    "<ul>"
    "<li>Build services in Python and Kafka.</li>"
    "<li>Deploy on AWS.</li>"
    "</ul></div>"
)


def _seed_html_job(session) -> Job:
    """A job whose description is still raw HTML — simulates rows ingested
    before the strip_html rewrite."""
    job = Job(
        source="greenhouse",
        external_id="acme-html",
        company="Acme",
        title="Senior Backend Engineer",
        url="https://example.com/apply",
        description=HTML_LADEN_DESCRIPTION,
        skills=["Python", "Kafka", "AWS"],
        content_hash="hash-acme-html",
        source_updated_at=datetime.now(UTC) - timedelta(hours=2),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_tailor_sanitizes_html_in_jd_before_calling_anthropic(
    factories, settings_with_key, monkeypatch
):
    """Safety net: even if the row was stored as raw HTML, the prompt sent
    to the model is clean text — never `<p>About Us</p>`."""
    payload = {
        "match_score": 80,
        "top_skills": ["Python", "Kafka"],
        "matched": ["Python"],
        "gaps": [],
        "questions": ["q1?", "q2?", "q3?"],
    }
    mock = _MockAnthropicClient(payload)
    monkeypatch.setattr(tailor_module, "_build_client", lambda s, c: mock)

    with factories() as s:
        job = _seed_html_job(s)
        tailor_module.analyze_job(s, job, settings=settings_with_key)

    assert len(mock.calls) == 1
    user_content = mock.calls[0]["messages"][0]["content"]
    # No raw HTML left in the prompt.
    assert "<p>" not in user_content
    assert "<ul>" not in user_content
    assert "&apos;" not in user_content
    # The cleaned structure survived.
    assert "About Us" in user_content
    assert "- Build services in Python and Kafka." in user_content


def test_tailor_handles_empty_description_gracefully(client):
    """A job with an empty description must not crash analyze or generate."""
    test_client, Session = client
    with Session() as s:
        job = Job(
            source="manual",
            external_id="manual-empty",
            company="Aptly",
            title="Senior Engineer",
            url="https://example.com/apply",
            description=None,
            skills=[],
            source_updated_at=datetime.now(UTC) - timedelta(hours=1),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    res = test_client.post("/api/tailor/analyze", json={"job_id": job_id})
    assert res.status_code == 200
    assert len(res.json()["analysis"]["questions"]) == 3

    res2 = test_client.post("/api/tailor/generate", json={"job_id": job_id, "answers": {}})
    assert res2.status_code == 200
    assert res2.json()["resume"]["summary"]


def test_tailor_succeeds_on_previously_html_job(client):
    """End-to-end: a job whose description is still raw HTML (i.e. ingested
    before the strip_html rewrite) tailors successfully through the public
    endpoint. Demo mode — no API key needed."""
    test_client, Session = client
    with Session() as s:
        job = _seed_html_job(s)

    res = test_client.post("/api/tailor/analyze", json={"job_id": job.id})
    assert res.status_code == 200
    res2 = test_client.post("/api/tailor/generate", json={"job_id": job.id, "answers": {}})
    assert res2.status_code == 200


# ── Backfill CLI ───────────────────────────────────────────────────────────


def test_clean_descriptions_backfill_rewrites_html_in_place(monkeypatch, factories):
    """`python -m app.cli clean-descriptions` should normalize html-laden
    descriptions in place and leave clean ones alone."""
    from app.cli import clean_descriptions as cmd

    # Seed: one HTML row, one already-clean row, one None.
    with factories() as s:
        s.add(
            Job(
                source="greenhouse",
                external_id="html-1",
                company="Acme",
                title="Eng",
                url="https://x/1",
                description=HTML_LADEN_DESCRIPTION,
                skills=[],
                source_updated_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        s.add(
            Job(
                source="greenhouse",
                external_id="clean-1",
                company="Acme",
                title="Eng",
                url="https://x/2",
                description="Already clean text.\n\nNo markup here.",
                skills=[],
                source_updated_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        s.add(
            Job(
                source="manual",
                external_id="empty-1",
                company="Aptly",
                title="Eng",
                url="https://x/3",
                description=None,
                skills=[],
                source_updated_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        s.commit()

    # Wire the CLI's SessionLocal to our in-memory engine for this test.
    monkeypatch.setattr(cmd, "SessionLocal", factories)

    report = cmd.run(dry_run=False)
    assert report["scanned"] == 3
    assert report["htmlish"] == 1
    assert report["changed"] == 1

    # Verify the row is actually rewritten.
    with factories() as s:
        rewritten = s.query(Job).filter(Job.external_id == "html-1").one().description or ""
        assert "<p>" not in rewritten and "<ul>" not in rewritten
        assert "- Build services in Python and Kafka." in rewritten

    # Second run is a no-op.
    report2 = cmd.run(dry_run=False)
    assert report2["changed"] == 0


def test_clean_descriptions_dry_run_makes_no_changes(monkeypatch, factories):
    from app.cli import clean_descriptions as cmd

    with factories() as s:
        s.add(
            Job(
                source="greenhouse",
                external_id="html-2",
                company="Acme",
                title="Eng",
                url="https://x/4",
                description=HTML_LADEN_DESCRIPTION,
                skills=[],
                source_updated_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        s.commit()

    monkeypatch.setattr(cmd, "SessionLocal", factories)
    report = cmd.run(dry_run=True)
    assert report["dry_run"] is True
    assert report["changed"] == 1  # would-have-changed count

    with factories() as s:
        unchanged = s.query(Job).filter(Job.external_id == "html-2").one().description
        assert "<p>" in unchanged  # the row is untouched
