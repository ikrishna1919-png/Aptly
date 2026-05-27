from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class JobAnalysis(Base):
    """Cached analyze() output for one (user, job) pair.

    Phase 5 added `user_id` so each user gets their own cache —
    different users targeting the same job have different profiles,
    which gives different gap lists. The uniqueness constraint is
    on `(user_id, job_id)` so the cache miss / hit decision is
    per-user.

    `input_hash` is still a SHA-256 of (candidate fingerprint + job
    content_hash) — if either side changes we recompute even within
    the same user.
    """

    __tablename__ = "job_analyses"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_job_analyses_user_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable on the column only because migration 0012 creates the
    # column before back-filling existing rows. New writes always set
    # it; the per-user query path filters by `user_id == current.id`.
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
