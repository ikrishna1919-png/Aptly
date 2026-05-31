"""IngestRun rows record the result of a background ingest pass.

POST /api/admin/ingest now returns immediately with a `run_id`; the actual
work happens in a background thread. Status + final stats are written here
so the scheduled workflow and ad-hoc operators can see how the last run
went without holding open a long HTTP connection.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="running",
        comment="running | success | failed",
    )
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


INGEST_STATUS_RUNNING = "running"
INGEST_STATUS_SUCCESS = "success"
INGEST_STATUS_FAILED = "failed"
# Derived/reported status only — a `running` row whose heartbeat has gone
# quiet past STALE_RUN_MINUTES. Never persisted by the worker; the reaper
# self-heals such rows to `failed`. Surfaced by the status endpoints.
INGEST_STATUS_STALE = "stale"
