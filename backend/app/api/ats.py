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
from app.services import ats, ats_runs

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
    settings: Settings = Depends(get_settings),
) -> RunIdResponse:
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


# ─── Status ────────────────────────────────────────────────────────────────


class AtsRunOut(BaseModel):
    run_id: str
    status: str
    option_type: str | None
    demo_mode: bool
    format: str | None = None
    resume: dict[str, Any] | None = None  # TailoredResume JSON (generate paths)
    diff: dict[str, Any] | None = None  # {applied, skipped} (docx-inject path)
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
    rj = run.result_json
    if isinstance(rj, dict) and rj.get("kind") == "docx_keyword_inject":
        diff = {"applied": rj.get("applied", []), "skipped": rj.get("skipped", [])}
    elif rj is not None:
        resume = rj
    return AtsRunOut(
        run_id=run.run_id,
        status=run.status,
        option_type=run.option_type,
        demo_mode=not settings.has_anthropic_key,
        format=run.format_selection,
        resume=resume,
        diff=diff,
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
