"""Google OAuth sign-in + session management.

Endpoints
  * `GET  /api/auth/google/login`     — start the OAuth flow; 302 to
                                        Google. Accepts an optional
                                        `?next=` to bounce the user
                                        back to the page they
                                        clicked sign-in from.
  * `GET  /api/auth/google/callback`  — Google redirects here with
                                        `code`. Exchanges the code,
                                        finds-or-creates the user,
                                        writes a session cookie,
                                        302s to the frontend.
  * `POST /api/auth/logout`           — clears the session cookie.
  * `GET  /api/auth/me`               — current user (200) or 401.

Session model: a signed cookie holding `{"user_id": int}`. The
`itsdangerous` signature on the cookie is what authenticates the
session — no DB session table. Cookie attributes: HTTP-only (no JS
read), Secure (HTTPS only in prod), SameSite=lax (works for the
same-origin OAuth callback redirect; cross-site fetches from the
frontend still send the cookie because we set credentials='include'
in the client and CORS allow_credentials=True on the server).

Initial-user linking: when a user signs in via Google we look up
`users.google_subject_id == sub`. On a miss, we look up
`users.email == <google email>` and link by writing `sub` onto that
row — this is how the migration's bootstrap row (created with
`google_subject_id=NULL`) gets attached to the real Google account
on the owner's first sign-in. Brand-new users (no matching email)
get a fresh row with both `sub` and email populated.

OAuth is mocked in tests via `find_or_link_user`: tests build a
canned `{'sub': ..., 'email': ..., 'name': ...}` dict and pass it
to that helper directly. The full Google round-trip is exercised in
the integration test by monkey-patching `_OAUTH.google.authorize_access_token`.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User

log = logging.getLogger(__name__)

router = APIRouter()

# Module-level OAuth client. Populated lazily in `_get_oauth` so the
# Settings instance reaches it at request time (avoids ordering issues
# between module import and Settings construction).
_OAUTH: OAuth | None = None


def _get_oauth(settings: Settings) -> OAuth:
    """Create (or return cached) authlib OAuth client. Registered
    lazily so the Settings instance is read at first-request time —
    pytest fixtures that override `get_settings` work as expected."""
    global _OAUTH
    if _OAUTH is None:
        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            # Google's OpenID Connect discovery doc handles every URL
            # the OAuth flow needs (auth, token, userinfo, jwks). Lets
            # us avoid hard-coding endpoints.
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        _OAUTH = oauth
    return _OAUTH


def _reset_oauth_for_tests() -> None:
    """pytest hook: force the next `_get_oauth` call to re-register so
    a fixture that swaps Settings mid-test gets a fresh client."""
    global _OAUTH
    _OAUTH = None


# ── Session helpers ────────────────────────────────────────────────────────

SESSION_USER_KEY = "user_id"


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: 401s when there's no signed-in session,
    otherwise returns the corresponding `User` row. Use on every
    per-user endpoint."""
    user_id = request.session.get(SESSION_USER_KEY) if hasattr(request, "session") else None
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not signed in")
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        # Session points at a user that no longer exists. Treat as
        # logged-out rather than 500 — the client can prompt for a
        # fresh sign-in.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    return user


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    """Same as `get_current_user` but returns None instead of 401.
    Useful for endpoints that render differently for guests."""
    user_id = request.session.get(SESSION_USER_KEY) if hasattr(request, "session") else None
    if not user_id:
        return None
    return db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()


# ── User lookup / link / create ────────────────────────────────────────────


