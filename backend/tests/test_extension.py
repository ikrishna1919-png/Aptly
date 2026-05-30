"""Tests for the Chrome-extension backend: token auth, semantic clustering +
learning loop (incl. inverse handling), and the /api/extension endpoints."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models.candidate import DEMO_SLUG, Candidate
from app.models.saved_qa_pair import SavedQAPair
from app.models.tailor_run import TailorRun
from app.models.user import User
from app.services import qa_clustering
from app.services.extension_auth import get_user_from_extension_token, mint_session


def _settings(*, key: bool) -> Settings:
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        ANTHROPIC_API_KEY="sk-test" if key else "",
    )


# Fake Anthropic client whose messages.create returns a fixed JSON text block.
class _FakeClient:
    def __init__(self, payload: dict):
        self._payload = payload

        class _Msgs:
            def create(_self, **_kw):  # noqa: N805
                block = type("B", (), {"type": "text", "text": json.dumps(payload)})()
                return type("M", (), {"content": [block]})()

        self.messages = _Msgs()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        s.add(User(id=1, google_subject_id="g", email="t@e.com", name="Test User"))
        s.commit()
    return Session


# ─── Clustering ──────────────────────────────────────────────────────────────


class TestClustering:
    def test_save_creates_canonical_then_exact_lookup(self, db):
        with db() as s:
            qa_clustering.save(
                s,
                user_id=1,
                question_text="Do you require visa sponsorship?",
                answer="Yes",
                field_type="radio",
                source_ats="greenhouse",
                source_url="u",
                settings=_settings(key=False),
            )
            hit = qa_clustering.lookup(
                s,
                user_id=1,
                question_text="Do you require visa sponsorship?",
                field_type="radio",
                settings=_settings(key=False),
            )
        assert hit["confidence"] == "exact" and hit["answer"] == "Yes"

    def test_fuzzy_lookup(self, db):
        with db() as s:
            qa_clustering.save(
                s,
                user_id=1,
                question_text="What is your expected salary?",
                answer="$150k",
                field_type="text",
                source_ats="greenhouse",
                source_url="u",
                settings=_settings(key=False),
            )
            hit = qa_clustering.lookup(
                s,
                user_id=1,
                question_text="What is your expected salary??",
                field_type="text",
                settings=_settings(key=False),
            )
        assert hit["confidence"] in ("exact", "fuzzy") and hit["answer"] == "$150k"

    def test_no_match_returns_none(self, db):
        with db() as s:
            hit = qa_clustering.lookup(
                s,
                user_id=1,
                question_text="Totally novel question?",
                field_type="text",
                settings=_settings(key=False),
            )
        assert hit["confidence"] == "none" and hit["answer"] is None

    def test_semantic_match_learns_variant(self, db):
        with db() as s:
            qa_clustering.save(
                s,
                user_id=1,
                question_text="Do you require visa sponsorship?",
                answer="Yes",
                field_type="radio",
                source_ats="greenhouse",
                source_url="u",
                settings=_settings(key=False),
            )
            client = _FakeClient({"match_index": 0, "inverse": False})
            hit = qa_clustering.lookup(
                s,
                user_id=1,
                question_text="Will you need work authorization support?",
                field_type="radio",
                settings=_settings(key=True),
                client=client,
            )
            assert hit["confidence"] == "semantic" and hit["answer"] == "Yes"
            # The new phrasing is now learned → next lookup is exact.
            again = qa_clustering.lookup(
                s,
                user_id=1,
                question_text="Will you need work authorization support?",
                field_type="radio",
                settings=_settings(key=False),
            )
        assert again["confidence"] == "exact"

    def test_inverse_boolean_is_flipped(self, db):
        with db() as s:
            qa_clustering.save(
                s,
                user_id=1,
                question_text="Do you require visa sponsorship?",
                answer="Yes",
                field_type="radio",
                source_ats="greenhouse",
                source_url="u",
                settings=_settings(key=False),
            )
            client = _FakeClient({"match_index": 0, "inverse": True})
            hit = qa_clustering.lookup(
                s,
                user_id=1,
                question_text="Are you authorized to work without sponsorship?",
                field_type="radio",
                settings=_settings(key=True),
                client=client,
            )
        assert hit["is_inverse"] is True and hit["answer"] == "No"

    def test_inverse_freetext_not_inverted(self, db):
        with db() as s:
            qa_clustering.save(
                s,
                user_id=1,
                question_text="Why do you want this job?",
                answer="Long prose answer.",
                field_type="text",
                source_ats="greenhouse",
                source_url="u",
                settings=_settings(key=False),
            )
            client = _FakeClient({"match_index": 0, "inverse": True})
            hit = qa_clustering.lookup(
                s,
                user_id=1,
                question_text="Why would you NOT want this job?",
                field_type="text",
                settings=_settings(key=True),
                client=client,
            )
        # Can't invert prose → no usable answer.
        assert hit["confidence"] == "none"

    def test_save_clusters_onto_existing(self, db):
        with db() as s:
            qa_clustering.save(
                s,
                user_id=1,
                question_text="What is your expected salary?",
                answer="$150k",
                field_type="text",
                source_ats="greenhouse",
                source_url="u",
                settings=_settings(key=False),
            )
            qa_clustering.save(
                s,
                user_id=1,
                question_text="What is your expected salary?",
                answer="$160k",
                field_type="text",
                source_ats="lever",
                source_url="u2",
                settings=_settings(key=False),
            )
            rows = s.execute(select(SavedQAPair).where(SavedQAPair.user_id == 1)).scalars().all()
        assert len(rows) == 1 and rows[0].answer == "$160k"  # latest intent wins


# ─── Token auth ──────────────────────────────────────────────────────────────


class TestTokenAuth:
    def test_mint_and_resolve(self, db):
        with db() as s:
            user = s.get(User, 1)
            raw, sid = mint_session(s, user, "Chrome on Mac")
            assert raw and sid
            resolved = get_user_from_extension_token(authorization=f"Bearer {raw}", db=s)
            assert resolved.id == 1

    def test_invalid_token_401(self, db):
        from fastapi import HTTPException

        with db() as s, pytest.raises(HTTPException) as exc:
            get_user_from_extension_token(authorization="Bearer nope", db=s)
        assert exc.value.status_code == 401

    def test_revoked_token_401(self, db):
        from datetime import UTC, datetime

        from fastapi import HTTPException

        from app.models.extension_session import ExtensionSession

        with db() as s:
            raw, sid = mint_session(s, s.get(User, 1), "dev")
            row = s.get(ExtensionSession, sid)
            row.revoked_at = datetime.now(UTC)
            s.commit()
            with pytest.raises(HTTPException) as exc:
                get_user_from_extension_token(authorization=f"Bearer {raw}", db=s)
        assert exc.value.status_code == 401


# ─── Endpoints ───────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path):
    from app import config as config_module
    from app.api.auth import get_current_user
    from app.config import get_settings
    from app.database import get_db
    from app.main import app

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path/'ext.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        s.add(User(id=1, google_subject_id="g", email="t@e.com", name="Test User"))
        s.add(
            Candidate(
                slug=DEMO_SLUG,
                user_id=None,
                profile={
                    "name": "Jane Doe",
                    "email": "jane@e.com",
                    "phone": "555-0100",
                    "links": {"linkedin": "https://lnkd.in/jane"},
                    "experience": [{"title": "Engineer", "company": "Acme"}],
                },
            )
        )
        s.add(
            TailorRun(
                run_id="run-1",
                user_id=1,
                status="done",
                result_json={
                    "contact": {"name": "Jane Doe", "headline": "Engineer"},
                    "summary": "x",
                    "skills": [],
                    "experience": [],
                    "education": [],
                    "projects": [],
                    "certifications": [],
                    "ats": {"matched_keywords": [], "missing_keywords": [], "score_estimate": 0},
                    "meta": {"mode": "visual", "pages_estimate": 1},
                },
            )
        )
        s.commit()
    user = type("U", (), {"id": 1, "email": "t@e.com", "name": "Test User"})()

    def override_db():
        with Session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: _settings(key=False)
    app.dependency_overrides[get_current_user] = lambda: user
    config_module.get_settings.cache_clear()
    # Mint a real bearer token against the test DB.
    with Session() as s:
        raw, _ = mint_session(s, s.get(User, 1), "test")
    try:
        yield TestClient(app), Session, raw
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def _bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def test_session_create_and_me(api):
    client, _Session, token = api
    # Cookie-authed mint.
    created = client.post("/api/extension/sessions/create", json={"device_name": "Chrome"})
    assert created.status_code == 200 and created.json()["token"]
    # Bearer-authed me.
    me = client.get("/api/extension/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["email"] == "t@e.com" and me.json()["has_active_tailor_run"] is True


def test_me_requires_bearer(api):
    client, _, _ = api
    assert client.get("/api/extension/me").status_code == 401


def test_profile_and_tailor_runs(api):
    client, _, token = api
    prof = client.get("/api/extension/profile", headers=_bearer(token)).json()
    assert prof["name"] == "Jane Doe" and prof["linkedin"] == "https://lnkd.in/jane"
    runs = client.get("/api/extension/tailor-runs", headers=_bearer(token)).json()
    assert len(runs) == 1 and runs[0]["id"] == "run-1"
    resume = client.get("/api/extension/tailor-runs/run-1/resume", headers=_bearer(token)).json()
    assert resume["contact"]["name"] == "Jane Doe"
    dl = client.get("/api/extension/tailor-runs/run-1/download", headers=_bearer(token))
    assert dl.status_code == 200 and dl.content[:2] == b"PK"


def test_qa_lookup_save_list_patch_delete(api):
    client, _, token = api
    # Novel → none.
    miss = client.post(
        "/api/extension/qa/lookup",
        headers=_bearer(token),
        json={"question_text": "Preferred start date?", "field_type": "text"},
    ).json()
    assert miss["confidence"] == "none"
    # Save (bearer).
    saved = client.post(
        "/api/extension/qa/save",
        headers=_bearer(token),
        json={
            "question_text": "Preferred start date?",
            "answer": "Two weeks",
            "field_type": "text",
        },
    )
    assert saved.status_code == 200
    qa_id = saved.json()["id"]
    # Now exact hit.
    hit = client.post(
        "/api/extension/qa/lookup",
        headers=_bearer(token),
        json={"question_text": "Preferred start date?", "field_type": "text"},
    ).json()
    assert hit["confidence"] == "exact" and hit["answer"] == "Two weeks"
    # List (cookie).
    lst = client.get("/api/extension/qa/list").json()
    assert len(lst) == 1 and lst[0]["answer"] == "Two weeks"
    # Patch (cookie).
    client.patch(f"/api/extension/qa/{qa_id}", json={"answer": "One month"})
    assert client.get("/api/extension/qa/list").json()[0]["answer"] == "One month"
    # Delete (cookie).
    assert client.delete(f"/api/extension/qa/{qa_id}").status_code == 204
    assert client.get("/api/extension/qa/list").json() == []


def test_revoke_then_bearer_fails(api):
    client, Session, token = api
    sessions = client.get("/api/extension/sessions").json()
    # Revoke the token we minted (find its session id).
    import hashlib

    from app.models.extension_session import ExtensionSession

    with Session() as s:
        row = s.execute(
            select(ExtensionSession).where(
                ExtensionSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
            )
        ).scalar_one()
        sid = row.id
    assert any(x["id"] == sid for x in sessions)
    rev = client.post("/api/extension/sessions/revoke", json={"session_id": sid})
    assert rev.status_code == 200
    # Bearer now rejected.
    assert client.get("/api/extension/me", headers=_bearer(token)).status_code == 401
