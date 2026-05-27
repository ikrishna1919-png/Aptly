"""The Source model — one row per (source_type, token) the ingest pulls from.

Replaces the hardcoded list in `app/sources/companies.py` as the runtime
source of truth: ingest reads enabled rows from here, calls the matching
adapter (Greenhouse / Lever / SmartRecruiters / …), and writes per-source
observability back onto the row (`last_run_at`, `last_status`,
`last_error`, `jobs_found_last_run`) so each token's health is queryable.

`companies.py` is still useful — Alembic migration 0007 imports its lists
to seed this table on first deploy, and its tests pin the seed contents.
After the migration runs, the DB is the source of truth.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Allowed values for `source_type`. The runtime mapping from a string here
# to an adapter class lives in `app.sources.SOURCES`. `workday`, `direct`,
# and `other` are accepted shapes for future adapters — the ingest loop
# logs a "skipped: unknown source type" if a row references one that
# isn't registered, but never crashes.
SOURCE_TYPES: tuple[str, ...] = (
    "greenhouse",
    "lever",
    "smartrecruiters",
    "workday",
    "direct",
    "other",
)


class Source(Base):
    """One ingest-able board/feed, keyed by (source_type, token).

    `enabled=False` disables it without losing its row (and its observability
    history). `last_run_at` / `last_status` / `last_error` /
    `jobs_found_last_run` are written by the ingest loop after every pass.
    """

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("source_type", "token", name="uq_sources_type_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    # Per-source observability — written by `app.services.ingest.run_ingest`
    # after every pass. All nullable so a never-run row reads cleanly.
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    jobs_found_last_run: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Status values written into `last_status`. Kept as constants so the
# ingest loop, the tests, and any future admin UI all agree on spelling.
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"
