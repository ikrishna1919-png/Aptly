"""Public read-only job feed.

Always scopes results to the rolling window: `source_updated_at` within
the last `HOURS_WINDOW` hours. Sorted newest-first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.job import Job

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

    base = select(Job).where(Job.source_updated_at >= window_start)
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
