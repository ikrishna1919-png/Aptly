"""AI resume tailoring endpoints — Phase 5, per-user.

  POST /api/tailor/analyze   { job_id }                  -> Analysis
  POST /api/tailor/generate  { job_id, answers }         -> TailoredResume
  POST /api/tailor/docx      { resume, mode? }           -> streamed DOCX
  POST /api/tailor/pdf       { resume, mode? }           -> streamed PDF

`mode` is "visual" (default) or "plain" (max ATS compatibility). The DOCX
and PDF carry identical text; only the styling differs.

All endpoints require a signed-in user (`get_current_user`). The candidate
profile + the per-job analysis cache are scoped per user. The endpoints
fall through to deterministic demo-mode data when ANTHROPIC_API_KEY isn't
configured.
"""

from __future__ import annotations

import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models.job import Job
from app.models.tailor_run import TailorRun
from app.models.user import User
from app.services.demo_candidate import get_candidate
from app.services.docx_export import render_docx
from app.services.pdf_export import render_pdf
from app.services.tailor import (
    Analysis,
    TailoredResume,
    analyze_job,
    generate_resume,
)
from app.services.tailor_runs import (
    profile_is_thin,
    start_tailor_run,
    submit_answers,
)

router = APIRouter()

# "visual" (default, richer styling) or "plain" (max ATS parser
# compatibility — no rules, dates inline).
RenderMode = Literal["visual", "plain"]
# Header (name + contact block) alignment. Orthogonal to mode; default center.
# Body text always stays left regardless.
HeaderAlignment = Literal["left", "center", "right"]


def _load_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return job


# ── Analyze ─────────────────────────────────────────────────────────────────


class AnalyzeRequest(BaseModel):
    job_id: int = Field(ge=1)


class AnalyzeResponse(BaseModel):
    job_id: int
    demo_mode: bool
    analysis: Analysis


