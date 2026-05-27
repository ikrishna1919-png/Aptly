"""ParseRun rows record the result of a background resume-parse pass.

`POST /api/admin/profile/parse` returns immediately with a `run_id`;
the actual Claude call happens in a background thread (so a slow
Anthropic response can't time the request out of the Render free-tier
100s budget). Status + the parsed profile (or error message) are
written here so the frontend can poll
`GET /api/admin/profile/parse/{run_id}` until the row settles.

Mirrors the `IngestRun` table — same lifecycle (`pending` → `running`
→ `success` | `failed`), same `started_at` / `finished_at`, just a
`profile` JSON payload instead of `stats`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ParseRun(Base):
    __tablename__ = "parse_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="running",
        comment="pending | running | success | failed",
    )
    # Populated on `success` with the parsed Profile shape. Null while
    # the worker is still running or on `failed`.
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Populated on `failed` with a user-facing error message. Null
    # otherwise.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


PARSE_STATUS_PENDING = "pending"
PARSE_STATUS_RUNNING = "running"
PARSE_STATUS_SUCCESS = "success"
PARSE_STATUS_FAILED = "failed"
