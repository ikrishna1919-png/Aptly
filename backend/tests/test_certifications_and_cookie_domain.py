"""Certifications: model field, LLM schema, parser merge, prompt
guidance, DOCX render — plus a pin that the session cookie carries
no `Domain` attribute (the Task B failure mode).

The certifications work in this PR is mostly schema + prompt
plumbing; the value is in the regression pins below so a future
"unify certifications and achievements" refactor can't quietly
break the misclassification fix.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models.parse_run import PARSE_STATUS_SUCCESS, ParseRun
from app.services import profile_parser as parser_module
from app.services.profile_parser import (
    Profile,
    ProfileCertification,
    _LLMCertification,
    _to_profile_certification,
)


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


def _seed_run(Session, run_id: str = "cert-run") -> None:
    from app.models.parse_run import PARSE_STATUS_RUNNING

    with Session() as s:
        s.add(ParseRun(run_id=run_id, status=PARSE_STATUS_RUNNING, user_id=None))
        s.commit()


def _row(Session, run_id: str) -> ParseRun:
    with Session() as s:
        return s.execute(select(ParseRun).where(ParseRun.run_id == run_id)).scalar_one()


# ─── Model + schema ─────────────────────────────────────────────────────────


class TestCertificationModel:
    def test_profile_carries_a_certifications_list(self):
        p = Profile(name="X")
        assert p.certifications == []

    def test_certification_required_name_only(self):
        c = ProfileCertification(name="AWS Certified Solutions Architect")
        assert c.issuer is None
        assert c.date is None
        assert c.credential_id is None

    def test_to_profile_certification_strips_empties_to_none(self):
        out = _to_profile_certification(
            _LLMCertification(
                name="PMP",
                issuer="PMI",
                date="2024",
                credential_id="",
            )
        )
        assert out is not None
        assert out.name == "PMP"
        assert out.issuer == "PMI"
        assert out.date == "2024"
        # Empty string → None so the JSON stays tidy.
        assert out.credential_id is None

    def test_to_profile_certification_drops_unnamed_entries(self):
        """An entry without a name is noise — drop rather than ship a
        blank credential row."""
        assert _to_profile_certification(_LLMCertification()) is None
        assert _to_profile_certification(_LLMCertification(name="   ")) is None


class TestLlmSchemaIncludesCertifications:
    def test_schema_has_certifications_property(self):
        from app.services.profile_parser import _LLM_SCHEMA

        # The structured-output schema must surface certifications so
        # Anthropic returns them — without this entry the model
        # would have no slot to put credentials into and they'd
        # silently land in achievements again.
        defs = _LLM_SCHEMA.get("$defs", {})
        # The `_LLMCertification` def is registered under its class
        # name; Pydantic generates a `properties.certifications`
        # entry that `$ref`s into it.
        assert "_LLMCertification" in defs, "certifications $def missing from LLM schema"
        cert_def = defs["_LLMCertification"]
        assert set(cert_def["properties"].keys()) >= {
            "name",
            "issuer",
            "date",
            "credential_id",
        }
        # additionalProperties:false (Anthropic strict-output rule).
        assert cert_def.get("additionalProperties") is False

        top = _LLM_SCHEMA.get("properties", {})
        assert "certifications" in top, "top-level certifications field missing"


# ─── Prompt guidance ────────────────────────────────────────────────────────


class TestPromptSeparatesCertsFromAchievements:
    """Pin the prompt's misclassification guard so a future edit
    can't quietly remove it."""

    def test_prompt_names_the_distinction(self):
        p = parser_module._SYSTEM_PROMPT.lower()
        # The prompt must explicitly call out the cert/achievement
        # split with example credentials.
        assert "certifications" in p
        assert "achievements" in p
        # Concrete cert examples — these are the most-mis-classified
        # in practice, so the prompt names them outright.
        assert "aws" in p
        assert "pmp" in p
        # Awards-vs-credentials trap is called out.
        assert "dean" in p or "award" in p

    def test_prompt_requires_every_experience_entry(self):
        p = parser_module._SYSTEM_PROMPT.lower()
        # Wording for completeness must mention "every" or
        # "no experience omitted" rule.
        assert "every" in p
        assert "experience" in p
        # The hard-rule phrase about a job-per-entry must survive.
        assert "one job" in p or "own object" in p

    def test_prompt_warns_against_swapped_pairing(self):
        p = parser_module._SYSTEM_PROMPT.lower()
        # The pairing failure mode (title from job A + company from
        # job B) must be explicitly forbidden.
        assert "pairing" in p
        assert "never pair the title" in p


# ─── End-to-end: LLM-returned certs survive the merge ───────────────────────


class _CapturingClient:
    def __init__(self, payload: dict) -> None:
        self.calls: list[dict] = []
        self.payload = payload
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        import json

        text_block = SimpleNamespace(type="text", text=json.dumps(self.payload))
        return SimpleNamespace(content=[text_block])


