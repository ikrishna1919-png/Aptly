"""Profile editor endpoints — Phase 5 per-user, session-authenticated.

  GET  /api/profile                       → load the current user's profile
  PUT  /api/profile                       → save the current user's profile
  POST /api/profile/parse                 → kick off a background parse
                                            from pasted text (202 + run_id)
  POST /api/profile/parse/upload          → kick off a background parse
                                            from a PDF / DOCX upload
                                            (202 + run_id). Returns 400 on
                                            unsupported type, 413 on
                                            oversized file, 422 when the
                                            file has no text layer.
  GET  /api/profile/parse/{run_id}        → poll the parse run's status

Each user has at most one Candidate row (unique on `user_id`). Reads
seed from `DEMO_CANDIDATE` when the row doesn't yet exist — that's
the first-load shape on a fresh sign-up so the editor isn't empty.
The tailoring service consumes the same row via `get_candidate(db,
user.id)`.

Routes are NO LONGER admin-token-gated — they require a Google
session via `get_current_user`. The admin token still gates the
operator endpoints under `/api/admin/*` (ingest, manual jobs).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, Path, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models.candidate import DEMO_SLUG, Candidate
from app.models.parse_run import ParseRun
from app.models.user import User
from app.services.demo_candidate import DEMO_CANDIDATE
from app.services.profile_parser import (
    Profile,
    start_background_parse,
    start_background_parse_pdf,
)
from app.services.resume_extractor import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_SUFFIXES,
    EmptyExtractionError,
    extract_text,
)

router = APIRouter()
log = logging.getLogger(__name__)


# ── Load / save ─────────────────────────────────────────────────────────────


def _load_or_seed(db: Session, user: User) -> Candidate:
    """Return the user's Candidate row, seeding from the in-code
    `DEMO_CANDIDATE` template when it doesn't yet exist. The seeded
    row carries the user's id + a per-user slug so the legacy
    `(slug)` unique constraint stays satisfied alongside the new
    `(user_id)` uniqueness."""
    row = db.execute(select(Candidate).where(Candidate.user_id == user.id)).scalar_one_or_none()
    if row is None:
        row = Candidate(
            user_id=user.id,
            # Per-user slug so two users seeded from the demo don't
            # collide on the legacy `slug` unique constraint.
            slug=f"{DEMO_SLUG}-u{user.id}",
            profile=dict(DEMO_CANDIDATE),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("/profile", response_model=Profile)
def get_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Profile:
    row = _load_or_seed(db, user)
    return Profile.model_validate(row.profile)


@router.put("/profile", response_model=Profile)
def update_profile(
    payload: Profile,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Profile:
    """Replace the saved profile with the payload. No diffing — the
    form sends the whole shape every save."""
    row = _load_or_seed(db, user)
    row.profile = payload.model_dump(mode="json")
    db.commit()
    db.refresh(row)
    return Profile.model_validate(row.profile)


# ── Parse (background job) ──────────────────────────────────────────────────


class ParseRequest(BaseModel):
    text: str = Field(min_length=1, description="Raw resume text to parse.")


class ParseStartResponse(BaseModel):
    run_id: str
    status: str = Field(default="running")
    status_url: str = Field(description="Poll this for completion.")


class ParseRunOut(BaseModel):
    run_id: str
    status: str = Field(description="running | success | failed")
    profile: Profile | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


@router.post(
    "/profile/parse",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ParseStartResponse,
)
def parse_profile_text(
    payload: ParseRequest,
    response: Response,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ParseStartResponse:
    """Kick off a resume parse and return immediately with a `run_id`.

    The parse is hybrid: regex for deterministic contact fields, Claude
    for structural ones (name, experience, education, skills). The
    background-job + polling shape stays so the frontend code path is
    unchanged. `settings` is captured at request time and forwarded to
    the worker so the LLM call uses the request-scoped configuration."""
    run_id = start_background_parse(payload.text, user_id=user.id, settings=settings)
    status_url = f"/api/profile/parse/{run_id}"
    response.headers["Location"] = status_url
    return ParseStartResponse(run_id=run_id, status_url=status_url)


@router.post(
    "/profile/parse/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ParseStartResponse,
)
async def parse_profile_upload(
    response: Response,
    file: UploadFile = File(..., description="PDF or DOCX resume."),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ParseStartResponse:
    """Accept a PDF or DOCX resume and kick off a background parse.

    Two routing paths, picked by extension:

    * `.pdf` → bytes are handed straight to Claude as a `document`
      content block. We deliberately SKIP the pdfplumber text
      extract here; Claude's document understanding handles
      bulleted lists, multi-column layouts, and table-styled
      contact rows far more reliably than text we'd lift out
      up-front. pdfplumber still runs inside the worker for the
      regex contact-field pass — see `parse_resume_pdf`.
    * `.docx` → run the (improved) DOCX text extractor first,
      which walks headers/footers, all paragraphs (with list
      markers preserved), tables, and text boxes, then hand the
      assembled text to the existing text-input worker. The
      `.docx → PDF` conversion path was considered but `python-docx`
      gives us cell-level access cleanly; the convert step would
      add a pandoc / LibreOffice dependency without buying
      anything we don't already get here.

    Failure surfaces:
      * 400 — extension isn't `.pdf` or `.docx`, or the file is
              corrupt / unreadable.
      * 413 — file is larger than `MAX_UPLOAD_BYTES`.
      * 422 — file parsed cleanly but produced no text (almost always
              a scanned / image-only PDF whose direct-to-LLM upload
              would also fail to surface anything useful). The
              frontend turns this into a "paste your text instead"
              message.
    """
    # Read bytes with a hard cap. `UploadFile.read(size)` returns up
    # to `size` bytes; if we got the cap we read one more byte to
    # confirm the file is genuinely oversized before 413ing.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Resume too large. Maximum size is " f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            ),
        )

    filename = file.filename or ""
    suffix = PurePosixPath(filename).suffix.lower()

    if suffix == ".pdf":
        if not data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Uploaded PDF is empty.",
            )
        # Cheap structural sanity-check at the API edge — real PDFs
        # start with the `%PDF-` magic. We do this here so an
        # obvious mistake (a text file renamed `.pdf`, a corrupted
        # download) fails fast with a clear 400 instead of burning
        # an Anthropic call inside the worker and surfacing the
        # error via the polling path.
        if not data.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Couldn't read PDF: file is not a PDF.",
            )
        # Direct-to-LLM path: hand the PDF bytes to the worker.
        # `parse_resume_pdf` runs pdfplumber for the regex contact
        # pass and Anthropic for the structural extract — the
        # bytes never round-trip through a separate text extractor.
        run_id = start_background_parse_pdf(data, user_id=user.id, settings=settings)
        status_url = f"/api/profile/parse/{run_id}"
        response.headers["Location"] = status_url
        return ParseStartResponse(run_id=run_id, status_url=status_url)

    if suffix == ".docx":
        try:
            text = extract_text(filename, data)
        except EmptyExtractionError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            ) from e
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e) or "Couldn't read uploaded file.",
            ) from e
        # Sanity log so an unexpectedly short extract (template that
        # parks all content somewhere the walker missed) is
        # detectable from the Render logs.
        log.info(
            "profile/parse/upload: DOCX extracted %d chars from %s",
            len(text),
            filename,
        )
        run_id = start_background_parse(text, user_id=user.id, settings=settings)
        status_url = f"/api/profile/parse/{run_id}"
        response.headers["Location"] = status_url
        return ParseStartResponse(run_id=run_id, status_url=status_url)

    # Anything else: clear, immediate 400.
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(f"Unsupported file type {suffix!r}. Allowed: " f"{', '.join(SUPPORTED_SUFFIXES)}."),
    )


# Re-exported for the OpenAPI docs / frontend constants.
SUPPORTED_RESUME_SUFFIXES = SUPPORTED_SUFFIXES
MAX_RESUME_UPLOAD_BYTES = MAX_UPLOAD_BYTES


@router.get("/profile/parse/{run_id}", response_model=ParseRunOut)
def get_parse_run(
    run_id: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ParseRunOut:
    """Return the current state of the parse run. The query filters
    on `user_id` AND `run_id` so guessing another user's `run_id`
    can't leak their parsed profile."""
    run = db.execute(
        select(ParseRun).where(ParseRun.run_id == run_id, ParseRun.user_id == user.id)
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="parse run not found")
    profile: Profile | None = None
    if run.profile is not None:
        try:
            profile = Profile.model_validate(run.profile)
        except Exception:  # noqa: BLE001
            return ParseRunOut(
                run_id=run.run_id,
                status="failed",
                profile=None,
                error="Parsed profile failed validation — retry the parse.",
                started_at=run.started_at,
                finished_at=run.finished_at,
            )
    return ParseRunOut(
        run_id=run.run_id,
        status=run.status,
        profile=profile,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )
