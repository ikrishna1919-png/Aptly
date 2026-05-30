"""ATS cover-letter endpoints (/api/cover-letter/*).

A single fast Anthropic call (no streaming worker needed), persisted to
`cover_letters`. Anti-fabrication rules live in the prompt (see
`services/cover_letter.py`).
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models.cover_letter import CoverLetter
from app.models.user import User
from app.services import cover_letter as cl
from app.services.default_formats import resolve_default

router = APIRouter()
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class CoverQuestions(BaseModel):
    tone: str = "confident"
    length: str = "standard"
    opening: str = "value"
    additional: str = ""


class GenerateCoverRequest(BaseModel):
    jd_text: str = Field(min_length=1, max_length=20000)
    company_name: str = Field(default="", max_length=256)
    hook: str = Field(default="", max_length=1000)
    questions: CoverQuestions = Field(default_factory=CoverQuestions)
    job_id: int | None = None


class CoverOut(BaseModel):
    id: str
    status: str
    demo_mode: bool
    format: str | None = None
    content: dict[str, Any] | None = None
    error: str | None = None


@router.post("/cover-letter/generate", response_model=CoverOut)
def generate(
    payload: GenerateCoverRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CoverOut:
    fmt = resolve_default(db, user.id, "cover").get("format", "traditional")
    cid = uuid.uuid4().hex
    row = CoverLetter(
        id=cid,
        user_id=user.id,
        job_id=payload.job_id,
        jd_text=payload.jd_text[:8000],
        company_name=payload.company_name or None,
        format=fmt,
        questions_json=payload.questions.model_dump(),
        status="generating",
    )
    db.add(row)
    db.commit()
    try:
        letter = cl.generate_cover_letter(
            db,
            user_id=user.id,
            jd_text=payload.jd_text,
            company_name=payload.company_name,
            hook=payload.hook,
            questions=payload.questions.model_dump(),
            settings=settings,
        )
        row.content_json = letter.model_dump()
        row.status = "done"
        row.finished_at = datetime.now(UTC)
        db.commit()
    except Exception as e:  # noqa: BLE001
        row.status = "error"
        row.error_text = f"Generation failed — {e}"
        row.finished_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(status_code=502, detail=row.error_text) from e
    return CoverOut(
        id=cid,
        status=row.status,
        demo_mode=not settings.has_anthropic_key,
        format=fmt,
        content=row.content_json,
    )


def _load(db: Session, user_id: int, cid: str) -> CoverLetter:
    row = db.execute(
        select(CoverLetter).where(CoverLetter.id == cid, CoverLetter.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="cover letter not found")
    return row


class UpdateCoverRequest(BaseModel):
    content: dict[str, Any]


@router.patch("/cover-letter/{cid}", response_model=CoverOut)
def update(
    payload: UpdateCoverRequest,
    cid: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CoverOut:
    """Save the user's edits to the letter (the editable preview)."""
    row = _load(db, user.id, cid)
    row.content_json = payload.content
    db.commit()
    return CoverOut(
        id=row.id,
        status=row.status,
        demo_mode=not settings.has_anthropic_key,
        format=row.format,
        content=row.content_json,
    )


@router.get("/cover-letter/{cid}/download")
def download(
    fmt: str = "docx",
    cid: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    row = _load(db, user.id, cid)
    if not row.content_json:
        raise HTTPException(status_code=409, detail="cover letter not generated yet")
    letter = cl.CoverLetterContent.model_validate(row.content_json)
    style = row.format or "traditional"
    if fmt == "pdf":
        raw = cl.render_cover_pdf(letter, style)
        return StreamingResponse(
            io.BytesIO(raw),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="cover-letter.pdf"'},
        )
    raw = cl.render_cover_docx(letter, style)
    return StreamingResponse(
        io.BytesIO(raw),
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": 'attachment; filename="cover-letter.docx"'},
    )
