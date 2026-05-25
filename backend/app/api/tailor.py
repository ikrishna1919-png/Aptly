"""AI resume tailoring endpoints (Phase 4, single-user).

  POST /api/tailor/analyze   { job_id }                  -> Analysis
  POST /api/tailor/generate  { job_id, answers }         -> TailoredResume
  POST /api/tailor/docx      { resume }                  -> streamed DOCX

All endpoints fall through to deterministic demo-mode data when
ANTHROPIC_API_KEY isn't configured — see `app.services.tailor` for the
fallback shape.
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.job import Job
from app.services.docx_export import render_docx
from app.services.tailor import (
    Analysis,
    TailoredResume,
    analyze_job,
    generate_resume,
)

router = APIRouter()


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
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AnalyzeResponse:
    job = _load_job(db, payload.job_id)
    analysis = analyze_job(db, job, settings=settings)
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
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> GenerateResponse:
    job = _load_job(db, payload.job_id)
    resume = generate_resume(db, job, payload.answers, settings=settings)
    return GenerateResponse(
        job_id=job.id,
        demo_mode=not settings.has_anthropic_key,
        resume=resume,
    )


# ── DOCX export ─────────────────────────────────────────────────────────────


class DocxRequest(BaseModel):
    resume: TailoredResume
    filename: str | None = Field(
        default=None, max_length=128, description="Override the suggested filename"
    )


@router.post("/tailor/docx")
def tailor_docx(
    payload: DocxRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream the tailored resume as a DOCX file.

    Takes the already-generated `TailoredResume` rather than re-calling the
    LLM — the frontend already has the data, and we don't want to double-pay
    for the same tailoring. The candidate's header (name / email / phone /
    location) is read from the canonical DB row so name changes to the
    seed are reflected immediately.
    """
    from app.services.demo_candidate import get_candidate  # noqa: PLC0415

    raw = render_docx(payload.resume, candidate=get_candidate(db))
    name = (payload.filename or "tailored-resume").strip() or "tailored-resume"
    if not name.lower().endswith(".docx"):
        name = f"{name}.docx"

    return StreamingResponse(
        io.BytesIO(raw),
        media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
