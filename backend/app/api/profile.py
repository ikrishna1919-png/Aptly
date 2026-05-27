"""Profile editor endpoints (single-user, admin-token gated).

  GET  /api/admin/profile                  → load the current profile
  PUT  /api/admin/profile                  → save the profile (full replacement)
  POST /api/admin/profile/parse            → kick off a background parse,
                                             return 202 + a `run_id`
  GET  /api/admin/profile/parse/{run_id}   → poll the parse run's status

The profile lives in `candidates.profile` (slug='demo'). The tailoring
service reads from this same row via `get_candidate(db)`, so any save
here changes the candidate fingerprint and invalidates the analyze cache
naturally.

Parse runs in the background because Anthropic can take longer than
Render's free-tier 100s request budget — same pattern the ingest
admin endpoint uses. The frontend kicks off, then polls until the
row settles at `success` or `failed`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin import _require_admin
from app.config import Settings, get_settings
from app.database import get_db
from app.models.candidate import DEMO_SLUG, Candidate
from app.models.parse_run import ParseRun
from app.services.demo_candidate import DEMO_CANDIDATE
from app.services.profile_parser import (
    Profile,
    ResumeParseConfigError,
    start_background_parse,
)

router = APIRouter()


# ── Load / save ─────────────────────────────────────────────────────────────


def _load_or_seed(db: Session) -> Candidate:
    """Return the demo candidate row, seeding from the in-code fallback
    when it doesn't exist (test DBs that skip the migration)."""
    row = db.query(Candidate).filter(Candidate.slug == DEMO_SLUG).one_or_none()
    if row is None:
        row = Candidate(slug=DEMO_SLUG, profile=dict(DEMO_CANDIDATE))
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("/admin/profile", response_model=Profile)
def get_profile(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> Profile:
    _require_admin(settings, x_admin_token)
    row = _load_or_seed(db)
    return Profile.model_validate(row.profile)


@router.put("/admin/profile", response_model=Profile)
def update_profile(
    payload: Profile,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> Profile:
    """Replace the saved profile with the payload. No diffing — the form
    sends the whole shape every save."""
    _require_admin(settings, x_admin_token)
    row = _load_or_seed(db)
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
    "/admin/profile/parse",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ParseStartResponse,
)
def parse_profile_text(
    payload: ParseRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> ParseStartResponse:
    """Kick off a resume parse and return immediately with a `run_id`.

    The actual Claude call runs in a background thread; the frontend
    polls `GET /admin/profile/parse/{run_id}` until the row settles
    at `status='success'` (with `profile`) or `status='failed'` (with
    `error`). Same pattern as the admin ingest endpoint — the
    long-running call can't time out the HTTP request anymore because
    the HTTP request doesn't wait for it.
    """
    _require_admin(settings, x_admin_token)
    try:
        run_id = start_background_parse(payload.text, settings)
    except ResumeParseConfigError as e:
        # Caller-fixable: missing API key, empty input. Surface
        # synchronously — no point creating a ParseRun row for an
        # input we can reject upfront.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e

    status_url = f"/api/admin/profile/parse/{run_id}"
    response.headers["Location"] = status_url
    return ParseStartResponse(run_id=run_id, status_url=status_url)


@router.get("/admin/profile/parse/{run_id}", response_model=ParseRunOut)
def get_parse_run(
    run_id: str = Path(..., min_length=1, max_length=64),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> ParseRunOut:
    """Return the current state of the parse run. The frontend polls
    this until `status` is `success` or `failed`."""
    _require_admin(settings, x_admin_token)
    run = db.execute(select(ParseRun).where(ParseRun.run_id == run_id)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="parse run not found")
    # Validate the stored JSON back into a Profile so the response
    # carries the typed shape (and any missing-field defaults the
    # Pydantic model fills in).
    profile: Profile | None = None
    if run.profile is not None:
        try:
            profile = Profile.model_validate(run.profile)
        except Exception:  # noqa: BLE001
            # A row whose stored JSON doesn't validate is treated as a
            # bug, not a 500 — surface as `failed` with a clear message
            # so the frontend can prompt the user to retry.
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
