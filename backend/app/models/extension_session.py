"""Per-device bearer tokens for the browser extension.

The extension can't use the first-party session cookie (chrome-extension://
origin), so it authenticates with a long-lived bearer token minted by the
`/extension/connect` page. We persist only the SHA-256 hash of the token —
enough to look it up and revoke it, but a DB leak never yields a usable token.
Revocable per-device from the /profile "Connected devices" UI.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExtensionSession(Base):
    __tablename__ = "extension_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    device_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
