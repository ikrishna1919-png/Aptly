"""Tests for the /ats hub additions: JD keyword coverage, cover letters,
default formats, and LinkedIn ZIP import."""

from __future__ import annotations

import csv
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models.candidate import DEMO_SLUG, Candidate
from app.models.user import User
from app.services import default_formats, keyword_coverage, linkedin_import
from app.services.cover_letter import CoverLetterContent, render_cover_docx, render_cover_pdf


def _settings() -> Settings:
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:", ADMIN_TOKEN="t", ANTHROPIC_API_KEY=""
    )


# ─── Keyword coverage ─────────────────────────────────────────────────────────


class TestKeywordCoverage:
    JD = "Backend Engineer: Python, Kafka, AWS, PostgreSQL, Docker, CI/CD. Go a plus."

    def test_extract_known_terms(self):
        terms = keyword_coverage.extract_jd_terms(self.JD)
        for t in ("python", "kafka", "aws", "postgresql", "docker", "ci/cd", "go"):
            assert t in terms

    def test_before_after_lift(self):
        weak = "Software engineer who knows Python."
        strong = "Backend engineer: Python, Kafka, AWS, PostgreSQL, Docker, CI/CD pipelines."
        before = keyword_coverage.score(self.JD, weak).percent
        after = keyword_coverage.score(self.JD, strong).percent
        assert after > before and after >= 70

    def test_single_token_not_substring(self):
        # 'go' must not match inside 'google'
        assert keyword_coverage.score("Go developer role", "I use google cloud").percent == 0

    def test_empty_jd(self):
        assert keyword_coverage.score("", "anything").percent == 0

    def test_matched_and_missing_partition(self):
        cov = keyword_coverage.score(self.JD, "Python and Docker only.")
        assert "python" in cov.matched and "docker" in cov.matched
        assert "kafka" in cov.missing
        assert set(cov.matched).isdisjoint(cov.missing)

    def test_text_from_profile(self):
        text = keyword_coverage.candidate_text_from_profile(
            {
                "summary": "Python dev",
                "skills": ["AWS"],
                "experience": [{"bullets": ["Used Kafka"]}],
            }
        )
        assert "python" in text.lower() and "aws" in text.lower() and "kafka" in text.lower()


# ─── Cover letter render ──────────────────────────────────────────────────────


def test_cover_letter_renders_both():
    letter = CoverLetterContent(
        recipient="Hiring Team, Acme",
        greeting="Dear Hiring Manager,",
        paragraphs=["First paragraph.", "Second paragraph."],
        signature="Jane Doe",
    )
    assert render_cover_docx(letter, "traditional")[:2] == b"PK"
    assert render_cover_pdf(letter, "modern")[:4] == b"%PDF"


# ─── Default formats ──────────────────────────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        s.add(User(id=1, google_subject_id="g", email="t@e.com", name="T"))
        s.add(Candidate(slug=DEMO_SLUG, user_id=None, profile={"name": "Demo"}))
        s.commit()
    return Session


class TestDefaultFormats:
    def test_fallback_when_unset(self, db):
        with db() as s:
            assert default_formats.resolve_default(s, 1, "resume")["format"] == "modern"
            assert default_formats.resolve_default(s, 1, "cover")["format"] == "traditional"

    def test_save_and_resolve(self, db):
        with db() as s:
            default_formats.save_default(s, 1, "resume", {"format": "classic", "custom": None})
            assert default_formats.resolve_default(s, 1, "resume")["format"] == "classic"

    def test_ai_choose_resume_heuristic(self):
        assert (
            default_formats.ai_choose_resume_format({"headline": "Senior Staff Engineer"})["format"]
            == "modern"
        )
        assert (
            default_formats.ai_choose_resume_format({"summary": "PhD researcher in NLP"})["format"]
            == "classic"
        )
        assert (
            default_formats.ai_choose_resume_format({"headline": "Software Engineer"})["format"]
            == "minimal"
        )


# ─── LinkedIn import ──────────────────────────────────────────────────────────


