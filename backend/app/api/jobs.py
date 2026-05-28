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

import re
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

# `posted_within` window → hours. "any" (or unknown) means no filter.
_POSTED_WITHIN_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}

# Heuristic, JD-text regexes computed at query time (no stored column).
# `_HYBRID_RE` derives the "hybrid" work model from the JD/location text.
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
# `_ADVANCED_DEGREE_REQUIRED_RE` flags a JD that REQUIRES a master's/PhD
# (not merely "preferred" — a preferred advanced degree is still
# bachelor's-friendly). Two orderings: "<degree> ... required" and
# "require(s) ... <degree>", within a short same-clause window. This is
# deliberately conservative and admittedly imperfect; the UI tooltip says
# so. Used to compute the `bachelors_friendly` filter on the fly.
_ADV_DEGREE = r"(master'?s?|m\.?s\.?|m\.eng|ph\.?\s?d\.?|doctorate|graduate degree)"
_REQUIRE = r"(required|must have|minimum)"
# Tempered gap: don't match ACROSS a "preferred" or "not" between the degree
# and the requirement word, so "PhD preferred but not required" is NOT read
# as a hard requirement (it's still bachelor's-friendly).
_GAP = r"(?:(?!\bpreferred\b|\bnot\b)[^.\n]){0,60}?"
_ADVANCED_DEGREE_REQUIRED_RE = re.compile(
    rf"\b{_ADV_DEGREE}\b{_GAP}\b{_REQUIRE}\b" rf"|\b{_REQUIRE}\b{_GAP}\b{_ADV_DEGREE}\b",
    re.IGNORECASE,
)


def _derive_work_model(job: Job) -> str | None:
    """Display work model derived at serve time (no stored column).
    JD/location mentioning "hybrid" wins; else fall back to the `remote`
    boolean. `None` when we genuinely can't tell."""
    haystack = " ".join(filter(None, (job.location, job.title, job.description or "")))
    if _HYBRID_RE.search(haystack):
        return "hybrid"
    if job.remote is True:
        return "remote"
    if job.remote is False:
        return "onsite"
    return None


def _jd_requires_advanced_degree(job: Job) -> bool:
    """True when the JD appears to REQUIRE a master's/PhD. Heuristic."""
    return bool(job.description and _ADVANCED_DEGREE_REQUIRED_RE.search(job.description))


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

    # Derived at serve time from `remote` + a JD "hybrid" heuristic — there
    # is no stored work_model column. "remote" | "hybrid" | "onsite" | None.
    work_model: str | None = None

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
    # Page-based view of the same pagination cursor — derived from
    # `offset` / `limit` so callers that prefer page numbers don't
    # have to recompute. `page` is 1-indexed; `total_pages` is
    # `ceil(total / limit)`.
    page: int
    total_pages: int
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
                work_model=_derive_work_model(r),
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
    work_model: str | None = Query(
        None,
        description='Work model filter: "remote" | "hybrid" | "onsite". "any"/None = no filter.',
    ),
    posted_within: str | None = Query(
        None,
        description='Recency window: "24h" | "7d" | "30d". "any"/None = no filter.',
    ),
    bachelors_friendly: bool | None = Query(
        None,
        description=(
            "When true, exclude jobs whose JD appears to REQUIRE a master's/PhD "
            "(heuristic regex on the description; may not catch every case)."
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

    # Work model is filtered in Python (below) against the SAME derived value
    # the UI shows, so the filter and the badge never disagree — e.g. a job
    # whose JD says "hybrid" but whose `remote` flag is False must NOT match
    # the "onsite" filter.
    wm = (work_model or "").strip().lower()

    # Recency window — filter on the freshest of posted_at / source_updated_at.
    posted_hours = _POSTED_WITHIN_HOURS.get((posted_within or "").strip().lower())
    if posted_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=posted_hours)
        base = base.where(func.coalesce(Job.posted_at, Job.source_updated_at) >= cutoff)

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
                page=1,
                total_pages=0,
                window_hours=settings.hours_window,
            )
        base = base.where(Job.company.in_(keep))

    # Stable, newest-first ordering with `id` as a deterministic tiebreaker
    # so pagination never reshuffles rows that share a timestamp.
    ordered = base.order_by(Job.source_updated_at.desc(), Job.id.desc())

    # JD-text filters ("hybrid" work model, bachelor's-friendly) can't be
    # expressed in portable SQL, so when either is active we materialise the
    # windowed rows, filter in Python, then paginate the filtered list — that
    # keeps `total`/`page` correct. Otherwise we let the DB do count + slice.
    needs_python_filter = wm in {"remote", "hybrid", "onsite"} or bachelors_friendly is True
    if needs_python_filter:
        all_rows = list(db.execute(ordered).scalars().all())

        def _passes(job: Job) -> bool:
            if wm in {"remote", "hybrid", "onsite"} and _derive_work_model(job) != wm:
                return False
            if bachelors_friendly is True and _jd_requires_advanced_degree(job):
                return False
            return True

        filtered = [r for r in all_rows if _passes(r)]
        total = len(filtered)
        rows = filtered[offset : offset + limit]
    else:
        total = int(db.execute(select(func.count()).select_from(base.subquery())).scalar_one())
        rows = list(db.execute(ordered.limit(limit).offset(offset)).scalars().all())

    jobs_out = _serialise_with_signals(
        list(rows), db, conservative_threshold=conservative_threshold
    )
    # Page math: `page` is 1-indexed; `total_pages` is `ceil(total /
    # limit)`. When the filter set is empty (total=0) we surface
    # `total_pages=0` rather than 1 so the pagination control can
    # hide itself cleanly.
    page = (offset // limit) + 1 if limit > 0 else 1
    total_pages = (total + limit - 1) // limit if limit > 0 else 0
    return JobsListResponse(
        jobs=jobs_out,
        total=total,
        limit=limit,
        offset=offset,
        page=page,
        total_pages=total_pages,
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
