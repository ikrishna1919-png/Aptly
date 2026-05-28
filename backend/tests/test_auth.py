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
        # `profile_saved=False` because we haven't PUT /api/profile —
        # the auto-seeded Candidate row (if any) carries NULL on
        # `profile_saved_at` until the user explicitly saves.
        assert body == {
            "id": u.id,
            "email": "me@example.com",
            "name": "Me",
            "profile_saved": False,
        }
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def test_auth_me_profile_saved_flips_after_first_put(factories, settings):
    """`profile_saved` starts False (seeded Candidate, never explicitly
    saved), then flips to True after the user PUTs their profile.
    This is the signal the frontend uses to gate `/jobs` behind a
    real save — without it, brand-new users would land on the jobs
    feed running tailoring against the demo template."""
    with factories() as s:
        u = User(google_subject_id="sub-2", email="u@example.com", name="U")
        s.add(u)
        s.commit()
        s.refresh(u)
        s.expunge(u)
    test_client = _client_with_user(factories, settings, user=u)
    try:
        # Fresh user — no PUT yet → profile_saved is False.
        body = test_client.get("/api/auth/me").json()
        assert body["profile_saved"] is False

        # A GET on /profile triggers the auto-seed but should NOT
        # flip the flag (seed != explicit save).
        test_client.get("/api/profile")
        assert test_client.get("/api/auth/me").json()["profile_saved"] is False

        # PUT the profile — this is what counts as "saved."
        profile_payload = test_client.get("/api/profile").json()
        profile_payload["name"] = "Real Name"
        put = test_client.put("/api/profile", json=profile_payload)
        assert put.status_code == 200
        assert test_client.get("/api/auth/me").json()["profile_saved"] is True
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


def test_session_middleware_configured_with_cookie_domain(monkeypatch):
    """`create_app` MUST forward `COOKIE_DOMAIN` to SessionMiddleware
    as the `domain` kwarg. Without it, the cookie is host-only on
    `api.aptly.fyi` and the frontend on `aptly.fyi` never receives
    it — sign-in completes silently but the next /me 401s and the
    user is stuck on the sign-in page.

    Inspect the middleware's actual `domain` attribute rather than
    round-tripping a request, so the test pins the wiring directly
    (and doesn't depend on any specific endpoint touching the
    session)."""
    import app.config as cfg  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    with_domain = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test-session-secret",
        GOOGLE_CLIENT_ID="x",
        GOOGLE_CLIENT_SECRET="y",
        GOOGLE_REDIRECT_URI="https://api.aptly.fyi/api/auth/google/callback",
        FRONTEND_URL="https://aptly.fyi",
        COOKIE_DOMAIN=".aptly.fyi",
        ENVIRONMENT="production",
        INITIAL_USER_EMAIL="owner@example.com",
    )
    cfg.get_settings.cache_clear()
    monkeypatch.setattr(cfg, "get_settings", lambda: with_domain)
    # `app.main` imported `get_settings` at module load — re-patch it
    # there too so `create_app` reads the parent-domain config.
    import app.main as main_module  # noqa: PLC0415

    monkeypatch.setattr(main_module, "get_settings", lambda: with_domain)

    fresh_app = create_app()

    # Walk the middleware stack and pick out SessionMiddleware. Starlette
    # stores middleware as a list of `Middleware` records, each with
    # `cls` + `kwargs`; we want the kwargs for the session entry.
    session_kwargs = next(
        (m.kwargs for m in fresh_app.user_middleware if m.cls.__name__ == "SessionMiddleware"),
        None,
    )
    assert session_kwargs is not None, "SessionMiddleware not registered"
    assert (
        session_kwargs.get("domain") == ".aptly.fyi"
    ), f"SessionMiddleware domain wiring missing: {session_kwargs}"
    # Sanity-check the other attrs we always set, so this test also
    # locks down the cookie's flag surface.
    assert session_kwargs.get("same_site") == "lax"
    assert session_kwargs.get("https_only") is True  # production env


def test_session_middleware_omits_domain_when_env_unset(monkeypatch):
    """When `COOKIE_DOMAIN` is empty (local dev OR the legacy
    Vercel-rewrite-proxy setup), SessionMiddleware must NOT receive
    a `domain` kwarg — Starlette would otherwise emit `Domain=` and
    cause weird behaviour in some browsers. Host-only is the
    correct default."""
    import app.config as cfg  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    no_domain = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test-session-secret",
        ENVIRONMENT="development",
        # COOKIE_DOMAIN deliberately unset.
    )
    cfg.get_settings.cache_clear()
    monkeypatch.setattr(cfg, "get_settings", lambda: no_domain)
    import app.main as main_module  # noqa: PLC0415

    monkeypatch.setattr(main_module, "get_settings", lambda: no_domain)
    fresh_app = create_app()
    session_kwargs = next(
        (m.kwargs for m in fresh_app.user_middleware if m.cls.__name__ == "SessionMiddleware"),
        None,
    )
    assert session_kwargs is not None
    assert "domain" not in session_kwargs


