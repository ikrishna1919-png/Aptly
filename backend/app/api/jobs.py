"""Public read-only job feed.

Always scopes results to the rolling window: `source_updated_at` within
the last `HOURS_WINDOW` hours. Sorted newest-first.

Each `JobOut` carries the sponsorship-intelligence signals attached at
serve time by joining `Job.company` (normalised) against
`employer_sponsorship`. The two signals are surfaced as distinct
fields so the UI can render them as separate badges — collapsing
them would lose the conservative/inclusive distinction the data
warrants.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.employer_sponsorship import DEFAULT_CONSERVATIVE_THRESHOLD
from app.models.job import MANUAL_SOURCE, Job
from app.services.sponsorship import lookup_signals_for_companies

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

    # ── Sponsorship intelligence (DOL H-1B LCA) ────────────────────────────
    # Both signals derive from the public LCA disclosure data and are
    # OPTIONAL — a company with no LCA history gets `False` on both
    # AND no badge in the UI. A False value is NOT a claim that the
    # company doesn't sponsor; it just means there's nothing in the
    # DOL data to assert that it does.
    sponsors_h1b: bool = False
    past_h1b_activity: bool = False
    # Surface the raw counts + most recent filing so the UI can show
    # them in a tooltip — helps a user calibrate the signal.
    lca_count_12mo: int = 0
    lca_count_3yr: int = 0
    most_recent_lca_filing: date | None = None

    model_config = {"from_attributes": True}


class JobsListResponse(BaseModel):
    jobs: list[JobOut]
    total: int
    limit: int
    offset: int
    window_hours: int


def _serialise_with_signals(
    rows: list[Job], db: Session, *, conservative_threshold: int
) -> list[JobOut]:
    """Pivot SQLAlchemy `Job` rows into `JobOut` payloads and attach
    sponsorship signals in one batched lookup. Avoids the N+1 a naive
    `for row in rows: row.signals = lookup(row.company)` would produce."""
    if not rows:
        return []
    company_names = [r.company for r in rows]
    signals_by_company = lookup_signals_for_companies(
        db,
        company_names,
        conservative_threshold=conservative_threshold,
    )

    out: list[JobOut] = []
    for r in rows:
        sig = signals_by_company.get(r.company)
        out.append(
            JobOut(
                id=r.id,
                source=r.source,
                external_id=r.external_id,
                company=r.company,
                title=r.title,
                location=r.location,
                remote=r.remote,
                employment_type=r.employment_type,
                salary=r.salary,
                skills=r.skills,
                sponsors_visa=r.sponsors_visa,
                url=r.url,
                description=r.description,
                posted_at=r.posted_at,
                source_updated_at=r.source_updated_at,
                sponsors_h1b=bool(sig and sig.sponsors_h1b),
                past_h1b_activity=bool(sig and sig.past_h1b_activity),
                lca_count_12mo=int(sig.lca_count_12mo) if sig else 0,
                lca_count_3yr=int(sig.lca_count_3yr) if sig else 0,
                most_recent_lca_filing=sig.most_recent_filing if sig else None,
            )
        )
    return out


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
    sponsors_h1b: bool | None = Query(
        None,
        description=(
            "Conservative H-1B signal: employer filed ≥ N LCAs in the past 12 months. "
            "True returns only matching jobs; False is treated as 'no filter'."
        ),
    ),
    past_h1b_activity: bool | None = Query(
        None,
        description=(
            "Inclusive H-1B signal: employer filed ≥ 1 LCA in the past 3 years. "
            "True returns jobs with any LCA history; False is treated as 'no filter'."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JobsListResponse:
    window_start = datetime.now(UTC) - timedelta(hours=settings.hours_window)
    conservative_threshold = DEFAULT_CONSERVATIVE_THRESHOLD

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

    # Sponsorship filters: only `True` is meaningful — we DO NOT
    # honour `sponsors_h1b=false` as a negative filter, because a
    # missing employer row should NOT be read as "doesn't sponsor"
    # (the DOL data is incomplete and naming mismatches are common).
    # The frontend filter UI only ever sends `true`.
    if sponsors_h1b is True or past_h1b_activity is True:
        # Find normalised employer keys that qualify, then filter
        # `Job.company` by the corresponding company strings via a
        # `normalize(Job.company) IN (subquery)` shape. SQL has no
        # portable string-normalisation function that matches our
        # Python helper, so we do the matching in Python: gather the
        # candidate company strings from the current `base` query,
        # normalise + look up in the sponsorship table, then re-apply
        # `Job.company IN (...)`.
        candidate_rows = db.execute(base.with_only_columns(Job.company).distinct()).all()
        candidate_companies = [row[0] for row in candidate_rows]
        signals_for_filter = lookup_signals_for_companies(
            db,
            candidate_companies,
            conservative_threshold=conservative_threshold,
        )
        keep: set[str] = set()
        for original in candidate_companies:
            sig = signals_for_filter.get(original)
            if sig is None:
                continue
            if sponsors_h1b is True and not sig.sponsors_h1b:
                continue
            if past_h1b_activity is True and not sig.past_h1b_activity:
                continue
            keep.add(original)
        if not keep:
            return JobsListResponse(
                jobs=[],
                total=0,
                limit=limit,
                offset=offset,
                window_hours=settings.hours_window,
            )
        base = base.where(Job.company.in_(keep))

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        db.execute(base.order_by(Job.source_updated_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )

    jobs_out = _serialise_with_signals(
        list(rows), db, conservative_threshold=conservative_threshold
    )
    return JobsListResponse(
        jobs=jobs_out,
        total=int(total),
        limit=limit,
        offset=offset,
        window_hours=settings.hours_window,
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
) -> JobOut:
    """Fetch one job by id. 404 if not found (which includes the case where
    the row has aged out of the rolling window since the link was issued)."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return _serialise_with_signals(
        [job], db, conservative_threshold=DEFAULT_CONSERVATIVE_THRESHOLD
    )[0]
