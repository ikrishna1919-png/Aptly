"""ATS resume-hub endpoints.

  POST /api/ats/parse-upload   (multipart) -> {kind, upload_id?, profile?}
  POST /api/ats/generate       {option_type, jd_text, questions, format, ...} -> {run_id}
  GET  /api/ats/runs/{run_id}  -> status + (resume | docx-injection diff)
  POST /api/ats/runs/{id}/download-docx  {accepted} -> edited DOCX (option 2)

Generate-path downloads (jd_paste / pdf_fallback) reuse /api/tailor/docx and
/pdf, which now accept `fmt` + `custom_options`. Runs persist in `tailor_runs`
and are driven by `ats_runs` background workers.
"""

from __future__ import annotations

import io
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models.tailor_run import TailorRun
from app.models.user import User
from app.services import ats, ats_runs, default_formats, keyword_coverage, linkedin_import
from app.services.demo_candidate import get_candidate

router = APIRouter()

_MAX_UPLOAD = 5 * 1024 * 1024  # 5 MB
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ─── Upload + parse ──────────────────────────────────────────────────────────


@router.post("/ats/parse-upload")
async def ats_parse_upload(
    file: UploadFile = File(..., description="DOCX or PDF resume."),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    raw = await file.read(_MAX_UPLOAD + 1)
    if len(raw) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB).")
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    name = (file.filename or "").lower()

    if name.endswith(".docx") or file.content_type == _DOCX_MIME:
        # Store the original bytes for in-place keyword injection later.
        run_id = ats_runs.create_docx_upload(
            user_id=user.id, filename=file.filename or "resume.docx", docx_blob=raw
        )
        return {"kind": "docx", "upload_id": run_id}

    if name.endswith(".pdf") or file.content_type == "application/pdf":
        # PDF can't preserve formatting → the frontend routes to the Option-1
        # fallback. We don't parse a profile override here (generation is
        # grounded in the saved Aptly profile); the upload just confirms the
        # user's intent + readability is handled downstream.
        return {"kind": "pdf"}

    raise HTTPException(status_code=415, detail="Unsupported file type. Upload a .docx or .pdf.")


# ─── Generate ──────────────────────────────────────────────────────────────


class CustomOptions(BaseModel):
    base: str = "modern"
    accent_color: str = "blue"
    font_family: str = "sans"
    margins: str = "normal"


class GenerateRequest(BaseModel):
    option_type: Literal["jd_paste", "upload_docx", "upload_pdf_fallback"]
    jd_text: str = Field(min_length=1, max_length=20000)
    questions: dict[str, Any] = Field(default_factory=dict)
    format: str = "modern"
    custom_options: CustomOptions | None = None
    upload_id: str | None = None  # required for upload_docx


class RunIdResponse(BaseModel):
    run_id: str