@router.post("/tailor/analyze", response_model=AnalyzeResponse)
def tailor_analyze(
    payload: AnalyzeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AnalyzeResponse:
    job = _load_job(db, payload.job_id)
    analysis = analyze_job(db, job, user_id=user.id, settings=settings)
    return AnalyzeResponse(
        job_id=job.id,
        demo_mode=not settings.has_anthropic_key,
        analysis=analysis,
    )


# ── Generate ────────────────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    job_id: int = Field(ge=1)
    answers: dict[str, str] = Field(
        default_factory=dict,
        description="User answers to the tailoring questions, keyed by question.",
    )


class GenerateResponse(BaseModel):
    job_id: int
    demo_mode: bool
    resume: TailoredResume


@router.post("/tailor/generate", response_model=GenerateResponse)
def tailor_generate(
    payload: GenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> GenerateResponse:
    job = _load_job(db, payload.job_id)
    resume = generate_resume(db, job, payload.answers, user_id=user.id, settings=settings)
    return GenerateResponse(
        job_id=job.id,
        demo_mode=not settings.has_anthropic_key,
        resume=resume,
    )


# ── Run-based flow (background + streaming) ───────────────────────────────────
#
# The flagship tailoring flow. `/start` kicks off analysis in the background
# and returns a run_id in <1s; the client polls `/runs/{run_id}` through
# analyzing → pending_questions → generating → done. Answers are posted to
# `/runs/{run_id}/answers`. Downloads reuse the `/docx` and `/pdf` endpoints
# below (they take the user's possibly-edited resume JSON in the body).


class StartRequest(BaseModel):
    job_id: int = Field(ge=1)
    # When the profile is thin we 409 with code="profile_thin" so the UI can
    # offer "Go to Profile" vs "Generate anyway". `force=True` skips that gate.
    force: bool = False


class StartResponse(BaseModel):
    run_id: str


class AnswersRequest(BaseModel):
    answers: dict[str, str] = Field(
        default_factory=dict,
        description="User answers to the gap questions, keyed by question text.",
    )


class TailorRunOut(BaseModel):
    run_id: str
    status: str
    demo_mode: bool
    # True when this result was served from the 7-day cache (no model call).
    cached: bool = False
    analysis: Analysis | None = None
    # The tailored resume — partial while status == generating, final on done.
    resume: TailoredResume | None = None
    error: str | None = None


@router.post(
    "/tailor/start",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StartResponse,
)
def tailor_start(
    payload: StartRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StartResponse:
    """Validate fast, then kick off the analyze stage in the background.

    Returns 202 with a run_id in <1s — no Anthropic call on this path. 409
    with code="profile_thin" when the profile is too empty to tailor from,
    unless `force` is set."""
    job = _load_job(db, payload.job_id)
    if not payload.force:
        candidate = get_candidate(db, user_id=user.id)
        if profile_is_thin(candidate):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "profile_thin",
                    "message": (
                        "Your profile is mostly empty — tailoring works best once "
                        "you've filled in your experience."
                    ),
                },
            )
    run_id = start_tailor_run(user_id=user.id, job=job, settings=settings)
    return StartResponse(run_id=run_id)


def _run_to_out(run: TailorRun, *, demo_mode: bool) -> TailorRunOut:
    analysis: Analysis | None = None
    if run.missing_skills_json is not None:
        try:
            analysis = Analysis.model_validate(run.missing_skills_json)
        except Exception:  # noqa: BLE001 — never let a stored blob 500 the poll
            analysis = None
    resume: TailoredResume | None = None
    if run.result_json is not None:
        try:
            resume = TailoredResume.model_validate(run.result_json)
        except Exception:  # noqa: BLE001
            resume = None
    # A cache hit is a `done` row with a result but no analysis payload —
    # `start_tailor_run` copies the prior resume in without ever running the
    # analyze step that would populate `missing_skills_json`.
    cached = (
        run.status == "done" and run.result_json is not None and run.missing_skills_json is None
    )
    return TailorRunOut(
        run_id=run.run_id,
        status=run.status,
        demo_mode=demo_mode,
        cached=cached,
        analysis=analysis,
        resume=resume,
        error=run.error_text,
    )


@router.get("/tailor/runs/{run_id}", response_model=TailorRunOut)
def tailor_run_status(
    run_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TailorRunOut:
    """Poll a tailor run. Ownership-scoped: filters on `user_id` AND `run_id`
    so a guessed UUID can't leak another user's run. Cheap + safe to poll."""
    run = db.execute(
        select(TailorRun).where(TailorRun.run_id == run_id, TailorRun.user_id == user.id)
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tailor run not found")
    return _run_to_out(run, demo_mode=not settings.has_anthropic_key)


@router.post(
    "/tailor/runs/{run_id}/answers",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StartResponse,
)
def tailor_run_answers(
    payload: AnswersRequest,
    run_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> StartResponse:
    """Submit answers to the gap questions and kick off generation in the
    background. Also the retry path: re-postable when a run is in `error`."""
    ok = submit_answers(run_id, payload.answers, user_id=user.id, settings=settings)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="tailor run not found, not owned, or not awaiting answers",
        )
    return StartResponse(run_id=run_id)


# ── DOCX export ─────────────────────────────────────────────────────────────


class RenderRequest(BaseModel):
    resume: TailoredResume
    mode: RenderMode = Field(
        default="visual",
        description='Render style: "visual" (default) or "plain" (max ATS compatibility).',
    )
    header_alignment: HeaderAlignment = Field(
        default="center",
        description='Header (name + contact) alignment: "left" | "center" | "right".',
    )
    # /ats format selection. When set, overrides `mode`: "modern" | "classic" |
    # "minimal" | "plain" | "custom". `custom_options` carries the custom knobs.
    fmt: str | None = Field(default=None, description="ATS format name (overrides mode).")
    custom_options: dict | None = Field(default=None, description="Custom-format options.")
    filename: str | None = Field(
        default=None, max_length=128, description="Override the suggested filename"
    )


def _filename(raw: str | None, ext: str) -> str:
    name = (raw or "tailored-resume").strip() or "tailored-resume"
    if not name.lower().endswith(f".{ext}"):
        name = f"{name}.{ext}"
    return name


@router.post("/tailor/docx")
def tailor_docx(
    payload: RenderRequest,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream the tailored resume as a DOCX file in the requested `mode`.
    Contact details live on the resume itself (reconciled server-side from
    the profile at generate time), so the download reflects the saved
    profile without re-running the LLM."""
    raw = render_docx(
        payload.resume,
        mode=payload.mode,
        header_alignment=payload.header_alignment,
        fmt=payload.fmt,
        custom=payload.custom_options,
    )
    name = _filename(payload.filename, "docx")
    return StreamingResponse(
        io.BytesIO(raw),
        media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/tailor/pdf")
def tailor_pdf(
    payload: RenderRequest,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream the tailored resume as a PDF in the requested `mode`. Same
    text content as the DOCX — only the styling differs."""
    raw = render_pdf(
        payload.resume,
        mode=payload.mode,
        header_alignment=payload.header_alignment,
        fmt=payload.fmt,
        custom=payload.custom_options,
    )
    name = _filename(payload.filename, "pdf")
    return StreamingResponse(
        io.BytesIO(raw),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