def _linkedin_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        prof = io.StringIO()
        csv.writer(prof).writerows(
            [
                ["First Name", "Last Name", "Headline", "Summary"],
                ["Jane", "Doe", "Engineer", "Backend dev"],
            ]
        )
        zf.writestr("Profile.csv", prof.getvalue())
        pos = io.StringIO()
        csv.writer(pos).writerows(
            [
                ["Company Name", "Title", "Description"],
                ["Acme", "Engineer", "Built Python services"],
            ]
        )
        zf.writestr("Positions.csv", pos.getvalue())
        skl = io.StringIO()
        csv.writer(skl).writerows([["Name"], ["Python"], ["AWS"]])
        zf.writestr("Skills.csv", skl.getvalue())
    return buf.getvalue()


class TestLinkedInImport:
    def test_parse(self):
        p = linkedin_import.parse_linkedin_zip(_linkedin_zip())
        assert p["name"] == "Jane Doe"
        assert p["experience"][0]["company"] == "Acme"
        assert "Python" in p["skills"]

    def test_non_zip_raises(self):
        with pytest.raises(ValueError):
            linkedin_import.parse_linkedin_zip(b"not a zip")

    def test_diff_new_vs_conflict(self):
        imported = {"name": "Jane Doe", "skills": ["Python"]}
        existing = {"name": "Old Name"}
        diff = linkedin_import.diff_against_existing(existing, imported)
        assert "name" in diff["conflict"]  # existing has a name
        assert "skills" in diff["new"]  # existing has none


# ─── Endpoints ────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path):
    from app import config as config_module
    from app.api.auth import get_current_user
    from app.config import get_settings
    from app.database import get_db
    from app.main import app

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path/'hub.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        s.add(User(id=1, google_subject_id="g", email="t@e.com", name="T"))
        s.add(
            Candidate(
                slug=DEMO_SLUG,
                user_id=None,
                profile={
                    "name": "Jane Doe",
                    "summary": "Backend engineer with Python and AWS.",
                    "skills": ["Python", "AWS"],
                    "experience": [
                        {
                            "title": "Engineer",
                            "company": "Acme",
                            "bullets": ["Built Python services on AWS."],
                        }
                    ],
                },
            )
        )
        s.commit()
    user = type("U", (), {"id": 1, "email": "t@e.com", "name": "T"})()

    def override_db():
        with Session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_current_user] = lambda: user
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def test_keyword_coverage_endpoint(api):
    res = api.post("/api/ats/keyword-coverage", json={"jd_text": "Need Python and AWS and Kafka."})
    assert res.status_code == 200
    body = res.json()
    assert "python" in body["matched"] and "kafka" in body["missing"]
    assert 0 <= body["percent"] <= 100


def test_default_format_endpoints(api):
    # Unset → fallback.
    assert api.get("/api/ats/default-format/resume").json()["format"] == "modern"
    # Save.
    saved = api.post("/api/ats/default-format", json={"kind": "resume", "format": "classic"})
    assert saved.status_code == 200 and saved.json()["format"] == "classic"
    # Persists.
    assert api.get("/api/ats/default-format/resume").json()["format"] == "classic"
    # AI choose (heuristic).
    pick = api.post("/api/ats/default-format/ai-choose/cover")
    assert pick.status_code == 200 and pick.json()["format"] in ("traditional", "modern")


def test_cover_letter_generate_demo(api):
    res = api.post(
        "/api/cover-letter/generate",
        json={
            "jd_text": "Backend role at Acme.",
            "company_name": "Acme",
            "questions": {"tone": "warm", "length": "short", "opening": "value"},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "done" and body["demo_mode"] is True
    assert body["content"]["signature"] == "Jane Doe"
    cid = body["id"]
    # Edit then download.
    api.patch(
        f"/api/cover-letter/{cid}", json={"content": {**body["content"], "greeting": "Hello,"}}
    )
    dl = api.get(f"/api/cover-letter/{cid}/download?fmt=docx")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"


def test_linkedin_import_endpoint(api):
    res = api.post(
        "/api/ats/linkedin-import",
        files={"file": ("export.zip", _linkedin_zip(), "application/zip")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["imported"]["name"] == "Jane Doe"
    assert "diff" in body