def find_or_link_user(db: Session, google_info: dict[str, Any]) -> User:
    """Resolve a Google userinfo dict to a `User` row, creating or
    linking as needed. Pulled out so the OAuth callback stays thin
    AND tests can exercise the linking semantics without standing up
    a full Google round-trip.

    Three branches:
      1. We already know this Google account (match on `sub`) → return.
      2. We have a row with the right email but no `sub` yet → that's
         the migration's bootstrap row (or an account a previous
         deploy created without `sub`). Write `sub` onto it and
         return.
      3. Brand-new user → insert and return.
    """
    sub = (google_info.get("sub") or "").strip()
    email = (google_info.get("email") or "").strip().lower()
    name = (google_info.get("name") or "").strip() or None
    if not sub:
        raise HTTPException(status_code=400, detail="missing google subject id")
    if not email:
        raise HTTPException(status_code=400, detail="missing google email")

    # (1) by sub.
    user = db.execute(select(User).where(User.google_subject_id == sub)).scalar_one_or_none()
    if user is not None:
        # Refresh name / email if they changed Google-side.
        if name and user.name != name:
            user.name = name
        if user.email != email:
            # Email change is unusual but allowed; if it now clashes
            # with another row, leave the existing email — the user
            # can resolve manually.
            existing = db.execute(
                select(User).where(User.email == email, User.id != user.id)
            ).scalar_one_or_none()
            if existing is None:
                user.email = email
        db.commit()
        db.refresh(user)
        return user

    # (2) by email (bootstrap link).
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None:
        user.google_subject_id = sub
        if name and user.name != name:
            user.name = name
        db.commit()
        db.refresh(user)
        return user

    # (3) brand-new.
    user = User(google_subject_id=sub, email=email, name=name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ── Routes ─────────────────────────────────────────────────────────────────


class CurrentUserOut(BaseModel):
    id: int
    email: str
    name: str | None = None

    model_config = {"from_attributes": True}


@router.get("/auth/me", response_model=CurrentUserOut)
def auth_me(user: User = Depends(get_current_user)) -> User:
    """Return the currently signed-in user. 401 when no session."""
    return user


@router.get("/auth/google/login")
async def google_login(
    request: Request,
    next: str = "/",
    settings: Settings = Depends(get_settings),
) -> Response:
    """Start the OAuth flow. Stashes the post-login destination on
    the session so the callback can bounce the user back to where
    they came from."""
    if not settings.has_google_oauth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Google sign-in is not configured. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI on the backend."
            ),
        )
    request.session["next"] = next
    oauth = _get_oauth(settings)
    return await oauth.google.authorize_redirect(request, settings.google_redirect_uri)


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """OAuth callback. Exchanges `code` for an ID token, resolves to
    a `User` (creating or linking), then sets the session cookie and
    redirects to the frontend."""
    if not settings.has_google_oauth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured on the backend.",
        )
    oauth = _get_oauth(settings)
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        log.warning("google oauth error: %s", e)
        # Bounce the user back to the sign-in page with a flag the
        # frontend can pick up. Never surface the raw OAuth error
        # (might leak the configured client id).
        url = _build_frontend_url(settings, "/sign-in", error="oauth")
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    # `userinfo` is the parsed OIDC userinfo claims dict; `id_token`
    # is the raw JWT.
    google_info = token.get("userinfo") or {}
    user = find_or_link_user(db, google_info)

    request.session[SESSION_USER_KEY] = user.id
    next_path = request.session.pop("next", None) or "/"
    return RedirectResponse(
        url=_build_frontend_url(settings, next_path), status_code=status.HTTP_302_FOUND
    )


@router.post("/auth/logout")
def auth_logout(request: Request) -> dict[str, bool]:
    """Drop the session entry. The cookie itself stays (signed) but
    no longer carries a `user_id`, so subsequent requests are
    treated as unauthenticated."""
    if hasattr(request, "session"):
        request.session.clear()
    return {"ok": True}


def _build_frontend_url(settings: Settings, path: str, **query: str) -> str:
    """Combine `settings.frontend_url` + a path + optional query
    args. Defensive against open-redirect: only the configured
    frontend URL is ever the host."""
    base = settings.frontend_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    # Reject any absolute URL passed through `next=` — only same-origin
    # paths are honoured. This is the open-redirect mitigation.
    if path.startswith("//") or "://" in path:
        path = "/"
    url = f"{base}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return url
