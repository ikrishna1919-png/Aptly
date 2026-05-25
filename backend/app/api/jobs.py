"""Public read-only job feed.

Always scopes results to the rolling window: `source_updated_at` within
the last `HOURS_WINDOW` hours. Sorted newest-first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.job import MANUAL_SOURCE, Job

router = APIRouter()


class JobOut(BaseModel):
    id: int
    source: str
    external_id: str
    company: str
    title: str
    location: str | None
    remote: bool | None
    employment_type: str | None
    salary: str | None
    skills: list[str]
    sponsors_visa: bool | None
    url: str
    description: str | None
    posted_at: datetime | None
    source_updated_at: datetime | None

    model_config = {"from_attributes": True}


class JobsListResponse(BaseModel):
    jobs: list[JobOut]
    total: int
    limit: int
    offset: int
    window_hours: int


@router.get("/jobs", response_model=JobsListResponse)
def list_jobs(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    q: str | None = Query(None, description="Free-text search over title + company"),
    company: str | None = None,
    location: str | None = None,
    remote: bool | None = None,
    employment_type: str | None = None,
    sponsors_visa: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JobsListResponse:
    window_start = datetime.now(UTC) - timedelta(hours=settings.hours_window)

    # Manual jobs always appear in the feed regardless of window — they're
    # human-curated and explicitly persisted.
    from sqlalchemy import or_

    base = select(Job).where(
        or_(Job.source_updated_at >= window_start, Job.source == MANUAL_SOURCE)
    )
    if q:
        pattern = f"%{q.lower()}%"
        base = base.where(
            func.lower(Job.title).like(pattern) | func.lower(Job.company).like(pattern)
        )
    if company:
        base = base.where(func.lower(Job.company) == company.lower())
    if location:
        base = base.where(func.lower(Job.location).like(f"%{location.lower()}%"))
    if remote is not None:
        base = base.where(Job.remote.is_(remote))
    if employment_type:
        base = base.where(func.lower(Job.employment_type) == employment_type.lower())
    if sponsors_visa is not None:
        base = base.where(Job.sponsors_visa.is_(sponsors_visa))

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        db.execute(base.order_by(Job.source_updated_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )

    return JobsListResponse(
        jobs=[JobOut.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
        window_hours=settings.hours_window,
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
) -> Job:
    """Fetch one job by id. 404 if not found (which includes the case where
    the row has aged out of the rolling window since the link was issued)."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return job
