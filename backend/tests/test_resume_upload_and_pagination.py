"""Upload endpoint, resume extractor, and the jobs-list page/total_pages
fields added with pagination.

Upload tests use the same `_wire_parse_worker` pattern the rest of the
profile suite uses so the background worker runs inline and we can
assert the terminal `success` / `failed` state in the same request.

The extractor is exercised both directly and via the HTTP endpoint to
keep the surface that the API + the worker actually run through under
test (rather than only the helper in isolation).
"""

from __future__ import annotations

import io
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
from app.models.job import Job
from app.models.user import User
from app.services import profile_parser as parser_module
from app.services.resume_extractor import (
    EmptyExtractionError,
    UnsupportedResumeFile,
    extract_text,
)

# ─── Fixtures ──────────────────────────────────────────────────────────────


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


@pytest.fixture
def upload_client(factories, monkeypatch):
    """A test client wired to drive the upload→parse worker inline
    against the in-memory DB. The Anthropic client is stubbed to raise
    so the LLM branch falls back to the regex extractor (same default
    as `test_profile.py`'s `_wire_parse_worker`)."""
    from app.api.auth import get_current_user

    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda target, args: target(*args))

    def _raise(*_a, **_k):
        raise RuntimeError("test stub: no Anthropic client wired")

    monkeypatch.setattr(parser_module, "_build_client", _raise)

    with factories() as s:
        user = User(google_subject_id="g-up", email="upload@example.com", name="Up Loader")
        s.add(user)
        s.commit()
        s.refresh(user)
        s.expunge(user)

    def override_db():
        with factories() as s:
            yield s

    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:", ADMIN_TOKEN="t")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: user
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


# ─── Extractor unit tests ──────────────────────────────────────────────────


def _make_minimal_docx(text: str) -> bytes:
    """Build a tiny in-memory DOCX containing one paragraph per line of
    `text`. Lets us exercise the DOCX branch without shipping a binary
    fixture in the repo."""
    from docx import Document

    doc = Document()
    for line in text.splitlines() or [text]:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class TestExtractor:
    def test_unsupported_extension_raises(self):
        with pytest.raises(UnsupportedResumeFile, match="Unsupported file type"):
            extract_text("resume.txt", b"hello")

    def test_empty_data_raises_empty(self):
        with pytest.raises(EmptyExtractionError, match="empty"):
            extract_text("resume.pdf", b"")

    def test_docx_roundtrip_extracts_paragraphs(self):
        data = _make_minimal_docx("Jordan Singh\nSoftware Engineer\nNew York, NY")
        text = extract_text("resume.docx", data)
        assert "Jordan Singh" in text
        assert "Software Engineer" in text
        assert "New York" in text

    def test_corrupt_pdf_raises_value_error_not_unhandled(self):
        # Random bytes — pdfplumber's underlying pdfminer will fail
        # with a syntax error. The extractor wraps that in a generic
        # ValueError so the HTTP layer surfaces a clean 400.
        with pytest.raises(ValueError):
            extract_text("garbage.pdf", b"not really a pdf at all")


# ─── HTTP endpoint ─────────────────────────────────────────────────────────


