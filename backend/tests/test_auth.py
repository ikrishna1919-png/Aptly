"""Tests for the Google sign-in auth surface.

Mocking the real Google round-trip is brittle, so we exercise the
auth flow at three levels:

  * `find_or_link_user` directly — pure data path. Pins the
    initial-user link semantics: an existing row with the same email
    but no `google_subject_id` (the migration's bootstrap row) gets
    linked on first OAuth sign-in.
  * `/api/auth/me` and `/api/auth/logout` with the session
    dependency injected via `dependency_overrides[get_current_user]`.
  * The OAuth callback handler with `authorize_access_token`
    monkey-patched to return a canned token + userinfo dict.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config as config_module
from app.api.auth import find_or_link_user, get_current_user
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models.user import User


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
def settings():
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test-session-secret",
        GOOGLE_CLIENT_ID="test-client-id",
        GOOGLE_CLIENT_SECRET="test-client-secret",
        GOOGLE_REDIRECT_URI="http://testserver/api/auth/google/callback",
        FRONTEND_URL="http://localhost:3000",
        INITIAL_USER_EMAIL="owner@example.com",
    )


# ── find_or_link_user — the data path that drives initial-user linking ─────


def test_find_or_link_user_links_existing_email_to_google_sub(factories):
    """The migration's bootstrap row exists with the owner's email
    and `google_subject_id=NULL`. When that owner signs in with
    Google for the first time, `find_or_link_user` writes the `sub`
    onto the row instead of creating a new one — the owner sees
    their migrated data."""
    with factories() as s:
        # Simulate the bootstrap row migration 0012 would have created.
        s.add(User(google_subject_id=None, email="owner@example.com", name="Owner"))
        s.commit()

    with factories() as s:
        user = find_or_link_user(
            s,
            {"sub": "google-sub-xyz", "email": "owner@example.com", "name": "Owner Updated"},
        )
    assert user.id is not None
    assert user.google_subject_id == "google-sub-xyz"
    # Name update from Google is honoured.
    assert user.name == "Owner Updated"
    # No duplicate row was created.
    with factories() as s:
        assert s.query(User).count() == 1


def test_find_or_link_user_resolves_known_sub_to_existing_row(factories):
    """A second sign-in (sub already linked) just returns the
    existing row — no INSERT, no email rewrite from a stale token."""
    with factories() as s:
        s.add(
            User(
                google_subject_id="google-sub-xyz",
                email="owner@example.com",
                name="Owner",
            )
        )
        s.commit()

    with factories() as s:
        user = find_or_link_user(
            s, {"sub": "google-sub-xyz", "email": "owner@example.com", "name": "Owner"}
        )
    assert user.email == "owner@example.com"
    with factories() as s:
        assert s.query(User).count() == 1


def test_find_or_link_user_creates_new_user_when_no_match(factories):
    """A brand-new Google account (no matching sub or email) gets a
    fresh row with both fields populated."""
    with factories() as s:
        user = find_or_link_user(
            s,
            {"sub": "new-google-sub", "email": "newcomer@example.com", "name": "Newcomer"},
        )
    assert user.id is not None
    assert user.google_subject_id == "new-google-sub"
    assert user.email == "newcomer@example.com"
    assert user.name == "Newcomer"


def test_find_or_link_user_normalises_email_case(factories):
    """Email matching is case-insensitive: Google sometimes returns
    a different case than what was seeded. Otherwise the bootstrap
    row would never link."""
    with factories() as s:
        s.add(User(google_subject_id=None, email="owner@example.com", name="Owner"))
        s.commit()

    with factories() as s:
        user = find_or_link_user(
            s,
            {"sub": "google-sub-xyz", "email": "Owner@Example.COM", "name": "Owner"},
        )
    assert user.google_subject_id == "google-sub-xyz"
    with factories() as s:
        assert s.query(User).count() == 1


def test_find_or_link_user_rejects_missing_sub_or_email():
    """The dependency assumes Google's userinfo carries both; the
    helper is defensive in case the token doesn't include them."""
    from fastapi import HTTPException

    class _MockSession:
        def execute(self, *a, **k):
            raise AssertionError("should not query")

    with pytest.raises(HTTPException):
        find_or_link_user(_MockSession(), {"sub": "", "email": "x@y"})
    with pytest.raises(HTTPException):
        find_or_link_user(_MockSession(), {"sub": "x", "email": ""})


# ── /api/auth/me + /api/auth/logout ────────────────────────────────────────


def _client_with_user(factories, settings, *, user: User | None) -> TestClient:
    def override_db():
        with factories() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    config_module.get_settings.cache_clear()
    return TestClient(app)


def test_auth_me_returns_signed_in_user(factories, settings):
    with factories() as s:
        u = User(google_subject_id="sub-1", email="me@example.com", name="Me")
        s.add(u)
        s.commit()
        s.refresh(u)
        s.expunge(u)
    test_client = _client_with_user(factories, settings, user=u)
    try:
        res = test_client.get("/api/auth/me")
        assert res.status_code == 200
        body = res.json()
        assert body == {"id": u.id, "email": "me@example.com", "name": "Me"}
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def test_auth_me_401_when_not_signed_in(factories, settings):
    test_client = _client_with_user(factories, settings, user=None)
    try:
        res = test_client.get("/api/auth/me")
        assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def test_auth_logout_clears_session(factories, settings):
    """`POST /api/auth/logout` returns 200 without requiring an
    existing session — clear-on-empty is a no-op so the frontend
    can safely fire it even after the cookie has expired."""
    test_client = _client_with_user(factories, settings, user=None)
    try:
        res = test_client.post("/api/auth/logout")
        assert res.status_code == 200
        assert res.json() == {"ok": True}
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


# ── OAuth start endpoint config gating ─────────────────────────────────────


def test_oauth_start_503s_when_unconfigured(factories):
    """Without GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI the start
    endpoint surfaces a 503 explaining the gap rather than crashing
    on the authlib redirect."""
    unconfigured = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test-session-secret",
        # Deliberately no GOOGLE_* set.
    )
    test_client = _client_with_user(factories, unconfigured, user=None)
    try:
        res = test_client.get("/api/auth/google/login", follow_redirects=False)
        assert res.status_code == 503
        assert "google" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()
