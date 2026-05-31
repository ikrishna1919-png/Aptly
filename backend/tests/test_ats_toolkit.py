"""Tests for the ATS Toolkit PR: keyword-injection JSON retry + active-resume."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models.user import User
from app.services import ats


def _settings_key() -> Settings:
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:", ADMIN_TOKEN="t", ANTHROPIC_API_KEY="sk-x"
    )


# ─── FIX 1: keyword-injection JSON retry + graceful failure ──────────────────


class _Client:
    """Fake Anthropic client returning a scripted sequence of text replies."""

    def __init__(self, replies: list[str]):
        self._replies = replies
        self.calls = 0
        self.messages = self

    def create(self, **_kw):
        text = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        block = type("B", (), {"type": "text", "text": text})()
        return type("M", (), {"content": [block]})()


def test_keyword_retry_recovers_from_bad_json():
    ats.keyword_parse_failures.update({"first": 0, "retry": 0})
    # Trailing comma → invalid JSON on the first reply.
    bad = '{"edits":[{"original_text":"Python","replacement_text":"Python, Kafka",}]}'
    good = (
        '{"edits":[{"original_text":"Python","replacement_text":"Python and Kafka","reason":"jd"}]}'
    )
    client = _Client([bad, good])
    out = ats.compute_keyword_edits(
        "Python dev", "need Kafka", settings=_settings_key(), client=client
    )
    assert out == [
        {"original_text": "Python", "replacement_text": "Python and Kafka", "reason": "jd"}
    ]
    assert client.calls == 2  # retried once
    assert ats.keyword_parse_failures["first"] == 1


def test_keyword_double_failure_raises_clean_error():
    ats.keyword_parse_failures.update({"first": 0, "retry": 0})
    client = _Client(["not json", "still not json"])
    with pytest.raises(ats.KeywordInjectionError) as exc:
        ats.compute_keyword_edits("x", "y", settings=_settings_key(), client=client)
    assert "Couldn't generate keyword edits" in str(exc.value)
    assert ats.keyword_parse_failures["retry"] == 1


def test_keyword_strips_fences():
    good = '```json\n{"edits":[{"original_text":"a","replacement_text":"a b"}]}\n```'
    client = _Client([good])
    out = ats.compute_keyword_edits("a", "b", settings=_settings_key(), client=client)
    assert out and out[0]["replacement_text"] == "a b"
    assert client.calls == 1  # no retry needed


# ─── FIX 4: active resume endpoints ──────────────────────────────────────────

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def api(tmp_path):
    from app import config as config_module
    from app.api.auth import get_current_user
    from app.config import get_settings
    from app.database import get_db
    from app.main import app

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path/'tk.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        s.add(User(id=1, google_subject_id="g", email="t@e.com", name="T"))
        s.commit()
    user = type("U", (), {"id": 1, "email": "t@e.com", "name": "T"})()

    def override_db():
        with Session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(
        DATABASE_URL="x", ADMIN_TOKEN="t", ANTHROPIC_API_KEY=""
    )
    app.dependency_overrides[get_current_user] = lambda: user
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def test_active_resume_lifecycle(api):
    client, _Session = api
    assert client.get("/api/profile/active-resume").json() == {"present": False}
    up = client.post(
        "/api/profile/active-resume",
        files={"file": ("me.docx", b"PKfake-docx-bytes", _DOCX)},
    )
    assert up.status_code == 200 and up.json()["present"] is True
    assert up.json()["content_type"] == _DOCX
    meta = client.get("/api/profile/active-resume").json()
    assert meta["present"] and meta["filename"] == "me.docx" and "blob" not in meta
    dl = client.get("/api/profile/active-resume/download")
    assert dl.status_code == 200 and dl.content == b"PKfake-docx-bytes"
    assert _DOCX in dl.headers["content-type"]
    # DOCX-only: a PDF is now rejected (the saved resume drives in-place tailoring).
    pdf = client.post(
        "/api/profile/active-resume",
        files={"file": ("me.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert pdf.status_code == 415
    # The previously-saved DOCX is untouched by the rejected PDF.
    assert client.get("/api/profile/active-resume").json()["content_type"] == _DOCX
    # Replace with another DOCX.
    client.post(
        "/api/profile/active-resume",
        files={"file": ("v2.docx", b"PKsecond-docx", _DOCX)},
    )
    assert client.get("/api/profile/active-resume").json()["filename"] == "v2.docx"
    assert client.delete("/api/profile/active-resume").status_code == 204
    assert client.get("/api/profile/active-resume").json() == {"present": False}


def test_active_resume_rejects_bad_type(api):
    client, _ = api
    res = client.post(
        "/api/profile/active-resume", files={"file": ("x.txt", b"hello", "text/plain")}
    )
    assert res.status_code == 415


def test_active_resume_download_404_when_none(api):
    client, _ = api
    assert client.get("/api/profile/active-resume/download").status_code == 404
