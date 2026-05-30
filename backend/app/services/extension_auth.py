"""Bearer-token auth for the browser extension.

The extension can't ride the first-party session cookie, so it uses a
long-lived random token minted by the cookie-authed `/extension/connect`
flow. We store only the SHA-256 hash; the raw token is shown to the
extension exactly once (in the connect redirect fragment).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.extension_session import ExtensionSession
from app.models.user import User


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint_session(db: Session, user: User, device_name: str | None) -> tuple[str, str]:
    """Create an extension session for `user`. Returns (raw_token, session_id).
    The raw token is never stored — only its hash."""
    raw = secrets.token_urlsafe(32)  # 256-bit
    session_id = uuid.uuid4().hex
    db.add(
        ExtensionSession(
            id=session_id,
            user_id=user.id,
            token_hash=_hash(raw),
            device_name=(device_name or "Browser extension")[:128],
        )
    )
    db.commit()
    return raw, session_id


def get_user_from_extension_token(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: resolve the `Authorization: Bearer <token>` header
    to a User via the extension_sessions table. 401 on missing/invalid/revoked.
    Bumps `last_used_at` on success."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing extension token"
        )
    token = authorization.split(" ", 1)[1].strip()
    session = db.execute(
        select(ExtensionSession).where(
            ExtensionSession.token_hash == _hash(token),
            ExtensionSession.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked extension token"
        )
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    session.last_used_at = datetime.now(UTC)
    db.commit()
    return user


__all__ = [
    "get_current_user",
    "get_user_from_extension_token",
    "mint_session",
]