def test_auth_logout_carries_cookie_domain_when_configured(factories):
    """When `COOKIE_DOMAIN=.aptly.fyi` is set, the logout's
    delete-cookie header MUST carry the matching `Domain=.aptly.fyi`.
    SessionMiddleware sets the cookie with that scope on sign-in so
    it works first-party for both `aptly.fyi` and `api.aptly.fyi`;
    deleting it requires the SAME scope or the browser keeps the
    parent-domain cookie and the user can't sign back in.

    This is the missing piece behind the "can't re-login after
    sign-out" bug on the live `*.aptly.fyi` setup."""
    with_domain = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test-session-secret",
        GOOGLE_CLIENT_ID="test-client-id",
        GOOGLE_CLIENT_SECRET="test-client-secret",
        GOOGLE_REDIRECT_URI="https://api.aptly.fyi/api/auth/google/callback",
        FRONTEND_URL="https://aptly.fyi",
        COOKIE_DOMAIN=".aptly.fyi",
        ENVIRONMENT="production",
        INITIAL_USER_EMAIL="owner@example.com",
    )
    test_client = _client_with_user(factories, with_domain, user=None)
    try:
        res = test_client.post("/api/auth/logout")
        assert res.status_code == 200
        set_cookies = res.headers.get_list("set-cookie")
        # Find the deletion entry (session cookie, with delete markers)
        # and assert it carries the configured Domain.
        deletes = [
            c
            for c in set_cookies
            if c.lower().startswith("session=")
            and ("max-age=0" in c.lower() or 'session=""' in c.lower() or "session=;" in c.lower())
        ]
        assert deletes, f"no session-cookie deletion in Set-Cookie: {set_cookies}"
        header = deletes[0].lower()
        # The scope MUST match what SessionMiddleware writes on set.
        assert "domain=.aptly.fyi" in header, f"missing parent-domain scope: {deletes[0]}"
        # And the rest of the cookie attributes still need to match
        # so the browser treats this as the same cookie.
        assert "path=/" in header
        assert "samesite=lax" in header
        assert "secure" in header  # production env
        assert "httponly" in header
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def test_auth_logout_explicitly_deletes_cookie(factories, settings):
    """`POST /api/auth/logout` writes a Set-Cookie that DELETES the
    session cookie (empty value + Max-Age=0 / expired), not just
    relying on Starlette's implicit clear-on-empty.

    Why this matters: Safari ITP + some Chrome cookie policies don't
    honour the implicit delete-cookie that SessionMiddleware emits
    when a session goes from "had data" to "empty". An explicit
    Set-Cookie with all matching attributes is unambiguous. Without
    it, stale OAuth state from a previous handshake survives logout
    and trips up the next sign-in. See the long-form note in the
    `auth_logout` docstring."""
    test_client = _client_with_user(factories, settings, user=None)
    try:
        res = test_client.post("/api/auth/logout")
        assert res.status_code == 200
        # FastAPI returns headers as a multi-dict; pull every
        # Set-Cookie line so we don't miss one when SessionMiddleware
        # ALSO emits an implicit delete.
        set_cookies = res.headers.get_list("set-cookie")
        # At least one must be the `session` cookie being deleted —
        # either empty value or an explicit Max-Age=0 / past expiry.
        deletes = [
            c
            for c in set_cookies
            if c.lower().startswith("session=")
            and (
                'session=""' in c.lower()
                or "session=;" in c.lower()
                or "max-age=0" in c.lower()
                or "expires=" in c.lower()
            )
        ]
        assert deletes, f"no session-cookie deletion in Set-Cookie: {set_cookies}"
        # Attributes that MUST be on the delete so the browser
        # matches it to the originally-set cookie.
        delete_header = deletes[0].lower()
        assert "path=/" in delete_header
        assert "samesite=lax" in delete_header
        assert "httponly" in delete_header
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


# ── Post-login redirect target ─────────────────────────────────────────────


def test_oauth_callback_500s_when_frontend_url_unset_via_build_url():
    """The auth callback bounces the user back to `FRONTEND_URL`. If
    it isn't configured the helper raises an explicit 500 instead of
    falling back to `http://localhost:3000` — the localhost default
    caused `ERR_CONNECTION_REFUSED` for prod sign-ins."""
    from fastapi import HTTPException

    from app.api.auth import _build_frontend_url

    unconfigured = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test",
        # No FRONTEND_URL.
    )
    with pytest.raises(HTTPException) as exc:
        _build_frontend_url(unconfigured, "/")
    assert exc.value.status_code == 500
    assert "FRONTEND_URL" in exc.value.detail


def test_oauth_start_503_includes_frontend_url_in_requirements():
    """`has_google_oauth` now requires `frontend_url` too, so the
    start endpoint won't kick off OAuth and 500 on callback — it
    fails up front."""
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test",
        GOOGLE_CLIENT_ID="x",
        GOOGLE_CLIENT_SECRET="y",
        GOOGLE_REDIRECT_URI="https://api.example/api/auth/google/callback",
        # Deliberately no FRONTEND_URL.
    )
    assert settings.has_google_oauth is False


