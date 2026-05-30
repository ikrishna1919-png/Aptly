"""The Candidate model — one profile row per authenticated user.

Phase 4 was single-user (slug='demo', seeded by migration 0005).
Phase 5 introduces `user_id` and migrates the existing demo row to
the `INITIAL_USER_EMAIL` owner so no data is lost. `slug` is kept on
the column but is no longer the queried key — the tailoring service
now looks up `Candidate.user_id == current_user.id`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Per-user ownership. Nullable on the column only because the
    # migration creates the column before back-filling — every
    # application write must set it (`unique=True` enforces one
    # profile per user).
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Set explicitly the first time the user `PUT`s to `/api/profile`
    # (and refreshed on every subsequent save). Distinguishes a row
    # auto-seeded from `DEMO_CANDIDATE` (NULL) from one the user has
    # actually claimed (datetime set). The auth `/me` endpoint
    # surfaces this as a `profile_saved` boolean so the frontend can
    # gate `/jobs` behind a real save without leaking demo data into
    # the tailoring flow.
    profile_saved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # /ats hub default formats: {"format": "modern", "custom": {...}|null}.
    # Null until the user picks one; app code falls back to "modern".
    default_resume_format: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    default_cover_letter_format: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # One saved ("active") resume per user — uploaded on /profile, reused by
    # the /ats match-upload + builder paths. All nullable (additive, 0023).
    active_resume_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    active_resume_content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active_resume_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_resume_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


DEMO_SLUG = "demo"
