"""The User model — one row per authenticated person.

Phase 5 introduces per-user accounts via Google sign-in. Before this
phase the app was single-user, gated by `ADMIN_TOKEN`; the admin gate
stays in place for the `/api/admin/*` routes (ingest, manual jobs)
but the user-facing surface (profile, parse, tailoring) is now
session-authenticated and scoped per user.

Initial-user backfill (migration `0012_users_table`):
  * A row is seeded with `email=INITIAL_USER_EMAIL` and `google_subject_id=NULL`.
  * Existing per-user rows (`candidates`, `parse_runs`, `job_analyses`)
    are backfilled onto this user so the owner sees their existing
    data the first time they sign in with the matching email.
  * On the owner's first Google sign-in, the auth handler finds the
    row by email AND lit `google_subject_id` to the Google `sub` so
    subsequent sign-ins lookup by `sub` (the stable identifier — an
    email change in Google won't orphan the account).
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Google's stable per-user identifier (`sub` claim). Nullable
    # because the initial-user backfill row exists before the owner
    # has signed in; gets populated on first OAuth callback that
    # matches the row by email.
    google_subject_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