def test_upload_endpoint_runs_parse_and_returns_run_id(upload_client):
    """End-to-end happy path: POST a DOCX, get 202 + run_id, poll
    the status endpoint, get a `success` row with the extracted
    profile."""
    data = _make_minimal_docx(
        "Jordan Singh\nSoftware Engineer\nBrooklyn, NY · jordan@example.com · 555-123-4567\n"
    )
    files = {
        "file": (
            "resume.docx",
            data,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    res = upload_client.post("/api/profile/parse/upload", files=files)
    assert res.status_code == 202
    body = res.json()
    assert "run_id" in body
    assert body["status_url"].startswith("/api/profile/parse/")

    # Worker ran inline — polling should return success immediately.
    poll = upload_client.get(body["status_url"])
    assert poll.status_code == 200
    out = poll.json()
    assert out["status"] == "success"
    assert out["profile"]["name"] == "Jordan Singh"
    assert out["profile"]["email"] == "jordan@example.com"


def test_upload_endpoint_400s_on_unsupported_extension(upload_client):
    files = {"file": ("resume.txt", b"plain text", "text/plain")}
    res = upload_client.post("/api/profile/parse/upload", files=files)
    assert res.status_code == 400
    assert "Unsupported" in res.json()["detail"]


def test_upload_endpoint_422s_on_empty_text_extract(upload_client):
    """An empty DOCX surfaces as 422 with the actionable 'paste your
    text instead' hint — the same path a scanned-image PDF takes
    (DOCX is easier to construct in a test than a deliberately
    text-less PDF)."""
    data = _make_minimal_docx("")  # zero-paragraph doc
    files = {
        "file": (
            "empty.docx",
            data,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    res = upload_client.post("/api/profile/parse/upload", files=files)
    assert res.status_code == 422
    assert "paste" in res.json()["detail"].lower()


def test_upload_endpoint_400s_on_corrupt_pdf(upload_client):
    files = {"file": ("garbage.pdf", b"not really a pdf", "application/pdf")}
    res = upload_client.post("/api/profile/parse/upload", files=files)
    assert res.status_code == 400


def test_upload_endpoint_413s_oversize_file(upload_client, monkeypatch):
    """A file larger than `MAX_UPLOAD_BYTES` is rejected before
    extraction. Lower the cap to keep the test cheap."""
    import app.api.profile as profile_api
    import app.services.resume_extractor as extractor

    monkeypatch.setattr(extractor, "MAX_UPLOAD_BYTES", 100)
    monkeypatch.setattr(profile_api, "MAX_UPLOAD_BYTES", 100)
    big = b"x" * 1024
    files = {"file": ("resume.pdf", big, "application/pdf")}
    res = upload_client.post("/api/profile/parse/upload", files=files)
    assert res.status_code == 413


# ─── Pagination response shape ─────────────────────────────────────────────


@pytest.fixture
def jobs_client(factories):
    """Client wired against an in-memory DB seeded with N jobs so the
    pagination math has something to walk."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:", ADMIN_TOKEN="t")

    def override_db():
        with factories() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()


def _seed_jobs(Session, n: int):
    now = datetime.now(UTC)
    with Session() as s:
        for i in range(n):
            s.add(
                Job(
                    source="greenhouse",
                    external_id=f"job-{i}",
                    company=f"Acme{i}",
                    title=f"Engineer {i}",
                    url="https://example.com",
                    skills=[],
                    source_updated_at=now - timedelta(minutes=i),
                )
            )
        s.commit()


class TestJobsPagination:
    def test_response_carries_page_and_total_pages(self, jobs_client):
        client, Session = jobs_client
        _seed_jobs(Session, 45)
        res = client.get("/api/jobs?limit=10&offset=0").json()
        assert res["total"] == 45
        assert res["limit"] == 10
        assert res["offset"] == 0
        assert res["page"] == 1
        assert res["total_pages"] == 5  # ceil(45 / 10)
        assert len(res["jobs"]) == 10

    def test_offset_advances_page(self, jobs_client):
        client, Session = jobs_client
        _seed_jobs(Session, 45)
        # Page 3 — offset 20 with limit=10 yields the 3rd page.
        res = client.get("/api/jobs?limit=10&offset=20").json()
        assert res["page"] == 3
        assert res["total_pages"] == 5
        assert len(res["jobs"]) == 10
        # Pin different jobs than page 1.
        first_page = client.get("/api/jobs?limit=10&offset=0").json()
        assert {j["id"] for j in first_page["jobs"]} != {j["id"] for j in res["jobs"]}

    def test_empty_result_set_yields_zero_pages(self, jobs_client):
        client, _ = jobs_client
        res = client.get("/api/jobs?limit=10").json()
        assert res["total"] == 0
        assert res["total_pages"] == 0
        assert res["page"] == 1
        assert res["jobs"] == []

    def test_last_page_partial(self, jobs_client):
        client, Session = jobs_client
        _seed_jobs(Session, 23)
        res = client.get("/api/jobs?limit=10&offset=20").json()
        # Final page should have just 3 jobs.
        assert res["total"] == 23
        assert res["total_pages"] == 3
        assert res["page"] == 3
        assert len(res["jobs"]) == 3