@router.post("/ats/generate", status_code=status.HTTP_202_ACCEPTED, response_model=RunIdResponse)
def ats_generate(
    payload: GenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RunIdResponse:
    # Saved tailoring SOURCE is the single source of truth. "resume" (match my
    # resume format) ALWAYS runs in-place keyword inject against the user's
    # saved DOCX — never the from-scratch generate path — regardless of the
    # request's option_type/format.
    if default_formats.resume_source(db, user.id) == "resume":
        run_id = ats_runs.start_docx_inject_from_active_resume(
            user_id=user.id,
            jd_text=payload.jd_text,
            customization=payload.questions,
            settings=settings,
        )
        if run_id is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No saved resume. Upload a .docx on your Profile to use "
                    "'Match my resume format', or switch your ATS format to 'Let AI choose'."
                ),
            )
        return RunIdResponse(run_id=run_id)

    if payload.option_type == "upload_docx":
        if not payload.upload_id:
            raise HTTPException(status_code=400, detail="upload_id required for upload_docx.")
        ok = ats_runs.start_docx_inject(
            run_id=payload.upload_id,
            user_id=user.id,
            jd_text=payload.jd_text,
            customization=payload.questions,
            settings=settings,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Upload not found or not owned.")
        return RunIdResponse(run_id=payload.upload_id)

    run_id = ats_runs.start_generate(
        user_id=user.id,
        option_type=payload.option_type,
        jd_text=payload.jd_text,
        customization=payload.questions,
        fmt=payload.format,
        custom_options=payload.custom_options.model_dump() if payload.custom_options else None,
        settings=settings,
    )
    return RunIdResponse(run_id=run_id)


# ─── JD keyword coverage ─────────────────────────────────────────────────────
#
# Deterministic keyword-overlap %, NOT an invented "ATS score". Computed before
# generation (profile vs JD) and after (tailored resume vs JD) so the user sees
# the real before→after lift.


class CoverageRequest(BaseModel):
    jd_text: str = Field(min_length=1, max_length=20000)


class CoverageOut(BaseModel):
    percent: int
    matched: list[str]
    missing: list[str]


@router.post("/ats/keyword-coverage", response_model=CoverageOut)
def ats_keyword_coverage(
    payload: CoverageRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverageOut:
    """Pre-generation coverage: how many of the JD's keywords the user's CURRENT
    profile already covers."""
    candidate = get_candidate(db, user_id=user.id)
    text = keyword_coverage.candidate_text_from_profile(candidate)
    cov = keyword_coverage.score(payload.jd_text, text)
    return CoverageOut(**cov.to_dict())


# ─── Status ────────────────────────────────────────────────────────────────


class AtsRunOut(BaseModel):
    run_id: str
    status: str
    option_type: str | None
    demo_mode: bool
    format: str | None = None
    resume: dict[str, Any] | None = None  # TailoredResume JSON (generate paths)
    diff: dict[str, Any] | None = None  # {applied, skipped} (docx-inject path)
    # Post-generation JD keyword coverage of the tailored resume (generate
    # paths, when the run carries jd_text). Null otherwise.
    coverage: CoverageOut | None = None
    error: str | None = None


@router.get("/ats/runs/{run_id}", response_model=AtsRunOut)
def ats_run_status(
    run_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AtsRunOut:
    run = db.execute(
        select(TailorRun).where(TailorRun.run_id == run_id, TailorRun.user_id == user.id)
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="ats run not found")
    resume = diff = None
    coverage: CoverageOut | None = None
    rj = run.result_json
    if isinstance(rj, dict) and rj.get("kind") == "docx_keyword_inject":
        diff = {"applied": rj.get("applied", []), "skipped": rj.get("skipped", [])}
    elif rj is not None:
        resume = rj
        # Post-generation coverage of the tailored resume against this run's JD.
        if run.jd_text and isinstance(rj, dict):
            text = keyword_coverage.candidate_text_from_resume(rj)
            coverage = CoverageOut(**keyword_coverage.score(run.jd_text, text).to_dict())
    return AtsRunOut(
        run_id=run.run_id,
        status=run.status,
        option_type=run.option_type,
        demo_mode=not settings.has_anthropic_key,
        format=run.format_selection,
        resume=resume,
        diff=diff,
        coverage=coverage,
        error=run.error_text,
    )


# ─── DOCX download (Option 2 keyword-injection) ──────────────────────────────


class DownloadDocxRequest(BaseModel):
    # Indices into the run's `applied` edits the user accepted (default: all).
    accepted: list[int] | None = None


@router.post("/ats/runs/{run_id}/download-docx")
def ats_download_docx(
    payload: DownloadDocxRequest,
    run_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    run = db.execute(
        select(TailorRun).where(TailorRun.run_id == run_id, TailorRun.user_id == user.id)
    ).scalar_one_or_none()
    if run is None or run.uploaded_docx_blob is None:
        raise HTTPException(status_code=404, detail="upload not found")
    rj = run.result_json or {}
    applied = rj.get("applied", []) if isinstance(rj, dict) else []
    if payload.accepted is not None:
        applied = [applied[i] for i in payload.accepted if 0 <= i < len(applied)]
    new_bytes, _, _ = ats.apply_docx_edits(run.uploaded_docx_blob, applied)
    fname = (run.uploaded_filename or "resume.docx").rsplit(".", 1)[0] + "-tailored.docx"
    return StreamingResponse(
        io.BytesIO(new_bytes),
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ─── Default formats (Feature #1 / #5) ───────────────────────────────────────


class DefaultFormatOut(BaseModel):
    kind: str  # "resume" | "cover"
    format: str
    custom: dict[str, Any] | None = None
    reason: str | None = None
    # Resume-only tailoring SOURCE: "ai" | "resume" | "available". None for cover.
    source: str | None = None


class SaveDefaultRequest(BaseModel):
    kind: Literal["resume", "cover"]
    format: str
    custom: dict[str, Any] | None = None
    # "ai" | "resume" | "available" (resume kind only). Drives generate vs
    # in-place docx-inject routing.
    source: Literal["ai", "resume", "available"] | None = None


@router.get("/ats/default-format/{kind}", response_model=DefaultFormatOut)
def get_default_format(
    kind: Literal["resume", "cover"] = Path(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DefaultFormatOut:
    value = default_formats.resolve_default(db, user.id, kind)
    return DefaultFormatOut(
        kind=kind,
        format=value.get("format", "modern"),
        custom=value.get("custom"),
        source=value.get("source") if kind == "resume" else None,
    )


@router.post("/ats/default-format", response_model=DefaultFormatOut)
def set_default_format(
    payload: SaveDefaultRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DefaultFormatOut:
    stored: dict[str, Any] = {"format": payload.format, "custom": payload.custom}
    if payload.kind == "resume" and payload.source is not None:
        stored["source"] = payload.source
    value = default_formats.save_default(db, user.id, payload.kind, stored)
    return DefaultFormatOut(
        kind=payload.kind,
        format=value["format"],
        custom=value.get("custom"),
        source=value.get("source") if payload.kind == "resume" else None,
    )


@router.post("/ats/default-format/ai-choose/{kind}", response_model=DefaultFormatOut)
def ai_choose_default_format(
    kind: Literal["resume", "cover"] = Path(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DefaultFormatOut:
    """Deterministic heuristic pick (NOT an LLM call), then saved as default."""
    candidate = get_candidate(db, user_id=user.id)
    pick = (
        default_formats.ai_choose_cover_format(candidate)
        if kind == "cover"
        else default_formats.ai_choose_resume_format(candidate)
    )
    default_formats.save_default(db, user.id, kind, {"format": pick["format"], "custom": None})
    return DefaultFormatOut(
        kind=kind, format=pick["format"], custom=None, reason=pick.get("reason")
    )


# ─── LinkedIn data-export import (Feature #2b) ───────────────────────────────


@router.post("/ats/linkedin-import")
async def linkedin_import_endpoint(
    file: UploadFile = File(..., description="LinkedIn data-export ZIP."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Parse a user-uploaded LinkedIn export ZIP into the profile shape and
    return a new-vs-conflict diff against the existing profile for review.
    User-initiated upload — no scraping."""
    raw = await file.read(_MAX_UPLOAD + 1)
    if len(raw) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB).")
    try:
        imported = linkedin_import.parse_linkedin_zip(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    existing = get_candidate(db, user_id=user.id)
    return {
        "imported": imported,
        "diff": linkedin_import.diff_against_existing(existing, imported),
    }
