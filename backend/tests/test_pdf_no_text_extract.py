"""Regression pin: pdfplumber must NEVER be called when parsing a PDF.

The failure mode this guards against: pdfplumber extracts PDF text
with word-spacing dropped — "Azure DevOps" comes out as
"AzureDevOps", "Data Warehousing" as "DataWarehousing". A previous
revision of `parse_resume_pdf` called pdfplumber as a regex contact
pass before sending the PDF to Claude. The PDF was sent correctly
as a document block, but the LLM's structural output STILL had
concatenated words because the model's prompt context was
contaminated, OR — more importantly — because operators couldn't
tell whether pdfplumber's bad text was reaching the LLM at all.

Resolution: remove the pdfplumber call from the PDF path entirely.
Contact fields come from the LLM's own reading of the PDF. The
text-paste path still uses regex (pasted text is already correctly
spaced); only the PDF path changes.

These tests:
  * Pin that `parse_resume_pdf` does not import / invoke any text
    extractor.
  * Pin that the LLM call carries the new `email`, `phone`,
    `location`, `linkedin_url`, `github_url` top-level properties
    in the schema.
  * Pin that LLM-returned contact fields land on the Profile when
    the PDF path runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models.parse_run import PARSE_STATUS_RUNNING, PARSE_STATUS_SUCCESS, ParseRun
from app.services import profile_parser as parser_module


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


class _MockClient:
    def __init__(self, payload: dict) -> None:
        self.calls: list[dict] = []
        self.payload = payload
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        import json

        self.calls.append(kwargs)
        text_block = SimpleNamespace(type="text", text=json.dumps(self.payload))
        return SimpleNamespace(content=[text_block])


def _seed_run(Session, run_id: str) -> None:
    with Session() as s:
        s.add(ParseRun(run_id=run_id, status=PARSE_STATUS_RUNNING, user_id=None))
        s.commit()


def _row(Session, run_id: str) -> ParseRun:
    with Session() as s:
        return s.execute(select(ParseRun).where(ParseRun.run_id == run_id)).scalar_one()


# ─── pdfplumber MUST NOT be in the PDF path ─────────────────────────────────


def test_parse_resume_pdf_does_not_call_pdfplumber(monkeypatch):
    """The hard contract: when `parse_resume_pdf` runs, the PDF text
    extractor in `app.services.resume_extractor` is NEVER called.
    Send the document to Claude as a base64 document block; don't
    pre-extract text. Spying on `_extract_pdf` and asserting zero
    calls is the most direct way to pin this — if a future
    refactor sneaks pdfplumber back into the path, this test
    breaks before the change ships."""
    import app.services.resume_extractor as extractor

    spy = MagicMock(return_value="should not be used")
    monkeypatch.setattr(extractor, "_extract_pdf", spy)

    # Mock the LLM so we don't make a real API call.
    payload = {
        "name": "Test",
        "email": "test@example.com",
        "phone": None,
        "location": None,
        "linkedin_url": None,
        "github_url": None,
        "experience": [],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [],
        "certifications": [],
        "section_order": [],
    }
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _MockClient(payload))

    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        ANTHROPIC_API_KEY="sk-test",
    )
    parser_module.parse_resume_pdf(b"%PDF-1.4\nfake bytes", settings=settings, run_id="nopdf")

    # `_extract_pdf` must not have been called even once.
    assert spy.call_count == 0, (
        f"pdfplumber was called {spy.call_count} time(s) on the PDF path — "
        "this re-introduces the space-stripping bug ('AzureDevOps' instead "
        "of 'Azure DevOps'). Remove the text-extract step from "
        "parse_resume_pdf."
    )


def test_parse_resume_pdf_returns_empty_when_no_api_key(monkeypatch):
    """No `ANTHROPIC_API_KEY` → no fallback to pdfplumber text.
    Returns an empty profile (the worker writes `status=failed`
    with a clear message) rather than serving the user
    space-stripped pdfplumber output disguised as a parsed
    resume."""
    import app.services.resume_extractor as extractor

    spy = MagicMock()
    monkeypatch.setattr(extractor, "_extract_pdf", spy)

    settings = Settings(DATABASE_URL="x", ADMIN_TOKEN="t")  # no API key
    out = parser_module.parse_resume_pdf(b"%PDF-1.4\nfake", settings=settings, run_id="nokey")
    assert out.name == ""
    assert out.experience == []
    # pdfplumber NOT used as a fallback.
    assert spy.call_count == 0


# ─── LLM schema gains contact fields for the PDF path ──────────────────────


def test_llm_schema_has_top_level_contact_fields():
    """The schema must surface contact fields at the top level —
    the PDF path uses these directly (no regex contact pass)."""
    from app.services.profile_parser import _LLM_SCHEMA

    top = _LLM_SCHEMA.get("properties", {})
    for field in ("email", "phone", "location", "linkedin_url", "github_url"):
        assert field in top, f"top-level {field!r} missing from LLM schema"


# ─── Document content block + prompt content ───────────────────────────────


def test_pdf_path_sends_document_block_to_anthropic(monkeypatch):
    """End-to-end pin: the captured Anthropic SDK call has exactly
    ONE `document` block carrying the base64-encoded PDF, AND a
    follow-up text instruction. No text-only payload of the PDF
    bytes — that's the failure mode this code path exists to
    prevent."""
    payload = {
        "name": "Jordan",
        "email": "jordan@example.com",
        "phone": None,
        "location": None,
        "linkedin_url": None,
        "github_url": None,
        "experience": [],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [],
        "certifications": [],
        "section_order": [],
    }
    mock = _MockClient(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)

    settings = Settings(DATABASE_URL="x", ADMIN_TOKEN="t", ANTHROPIC_API_KEY="sk-test")
    parser_module.parse_resume_pdf(b"%PDF-1.4\nbytes here", settings=settings, run_id="doc")

    assert len(mock.calls) == 1
    content = mock.calls[0]["messages"][0]["content"]
    document_blocks = [b for b in content if b.get("type") == "document"]
    assert len(document_blocks) == 1
    src = document_blocks[0]["source"]
    assert src["type"] == "base64"
    assert src["media_type"] == "application/pdf"

    import base64

    assert base64.b64decode(src["data"]) == b"%PDF-1.4\nbytes here"

    # The instruction text must enforce the failure-mode rules.
    text_blocks = [b for b in content if b.get("type") == "text"]
    instruction = "\n".join(b["text"] for b in text_blocks)
    assert "Azure DevOps" in instruction
    assert "five jobs" in instruction.lower() or "every job" in instruction.lower()
    assert "linkedin_url" in instruction or "phone" in instruction


def test_pdf_path_uses_llm_contact_fields(monkeypatch):
    """Confirm the new contact fields on `_LLMStructuralExtract` flow
    through into the final Profile when the PDF path runs (no
    regex contact pass to override them)."""
    payload = {
        "name": "Dana Sponsor",
        "email": "dana@example.com",
        "phone": "+1 415 555-0100",
        "location": "San Francisco, CA",
        "linkedin_url": "https://linkedin.com/in/dana-sponsor",
        "github_url": "https://github.com/danasponsor",
        "experience": [],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [],
        "certifications": [],
        "section_order": [],
    }
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _MockClient(payload))

    settings = Settings(DATABASE_URL="x", ADMIN_TOKEN="t", ANTHROPIC_API_KEY="sk-test")
    profile = parser_module.parse_resume_pdf(b"%PDF-1.4\nfake", settings=settings, run_id="contact")
    assert profile.email == "dana@example.com"
    assert profile.phone == "+1 415 555-0100"
    assert profile.location == "San Francisco, CA"
    assert profile.links.linkedin == "https://linkedin.com/in/dana-sponsor"
    assert profile.links.github == "https://github.com/danasponsor"


# ─── End-to-end through the worker — multi-job PDF ──────────────────────────


def test_pdf_worker_round_trips_five_distinct_jobs(factories, monkeypatch):
    """The reported failure: a five-job resume collapsed into one
    experience entry. With the PDF going directly to Claude, the
    LLM returns five entries and they all survive the worker's
    merge into the final Profile. Each entry's company/title pair
    stays paired."""
    payload = {
        "name": "Multi Job",
        "email": "multi@example.com",
        "phone": None,
        "location": None,
        "linkedin_url": None,
        "github_url": None,
        "experience": [
            {
                "company": "SMBC",
                "title": "Senior Data Engineer",
                "location": "New York, NY",
                "start_date": "2023",
                "end_date": "Present",
                "description_bullets": ["Led the data-warehouse migration."],
            },
            {
                "company": "SMBC",
                "title": "Data Engineer",
                "location": "New York, NY",
                "start_date": "2021",
                "end_date": "2023",
                "description_bullets": ["Built the streaming pipeline."],
            },
            {
                "company": "Capgemini",
                "title": "Consultant",
                "location": "Remote",
                "start_date": "2019",
                "end_date": "2021",
                "description_bullets": ["Delivered cloud migrations."],
            },
            {
                "company": "Capgemini",
                "title": "Associate Consultant",
                "location": "Remote",
                "start_date": "2018",
                "end_date": "2019",
                "description_bullets": ["Implemented ETL workflows."],
            },
            {
                "company": "Soulpage",
                "title": "Software Engineer",
                "location": "Hyderabad",
                "start_date": "2016",
                "end_date": "2018",
                "description_bullets": ["Shipped ML pipelines."],
            },
        ],
        "education": [],
        "skills": ["Azure DevOps", "Data Warehousing"],
        "projects": [
            {
                "name": "Open-source contribution",
                "description": "Contributor to pandas",
                "technologies": ["Python"],
                "link": None,
                "start_date": None,
                "end_date": None,
            }
        ],
        "achievements": [{"title": "Spot Award 2022", "description": "", "date": "2022"}],
        "certifications": [
            {
                "name": "AWS Certified Solutions Architect – Associate",
                "issuer": "Amazon Web Services",
                "date": "2023",
                "credential_id": None,
            }
        ],
        "section_order": [
            "experience",
            "education",
            "skills",
            "projects",
            "achievements",
            "certifications",
        ],
    }
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _MockClient(payload))
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda t, a: t(*a))

    _seed_run(factories, "five-jobs")
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        ANTHROPIC_API_KEY="sk-test",
    )
    parser_module._execute_parse_run_pdf("five-jobs", b"%PDF-1.4\nfake", settings)

    run = _row(factories, "five-jobs")
    assert run.status == PARSE_STATUS_SUCCESS

    # All five experiences arrived intact.
    profile = run.profile
    assert len(profile["experience"]) == 5
    assert [e["company"] for e in profile["experience"]] == [
        "SMBC",
        "SMBC",
        "Capgemini",
        "Capgemini",
        "Soulpage",
    ]
    assert [e["title"] for e in profile["experience"]] == [
        "Senior Data Engineer",
        "Data Engineer",
        "Consultant",
        "Associate Consultant",
        "Software Engineer",
    ]
    # Pair check: every entry's bullets stayed with their own job.
    assert profile["experience"][0]["bullets"] == ["Led the data-warehouse migration."]
    assert profile["experience"][4]["bullets"] == ["Shipped ML pipelines."]

    # Skills, projects, certifications, achievements all populated.
    assert profile["skills"] == ["Azure DevOps", "Data Warehousing"]
    assert len(profile["projects"]) == 1
    assert profile["projects"][0]["name"] == "Open-source contribution"
    assert len(profile["certifications"]) == 1
    assert profile["certifications"][0]["name"].startswith("AWS Certified")
    assert len(profile["achievements"]) == 1
    assert profile["achievements"][0]["title"] == "Spot Award 2022"

    # Contact fields surfaced from the LLM (no pdfplumber regex
    # pass on this path).
    assert profile["email"] == "multi@example.com"


def test_pdf_path_does_not_import_resume_extractor_for_pdf_helpers(monkeypatch):
    """Belt-and-braces: even if a future refactor adds a different
    text-extract call, the parse_resume_pdf body itself should not
    reference any `_extract_pdf`-style helper on a successful
    happy-path call. We check via a counted-spy: zero calls."""
    import app.services.resume_extractor as extractor

    counts = {"extract_pdf": 0, "extract_text": 0}

    def _count_pdf(*_a, **_k):
        counts["extract_pdf"] += 1
        return ""

    def _count_text(*_a, **_k):
        counts["extract_text"] += 1
        return ""

    monkeypatch.setattr(extractor, "_extract_pdf", _count_pdf)
    monkeypatch.setattr(extractor, "extract_text", _count_text)

    payload = {
        "name": "X",
        "email": None,
        "phone": None,
        "location": None,
        "linkedin_url": None,
        "github_url": None,
        "experience": [],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [],
        "certifications": [],
        "section_order": [],
    }
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _MockClient(payload))
    settings = Settings(DATABASE_URL="x", ADMIN_TOKEN="t", ANTHROPIC_API_KEY="sk-test")
    parser_module.parse_resume_pdf(b"%PDF-1.4\nfake", settings=settings, run_id="x")
    assert counts == {"extract_pdf": 0, "extract_text": 0}
