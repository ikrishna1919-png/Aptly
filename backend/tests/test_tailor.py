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
            job, {"q1": "5 yrs Kafka"}, settings=settings_with_key
        )

    assert resume.skills[:2] == ["Python", "Kafka"]
    assert "Kafka" in resume.ats_notes or "Kafka" in resume.summary
    assert len(mock.calls) == 1
    # Generate must send a strict schema too — the TailoredResume has a
    # nested ExperienceBullet object, so this exercises the recursive walk
    # into $defs / array items.
    schema = mock.calls[0]["output_config"]["format"]["schema"]
    assert _every_object_has_additional_properties_false(schema)


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
