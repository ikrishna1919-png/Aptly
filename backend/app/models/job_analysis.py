from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class JobAnalysis(Base):
    """Cached analyze() output for a job, keyed by (job_id, input_hash).

    `input_hash` is a SHA-256 of (candidate fingerprint + job content_hash) —
    if either side changes we recompute. Without it, edits to the canonical
    candidate would silently serve stale analyses.
    """

    __tablename__ = "job_analyses"
    __table_args__ = (UniqueConstraint("job_id", name="uq_job_analyses_job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
