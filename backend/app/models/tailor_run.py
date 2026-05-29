"""TailorRun rows record the lifecycle of a background resume-tailoring pass.

`POST /api/tailor/start` returns immediately with a `run_id`; the analyze
Anthropic call runs in a background thread, then (after the user answers
the gap questions) a second thread streams the generated resume. Status +
every intermediate payload are written here so the frontend can poll
`GET /api/tailor/runs/{run_id}` through the whole flow without holding a
long HTTP connection open against the Render free-tier budget.

Mirrors `ParseRun`: same `run_id` / `user_id` ownership, same
`started_at` / `finished_at`, same "the worker MUST write a terminal
status" contract. The lifecycle has one extra hop because tailoring is
two-phase (analyze, then generate):

    analyzing -> pending_questions -> generating -> done | error

`pending_questions` is skipped (straight to `generating`) when the
analyze step finds no askable gaps.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TailorRun(Base):
    __tablename__ = "tailor_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Per-user ownership. Every read filters by `user_id == current.id`
    # AND `run_id` so a guessed UUID can't leak another user's run.
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Result-cache key: SHA-256 over (prompt version + candidate fingerprint +
    # normalized JD). A repeat request with the same key reuses a recent `done`
    # run's resume instead of calling Anthropic again. Null on legacy rows.
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Job tailored against. Nullable so a future pasted-JD path fits.
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Snapshot of the JD text sent to the model (for triage).
    jd_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="analyzing",
        comment="analyzing | pending_questions | generating | done | error",
    )
    # The Analysis payload (questions / matched / gaps / genuine_lacks /
    # top_skills). Populated when the analyze worker finishes.
    missing_skills_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # The user's answers to the gap questions, keyed by question text.
    user_answers_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # The TailoredResume. Written incrementally (best-effort partial
    # parse) while generating, then the final validated object on
    # `done`. Preserved on `error` so a partial generation isn't lost.
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # User-facing error message on `error`. Null otherwise.
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


TAILOR_STATUS_ANALYZING = "analyzing"
TAILOR_STATUS_PENDING_QUESTIONS = "pending_questions"
TAILOR_STATUS_GENERATING = "generating"
TAILOR_STATUS_DONE = "done"
TAILOR_STATUS_ERROR = "error"