def test_oauth_fully_configured_requires_all_four_envs():
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test",
        GOOGLE_CLIENT_ID="x",
        GOOGLE_CLIENT_SECRET="y",
        GOOGLE_REDIRECT_URI="https://api.example/api/auth/google/callback",
        FRONTEND_URL="https://aptly-buvg.vercel.app",
    )
    assert settings.has_google_oauth is True


def test_build_frontend_url_strips_trailing_slash_and_assembles_path():
    """Combining a `FRONTEND_URL` that ends in `/` with a path that
    starts with `/` must NOT yield a double slash — the trailing
    slash is stripped before composition."""
    from app.api.auth import _build_frontend_url

    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test",
        FRONTEND_URL="https://aptly-buvg.vercel.app/",
    )
    assert _build_frontend_url(settings, "/profile") == "https://aptly-buvg.vercel.app/profile"
    assert (
        _build_frontend_url(settings, "/sign-in", error="oauth")
        == "https://aptly-buvg.vercel.app/sign-in?error=oauth"
    )


def test_build_frontend_url_rejects_absolute_next_path():
    """Open-redirect mitigation: a `next=` that's an absolute URL
    must NOT become the redirect target — only paths relative to
    `FRONTEND_URL` are honoured."""
    from app.api.auth import _build_frontend_url

    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        SESSION_SECRET="test",
        FRONTEND_URL="https://aptly-buvg.vercel.app",
    )
    # Absolute-URL `next` collapses to "/" — never lets the user be
    # bounced to evil.com.
    assert (
        _build_frontend_url(settings, "https://evil.com/steal") == "https://aptly-buvg.vercel.app/"
    )
    assert _build_frontend_url(settings, "//evil.com/steal") == "https://aptly-buvg.vercel.app/"


# ── Same-origin session cookie attributes ─────────────────────────────────


def test_session_cookie_uses_samesite_lax_in_production():
    """The browser → backend call is now SAME-ORIGIN via the
    frontend's Next.js rewrite proxy (see `frontend/next.config.mjs`).
    With first-party cookies, `SameSite=Lax` is the right choice:
    survives Safari ITP and Chrome/Firefox incognito (which both
    block the third-party cookies that the old `SameSite=None`
    required). `Secure` is still on in production because the
    proxied request rides HTTPS."""
    import os

    from starlette.middleware.sessions import SessionMiddleware

    from app.main import create_app

    prev_env = os.environ.get("ENVIRONMENT")
    os.environ["ENVIRONMENT"] = "production"
    try:
        from app import config as cm

        cm.get_settings.cache_clear()
        app = create_app()
        # Find the SessionMiddleware on the stack. Starlette stores
        # user-middleware as `Middleware(cls, options)` entries on
        # `app.user_middleware`.
        session_mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
        kwargs = session_mw.kwargs
        assert kwargs.get("same_site") == "lax"
        assert kwargs.get("https_only") is True
    finally:
        if prev_env is None:
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = prev_env
        cm.get_settings.cache_clear()


def test_session_cookie_uses_samesite_lax_in_development():
    """Local dev runs over HTTP at http://localhost:3000;
    `SameSite=None` would require `Secure=True` which drops the
    cookie on the plain-HTTP loopback. Keep `Lax` + no Secure flag
    for dev so `next dev` keeps working."""
    import os

    from starlette.middleware.sessions import SessionMiddleware

    from app.main import create_app

    prev_env = os.environ.get("ENVIRONMENT")
    os.environ["ENVIRONMENT"] = "development"
    try:
        from app import config as cm

        cm.get_settings.cache_clear()
        app = create_app()
        session_mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
        kwargs = session_mw.kwargs
        assert kwargs.get("same_site") == "lax"
        assert kwargs.get("https_only") is False
    finally:
        if prev_env is None:
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = prev_env
        cm.get_settings.cache_clear()


def test_cors_middleware_explicitly_lists_frontend_origin_with_credentials():
    """Browsers reject `Access-Control-Allow-Origin: *` together
    with `Access-Control-Allow-Credentials: true`. Pin that the
    CORS middleware uses an allow-list AND has credentials on."""
    import os

    from fastapi.middleware.cors import CORSMiddleware

    from app.main import create_app

    prev_cors = os.environ.get("CORS_ORIGINS")
    os.environ["CORS_ORIGINS"] = "https://aptly-buvg.vercel.app"
    try:
        from app import config as cm

        cm.get_settings.cache_clear()
        app = create_app()
        cors_mw = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
        kwargs = cors_mw.kwargs
        assert kwargs.get("allow_credentials") is True
        origins = kwargs.get("allow_origins", [])
        # Wildcards forbidden — they'd be silently rejected by the
        # browser when combined with credentials.
        assert "*" not in origins
        assert "https://aptly-buvg.vercel.app" in origins
    finally:
        if prev_cors is None:
            os.environ.pop("CORS_ORIGINS", None)
        else:
            os.environ["CORS_ORIGINS"] = prev_cors
        cm.get_settings.cache_clear()
