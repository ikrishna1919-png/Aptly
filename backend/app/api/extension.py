"""Browser-extension API (v1.0). All under /api/extension/*.

Auth split:
  * session minting + management (/sessions/*) use the first-party cookie
    (`get_current_user`) — driven by the /extension/connect page and the
    /profile "Connected devices" UI.
  * everything the extension itself calls uses the bearer token
    (`get_user_from_extension_token`).
  * QA management used by the /profile "Saved answers" UI uses the cookie.
"""

from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.extension_session import ExtensionSession
from app.models.saved_qa_pair import SavedQAPair
from app.models.tailor_run import TailorRun
from app.models.user import User
from app.services import qa_clustering
from app.services.demo_candidate import get_candidate
from app.services.docx_export import render_docx
from app.services.extension_auth import (
    get_current_user,
    get_user_from_extension_token,
    mint_session,
)
from app.services.tailor import TailoredResume

router = APIRouter()
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ─── Session minting + management (cookie auth) ──────────────────────────────


class CreateSessionRequest(BaseModel):
    device_name: str | None = Field(default=None, max_length=128)


class CreateSessionResponse(BaseModel):
    token: str
    session_id: str


@router.post("/extension/sessions/create", response_model=CreateSessionResponse)
def create_session(
    payload: CreateSessionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateSessionResponse:
    """Mint a bearer token for the extension. Cookie-authed — called by the
    /extension/connect page once the user is signed in to aptly.fyi."""
    raw, session_id = mint_session(db, user, payload.device_name)
    return CreateSessionResponse(token=raw, session_id=session_id)


class SessionOut(BaseModel):
    id: str
    device_name: str | None
    created_at: str
    last_used_at: str | None
    revoked: bool


@router.get("/extension/sessions", response_model=list[SessionOut])
def list_sessions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SessionOut]:
    rows = (
        db.execute(
            select(ExtensionSession)
            .where(ExtensionSession.user_id == user.id)
            .order_by(ExtensionSession.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        SessionOut(
            id=s.id,
            device_name=s.device_name,
            created_at=s.created_at.isoformat(),
            last_used_at=s.last_used_at.isoformat() if s.last_used_at else None,
            revoked=s.revoked_at is not None,
        )
        for s in rows
    ]


class RevokeRequest(BaseModel):
    session_id: str


@router.post("/extension/sessions/revoke")
def revoke_session(
    payload: RevokeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    from datetime import UTC, datetime  # noqa: PLC0415

    s = db.execute(
        select(ExtensionSession).where(
            ExtensionSession.id == payload.session_id, ExtensionSession.user_id == user.id
        )
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    if s.revoked_at is None:
        s.revoked_at = datetime.now(UTC)
        db.commit()
    return {"ok": True}


# ─── Extension data (bearer auth) ────────────────────────────────────────────


def _has_done_run(db: Session, user_id: int) -> bool:
    return (
        db.execute(
            select(TailorRun.id).where(
                TailorRun.user_id == user_id,
                TailorRun.status == "done",
                TailorRun.result_json.isnot(None),
            )
        ).first()
        is not None
    )


@router.get("/extension/me")
def extension_me(
    user: User = Depends(get_user_from_extension_token),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "name": user.name,
        "email": user.email,
        "has_active_tailor_run": _has_done_run(db, user.id),
    }


@router.get("/extension/profile")
def extension_profile(
    user: User = Depends(get_user_from_extension_token),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """ONLY the fields needed to fill a form — not the full profile JSON."""
    c = get_candidate(db, user_id=user.id)
    links = c.get("links") or {}
    if isinstance(links, list):  # tolerate the list-of-{label,url} shape
        links = {(i.get("label") or "").lower(): i.get("url") for i in links if isinstance(i, dict)}
    experience = c.get("experience") or []
    current = experience[0] if experience else {}
    return {
        "name": c.get("name") or user.name or "",
        "email": c.get("email") or user.email or "",
        "phone": c.get("phone") or "",
        "location": c.get("location") or "",
        "linkedin": links.get("linkedin") or "",
        "github": links.get("github") or "",
        "portfolio": links.get("website") or links.get("portfolio") or "",
        "work_auth_status": c.get("work_authorization") or c.get("work_auth_status") or "",
        "current_company": current.get("company") or "",
        "current_title": current.get("title") or c.get("headline") or "",
    }


class TailorRunSummary(BaseModel):
    id: str
    job_title: str | None
    company: str | None
    created_at: str


@router.get("/extension/tailor-runs", response_model=list[TailorRunSummary])
def extension_tailor_runs(
    limit: int = Query(default=10, ge=1, le=50),
    user: User = Depends(get_user_from_extension_token),
    db: Session = Depends(get_db),
) -> list[TailorRunSummary]:
    rows = (
        db.execute(
            select(TailorRun)
            .where(
                TailorRun.user_id == user.id,
                TailorRun.status == "done",
                TailorRun.result_json.isnot(None),
            )
            .order_by(TailorRun.started_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    out: list[TailorRunSummary] = []
    for r in rows:
        contact = (
            (r.result_json or {}).get("contact", {}) if isinstance(r.result_json, dict) else {}
        )
        out.append(
            TailorRunSummary(
                id=r.run_id,
                job_title=contact.get("headline") or "Tailored resume",
                company=None,
                created_at=r.started_at.isoformat(),
            )
        )
    return out


def _load_done_run(db: Session, user_id: int, run_id: str) -> TailorRun:
    r = db.execute(
        select(TailorRun).where(TailorRun.run_id == run_id, TailorRun.user_id == user_id)
    ).scalar_one_or_none()
    if r is None or r.status != "done" or r.result_json is None:
        raise HTTPException(status_code=404, detail="completed tailor run not found")
    return r


@router.get("/extension/tailor-runs/{run_id}/resume")
def extension_resume(
    run_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_user_from_extension_token),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _load_done_run(db, user.id, run_id).result_json  # type: ignore[return-value]


@router.get("/extension/tailor-runs/{run_id}/download")
def extension_download(
    run_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_user_from_extension_token),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    r = _load_done_run(db, user.id, run_id)
    resume = TailoredResume.model_validate(r.result_json)
    raw = render_docx(resume, fmt=r.format_selection or "modern", custom=r.custom_options_json)
    return StreamingResponse(
        io.BytesIO(raw),
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": 'attachment; filename="resume.docx"'},
    )


# ─── QA lookup / save (bearer auth) ──────────────────────────────────────────


class LookupRequest(BaseModel):
    question_text: str = Field(min_length=1, max_length=2000)
    field_type: str = "text"
    options: list[str] | None = None


@router.post("/extension/qa/lookup")
def qa_lookup(
    payload: LookupRequest,
    user: User = Depends(get_user_from_extension_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return qa_clustering.lookup(
        db,
        user_id=user.id,
        question_text=payload.question_text,
        field_type=payload.field_type,
        settings=settings,
    )


class SaveRequest(BaseModel):
    question_text: str = Field(min_length=1, max_length=2000)
    answer: str = Field(max_length=4000)
    field_type: str = "text"
    source_ats: str | None = "greenhouse"
    source_url: str | None = None


@router.post("/extension/qa/save")
def qa_save(
    payload: SaveRequest,
    user: User = Depends(get_user_from_extension_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    pair = qa_clustering.save(
        db,
        user_id=user.id,
        question_text=payload.question_text,
        answer=payload.answer,
        field_type=payload.field_type,
        source_ats=payload.source_ats,
        source_url=payload.source_url,
        settings=settings,
    )
    return {"id": pair.id, "canonical_question": pair.question_canonical}


# ─── QA management (cookie auth — /profile "Saved answers") ───────────────────


class QAOut(BaseModel):
    id: str
    question_canonical: str
    answer: str
    field_type: str
    times_used: int
    source_ats: str | None
    updated_at: str


@router.get("/extension/qa/list", response_model=list[QAOut])
def qa_list(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[QAOut]:
    rows = (
        db.execute(
            select(SavedQAPair)
            .where(SavedQAPair.user_id == user.id)
            .order_by(SavedQAPair.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return [
        QAOut(
            id=p.id,
            question_canonical=p.question_canonical,
            answer=p.answer,
            field_type=p.field_type,
            times_used=p.times_used,
            source_ats=p.source_ats,
            updated_at=p.updated_at.isoformat(),
        )
        for p in rows
    ]


class PatchQARequest(BaseModel):
    answer: str | None = Field(default=None, max_length=4000)
    question_canonical: str | None = Field(default=None, max_length=2000)


def _own_pair(db: Session, user_id: int, qa_id: str) -> SavedQAPair:
    p = db.execute(
        select(SavedQAPair).where(SavedQAPair.id == qa_id, SavedQAPair.user_id == user_id)
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="saved answer not found")
    return p


@router.patch("/extension/qa/{qa_id}", response_model=QAOut)
def qa_patch(
    payload: PatchQARequest,
    qa_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QAOut:
    from datetime import UTC, datetime  # noqa: PLC0415

    p = _own_pair(db, user.id, qa_id)
    if payload.answer is not None:
        p.answer = payload.answer
    if payload.question_canonical is not None:
        p.question_canonical = payload.question_canonical
    p.updated_at = datetime.now(UTC)
    db.commit()
    return QAOut(
        id=p.id,
        question_canonical=p.question_canonical,
        answer=p.answer,
        field_type=p.field_type,
        times_used=p.times_used,
        source_ats=p.source_ats,
        updated_at=p.updated_at.isoformat(),
    )


@router.delete("/extension/qa/{qa_id}", status_code=status.HTTP_204_NO_CONTENT)
def qa_delete(
    qa_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    p = _own_pair(db, user.id, qa_id)
    db.delete(p)
    db.commit()


# ─── Analytics opt-in (bearer) ───────────────────────────────────────────────


class SubmittedRequest(BaseModel):
    ats: str = "greenhouse"
    source_url: str | None = None
    tailor_run_id: str | None = None
    num_fields_filled: int = 0


@router.post("/extension/applications-submitted")
def applications_submitted(
    payload: SubmittedRequest,
    user: User = Depends(get_user_from_extension_token),  # noqa: ARG001 — auth only
) -> dict[str, bool]:
    """Privacy: default-off analytics ping. v1.0 acknowledges without storing a
    tracked application (the extension only calls this when the user opted in)."""
    return {"ok": True}