def test_worker_round_trips_certifications_through_parse(factories, monkeypatch):
    """End-to-end: a mocked Claude returns a certifications array; the
    worker writes a success row with certifications populated AND
    achievements NOT populated for the same items."""
    payload = {
        "name": "Jordan Singh",
        "experience": [],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [
            {"title": "Dean's List, Fall 2022", "description": "", "date": "2022"},
        ],
        "certifications": [
            {
                "name": "AWS Certified Solutions Architect – Associate",
                "issuer": "Amazon Web Services",
                "date": "2024-03",
                "credential_id": "ABC-12345",
            },
            {
                "name": "Project Management Professional (PMP)",
                "issuer": "PMI",
                "date": "2023",
                "credential_id": None,
            },
        ],
        "section_order": ["experience", "achievements", "certifications"],
    }
    mock = _CapturingClient(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda t, a: t(*a))

    _seed_run(factories, "with-certs")
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        ANTHROPIC_API_KEY="sk-test",
    )
    parser_module._execute_parse_run("with-certs", "doesn't matter — LLM mocked", settings)

    run = _row(factories, "with-certs")
    assert run.status == PARSE_STATUS_SUCCESS
    profile = run.profile
    # Both certifications survived intact.
    assert [c["name"] for c in profile["certifications"]] == [
        "AWS Certified Solutions Architect – Associate",
        "Project Management Professional (PMP)",
    ]
    assert profile["certifications"][0]["issuer"] == "Amazon Web Services"
    assert profile["certifications"][0]["credential_id"] == "ABC-12345"
    # PMP's empty credential_id round-trips as None.
    assert profile["certifications"][1]["credential_id"] is None

    # The achievement stays an achievement — NOT shoved into
    # certifications. Pinning the inverse keeps the misclassification
    # fix honest.
    assert [a["title"] for a in profile["achievements"]] == ["Dean's List, Fall 2022"]

    # Section order from the resume rides through.
    assert profile["section_order"] == ["experience", "achievements", "certifications"]


# ─── DOCX renderer ──────────────────────────────────────────────────────────


def test_docx_renders_certifications_section_with_issuer_and_date():
    """The tailored DOCX must include a `Certifications` section
    when the resume has any — with issuer + date on the same line as
    the credential name, and a credential-ID sub-line when present.
    """
    from app.services.docx_export import render_docx
    from app.services.tailor import TailoredCertification, TailoredResume

    resume = TailoredResume(
        summary="Backend engineer.",
        skills=["Python"],
        experience=[],
        education=["B.S. CS, MIT (2018)"],
        certifications=[
            TailoredCertification(
                name="AWS Certified Solutions Architect – Associate",
                issuer="Amazon Web Services",
                date="2024-03",
                credential_id="ABC-12345",
            )
        ],
        section_order=["summary", "skills", "education", "certifications"],
        ats_notes="…",
    )
    candidate = {
        "name": "Jordan Singh",
        "headline": None,
        "email": "jordan@example.com",
        "phone": None,
        "location": None,
        "summary": "",
        "experience": [],
        "education": [],
    }
    blob = render_docx(resume, candidate=candidate)
    # Round-trip through python-docx to inspect the rendered text.
    from io import BytesIO

    from docx import Document

    doc = Document(BytesIO(blob))
    body_text = "\n".join(p.text for p in doc.paragraphs)
    assert "CERTIFICATIONS" in body_text
    assert "AWS Certified Solutions Architect – Associate" in body_text
    assert "Amazon Web Services" in body_text
    assert "2024-03" in body_text
    assert "Credential ID: ABC-12345" in body_text


# ─── Task B: session cookie carries no `Domain` attribute ───────────────────


def test_session_cookie_has_no_domain_attribute():
    """Task B failure mode: a `Domain=...onrender.com` would make the
    cookie third-party from the Vercel frontend's perspective and
    break sign-in in Safari + incognito. Starlette's
    `SessionMiddleware` accepts a `domain` kwarg; the contract here
    is that we leave it UNSET (the default `None`) so the browser
    binds the cookie to whatever host actually called the URL —
    which, via the Next.js rewrite proxy, is the FRONTEND origin.
    """
    from starlette.middleware.sessions import SessionMiddleware

    from app.main import create_app

    app = create_app()
    session_mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    kwargs = session_mw.kwargs
    # Either the kwarg is missing (== default None) OR it's explicitly
    # None. Anything else (a string Domain) is a misconfiguration.
    assert kwargs.get("domain") in (None,), (
        f"session cookie has a Domain attribute: {kwargs.get('domain')!r}. "
        "Leave it unset so the browser binds the cookie to the host that "
        "called the URL (the Vercel frontend, via the proxy)."
    )
