"""Active autofill resume pointer.

Stores WHICH completed tailor run the Chrome extension should fill with when no
explicit run is selected. Kept in the candidate's `profile` JSON (key
`active_autofill_run_id`) so this needs NO migration — same JSON-pointer pattern
as `default_resume_format`. Setting it does NOT push a file into the browser; it
just records the pointer the extension reads.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.candidate import DEMO_SLUG, Candidate
from app.models.tailor_run import TailorRun
from app.services.demo_candidate import get_candidate

_KEY = "active_autofill_run_id"


def _candidate_row(db: Session, user_id: int | None) -> Candidate | None:
    if user_id is not None:
        row = db.execute(select(Candidate).where(Candidate.user_id == user_id)).scalar_one_or_none()
        if row is not None:
            return row
    return db.execute(select(Candidate).where(Candidate.slug == DEMO_SLUG)).scalar_one_or_none()


def set_active_run(db: Session, user_id: int | None, run_id: str) -> None:
    """Mark `run_id` as the user's active autofill run. Verifies the run is the
    user's own COMPLETED tailor run first (raises ValueError otherwise). Creates
    the per-user candidate row from the demo seed if it doesn't exist yet (same
    as the profile/default-format editors)."""
    run = db.execute(
        select(TailorRun).where(TailorRun.run_id == run_id, TailorRun.user_id == user_id)
    ).scalar_one_or_none()
    if run is None or run.status != "done" or run.result_json is None:
        raise ValueError("completed tailor run not found")

    row = _candidate_row(db, user_id)
    if row is None:
        base = get_candidate(db, user_id=user_id)
        row = Candidate(slug=f"user-{user_id}", user_id=user_id, profile=base)
        db.add(row)
    profile = dict(row.profile or {})
    profile[_KEY] = run_id
    row.profile = profile
    flag_modified(row, "profile")  # JSON in-place mutation needs an explicit dirty flag
    db.commit()


def get_active_run_id(db: Session, user_id: int | None) -> str | None:
    """The user's active autofill run id, or None. Self-heals: if the pointed-to
    run no longer exists / isn't a completed run of this user, returns None
    (the extension then falls back to the most recent run)."""
    row = _candidate_row(db, user_id)
    if row is None:
        return None
    run_id = (row.profile or {}).get(_KEY)
    if not run_id:
        return None
    run = db.execute(
        select(TailorRun).where(TailorRun.run_id == run_id, TailorRun.user_id == user_id)
    ).scalar_one_or_none()
    if run is None or run.status != "done" or run.result_json is None:
        return None
    return run_id
