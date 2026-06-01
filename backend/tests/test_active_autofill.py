"""Active autofill run pointer (Chrome extension): set on web, read on extension.

Stored in candidate.profile JSON (no migration). Setting it records a pointer
to a completed tailor run; the extension /me surfaces it so the popup/worker
fill with that resume by default.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models.candidate import Candidate
from app.models.extension_session import ExtensionSession
from app.models.tailor_run import TailorRun
from app.models.user import User
from app.services import active_autofill


def hash_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def _park_done_run(Session, run_id: str, user_id: int = 1) -> None:
    with Session() as s:
        s.add(
            TailorRun(
                run_id=run_id,
                user_id=user_id,
                status="done",
                result_json={"contact": {"name": "T", "headline": "Engineer"}},
            )
        )
        s.commit()


@pytest.fixture
def api(tmp_path):
    from app import config as config_module
    from app.api.auth import get_current_user
    from app.config import get_settings
    from app.database import get_db
    from app.main import app

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'af.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        s.add(User(id=1, google_subject_id="g", email="t@e.com", name="T"))
        # Extension bearer-token session for the same user.
        s.add(
            ExtensionSession(
                id="sess-1", user_id=1, token_hash=hash_token("ext-tok"), device_name="dev"
            )
        )
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


def test_set_active_run_persists_and_extension_me_reads_it(api):
    client, Session = api
    _park_done_run(Session, "run-A")

    # Web app sets it (cookie/get_current_user auth).
    r = client.post("/api/ats/active-autofill-run", json={"run_id": "run-A"})
    assert r.status_code == 204

    # Persisted in the candidate profile JSON (no dedicated column).
    with Session() as s:
        cand = s.query(Candidate).filter_by(user_id=1).one()
        assert cand.profile["active_autofill_run_id"] == "run-A"

    # Extension /me (bearer auth) surfaces it.
    me = client.get("/api/extension/me", headers={"Authorization": "Bearer ext-tok"})
    assert me.status_code == 200
    assert me.json()["active_autofill_run_id"] == "run-A"


def test_set_active_run_rejects_unknown_or_unowned_run(api):
    client, _ = api
    r = client.post("/api/ats/active-autofill-run", json={"run_id": "nope"})
    assert r.status_code == 404


def test_get_active_run_self_heals_when_run_gone(api):
    client, Session = api
    _park_done_run(Session, "run-B")
    client.post("/api/ats/active-autofill-run", json={"run_id": "run-B"})
    # Delete the run → the pointer must resolve to None (extension falls back).
    with Session() as s:
        s.query(TailorRun).filter_by(run_id="run-B").delete()
        s.commit()
    with Session() as s:
        assert active_autofill.get_active_run_id(s, 1) is None
    me = client.get("/api/extension/me", headers={"Authorization": "Bearer ext-tok"})
    assert me.json()["active_autofill_run_id"] is None
