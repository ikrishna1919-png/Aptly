"""Profile editor endpoints (single-user, admin-token gated).

  GET  /api/admin/profile          → load the current profile
  PUT  /api/admin/profile          → save the profile (full replacement)
  POST /api/admin/profile/parse    → parse pasted resume text into the
                                     profile shape (does NOT save — the
                                     UI shows the result for review first)

The profile lives in `candidates.profile` (slug='demo'). The tailoring
service reads from this same row via `get_candidate(db)`, so any save
here changes the candidate fingerprint and invalidates the analyze cache
naturally.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin import _require_admin
from app.config import Settings, get_settings
from app.database import get_db
from app.models.candidate import DEMO_SLUG, Candidate
from app.services.demo_candidate import DEMO_CANDIDATE
from app.services.profile_parser import (
    Profile,
    ResumeParseConfigError,
    ResumeParseConnectionError,
    ResumeParseError,
    ResumeParseTimeoutError,
    parse_resume,
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


# ── Parse ───────────────────────────────────────────────────────────────────


class ParseRequest(BaseModel):
    text: str = Field(min_length=1, description="Raw resume text to parse.")


@router.post("/admin/profile/parse", response_model=Profile)
def parse_profile_text(
    payload: ParseRequest,
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> Profile:
    """Send the pasted text to Claude and return the parsed Profile shape.

    Does NOT save — the UI shows the result for the user to review and
    edit before they hit Save (which is the PUT above).
    """
    _require_admin(settings, x_admin_token)
    try:
        return parse_resume(payload.text, settings=settings)
    except ResumeParseConfigError as e:
        # Missing API key, empty input — caller-fixable config issue.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except ResumeParseTimeoutError as e:
        # We bailed before the upstream proxy could 502 us. Surfaces a
        # readable message instead of a generic 504.
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(e)) from e
    except ResumeParseConnectionError as e:
        # We never got bytes from Anthropic — DNS / TLS / dropped conn.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except ResumeParseError as e:
        # Catch-all (bad JSON, generic API error). 502 is the right shape
        # — it's an upstream failure, not ours.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
