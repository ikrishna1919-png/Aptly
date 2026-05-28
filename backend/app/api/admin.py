"""Admin endpoints — protected by a shared token.

- POST /admin/ingest             — KICK OFF an ATS ingest + cleanup run.
                                   Returns 202 + a `run_id` immediately;
                                   the work continues in a background
                                   thread.
- GET  /admin/ingest             — Latest ingest run (running, success,
                                   or failed) with its full stats.
- GET  /admin/ingest/{run_id}    — That specific run.
- POST /admin/jobs               — Add a manual job (persists indefinitely).
- DELETE /admin/jobs/{id}        — Remove a manual job.

The scheduled GitHub Actions workflow POSTs /admin/ingest every 6h and
treats the 202 as success — no more 502s on long runs.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.api.jobs import JobOut
from app.config import Settings, get_settings
from app.database import get_db
from app.models.ingest_run import IngestRun
from app.models.job import MANUAL_SOURCE, Job
from app.models.user import User
from app.services.ingest import start_background_ingest

router = APIRouter()


def _require_admin(settings: Settings, token: str | None) -> None:
    """Token-gated path — used by the scheduled GitHub Actions
    cron that posts `/admin/ingest`. Separate from the user-facing
    admin gate below (`require_admin_user`) which protects the
    manual-entry endpoints humans call from the UI.

    Two-gate design rationale: cron has no user identity to attach
    to; humans must not be able to bypass the email allowlist by
    discovering / leaking the cron token. Keeping the gates
    distinct means a token leak doesn't unlock manual-entry, and a
    non-admin user account can't trigger an ingest run.
    """
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


def require_admin_user(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> User:
    """FastAPI dependency for the human-facing admin endpoints.

    Verifies that:
      1. There IS a signed-in user (`get_current_user` already 401s
         when not — the dependency above does the heavy lifting).
      2. The user's email is in the `ADMIN_EMAILS` allowlist. 403
         otherwise — non-admin users see exactly the same error
         whether the endpoint exists or not, no enumeration.

    Defaults closed: empty `ADMIN_EMAILS` means every user 403s.
    Hide the UI on the frontend too (cosmetic), but rely on this
    dependency for the actual access control. Anyone can hit the
    endpoint directly — the gate has to live on the server.
    """
    if not settings.is_admin_email(user.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admins only")
    return user


class IngestStartResponse(BaseModel):
    run_id: str
    status: str = Field(default="running")
    status_url: str = Field(description="Poll this for completion. Final status comes via GET.")


class IngestRunOut(BaseModel):
    run_id: str
    status: str
    stats: dict
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


@router.post(
    "/admin/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestStartResponse,
)
def admin_ingest(
    response: Response,
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> IngestStartResponse:
    """Kick off an ingest pass and return immediately.

    Ingestion can take 30s–several minutes depending on the number of
    boards and how the upstream ATSes feel that day, well over the
    Render free-tier 100-second request budget. So we run it in a
    background thread and let the caller poll
    `GET /api/admin/ingest/{run_id}` for completion.
    """
    _require_admin(settings, x_admin_token)
    run_id = start_background_ingest(settings)
    status_url = f"/api/admin/ingest/{run_id}"
    response.headers["Location"] = status_url
    return IngestStartResponse(run_id=run_id, status_url=status_url)


@router.get("/admin/ingest", response_model=IngestRunOut)
def latest_ingest_run(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> IngestRun:
    """Return the most recently started ingest run (any status)."""
    _require_admin(settings, x_admin_token)
    run = db.execute(
        select(IngestRun).order_by(IngestRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no ingest runs yet")
    return run


@router.get("/admin/ingest/{run_id}", response_model=IngestRunOut)
def get_ingest_run(
    run_id: str = Path(..., min_length=1, max_length=64),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> IngestRun:
    _require_admin(settings, x_admin_token)
    run = db.execute(select(IngestRun).where(IngestRun.run_id == run_id)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ingest run not found")
    return run


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
    # Gated on the user's email being in `ADMIN_EMAILS`, NOT on
    # the cron token — see `require_admin_user` for the rationale.
    _admin: User = Depends(require_admin_user),
) -> Job:
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
    _admin: User = Depends(require_admin_user),
) -> None:
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
