"""Admin endpoints — protected by a shared token.

- POST /admin/ingest        — run the ATS ingest + rolling-window cleanup.
- POST /admin/jobs          — add a manual job (persists indefinitely).
- DELETE /admin/jobs/{id}   — remove a manual job.

The same scheduled GitHub Actions workflow calls /admin/ingest every 6h.
The manual-jobs endpoints back the /admin frontend page.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.jobs import JobOut
from app.config import Settings, get_settings
from app.database import get_db
from app.models.job import MANUAL_SOURCE, Job
from app.services.ingest import run_ingest

router = APIRouter()


def _require_admin(settings: Settings, token: str | None) -> None:
    expected = settings.admin_token
    if not expected:
        # If no admin token is configured, the endpoint is locked shut —
        # never serve an unprotected admin call.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin endpoint disabled: ADMIN_TOKEN is not configured",
        )
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


@router.post("/admin/ingest")
def admin_ingest(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    _require_admin(settings, x_admin_token)
    stats = run_ingest(db, settings)
    return stats.to_dict()


class ManualJobIn(BaseModel):
    """Payload for POST /admin/jobs. `source` is always coerced to "manual";
    the field is accepted for forward-compat but ignored."""

    title: str = Field(min_length=1, max_length=512)
    company: str = Field(min_length=1, max_length=256)
    apply_url: str = Field(min_length=1, description="URL the candidate applies at")
    location: str | None = Field(default=None, max_length=256)
    remote: bool | None = None
    employment_type: str | None = Field(default=None, max_length=64)
    salary: str | None = Field(default=None, max_length=128)
    skills: list[str] = Field(default_factory=list)
    sponsors_visa: bool | None = None
    description: str | None = None
    source: str | None = None  # ignored — always stored as MANUAL_SOURCE


@router.post("/admin/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_manual_job(
    payload: ManualJobIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> Job:
    _require_admin(settings, x_admin_token)

    now = datetime.now(UTC)
    job = Job(
        source=MANUAL_SOURCE,
        # Unique within source="manual"; satisfies the (source, external_id)
        # uniqueness constraint without colliding with ATS-ingested rows.
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        company=payload.company.strip(),
        title=payload.title.strip(),
        location=payload.location,
        remote=payload.remote,
        employment_type=payload.employment_type,
        salary=payload.salary,
        skills=list(payload.skills),
        sponsors_visa=payload.sponsors_visa,
        url=payload.apply_url,
        description=payload.description,
        posted_at=now,
        # source_updated_at is set so manual rows sort newest-first alongside
        # ATS-ingested ones. The rolling cleanup is gated on `source != "manual"`,
        # so this timestamp aging doesn't expose them to deletion.
        source_updated_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.delete("/admin/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_manual_job(
    job_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    _require_admin(settings, x_admin_token)
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    # Restrict deletes to manual rows so this endpoint can't be used to
    # surgically remove ATS-ingested postings (those churn on their own).
    if job.source != MANUAL_SOURCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="can only delete manual jobs via this endpoint",
        )
    db.delete(job)
    db.commit()
