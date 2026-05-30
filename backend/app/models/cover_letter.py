"""Generated cover letters for the /ats hub.

Stored structured (date / recipient / greeting / paragraphs[] / signature) so
the DOCX and PDF renderers share one source of truth, mirroring how
`tailor_runs` stores a resume.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CoverLetter(Base):
    __tablename__ = "cover_letters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jd_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    questions_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="generating")
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


COVER_STATUS_GENERATING = "generating"
COVER_STATUS_DONE = "done"
COVER_STATUS_ERROR = "error"
