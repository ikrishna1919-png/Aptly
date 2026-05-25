from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Source identifier for jobs created by the admin "Add Job" flow. These rows
# survive the rolling-window cleanup — they only go away on explicit DELETE.
MANUAL_SOURCE = "manual"


class Job(Base):
    """A normalized job posting ingested from a public ATS board.

    The rolling-window guarantee lives on `source_updated_at`: anything older
    than `HOURS_WINDOW` is deleted on each ingest pass — *except* rows with
    `source == MANUAL_SOURCE`, which persist until explicitly deleted.
    DB row timestamps (`created_at` / `updated_at`) are bookkeeping only.
    """

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(256))
    company: Mapped[str] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(String(512))
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    remote: Mapped[bool | None] = mapped_column(Boolean(), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    salary: Mapped[str | None] = mapped_column(String(128), nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSON(), nullable=False, default=list)
    sponsors_visa: Mapped[bool | None] = mapped_column(Boolean(), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
