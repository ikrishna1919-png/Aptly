"""User default-format storage + the AI-chooses heuristic (Feature #1 / #5).

Defaults live on the candidate row (`default_resume_format` /
`default_cover_letter_format`) as `{"format": name, "custom": {...}|null}`.
Unset → falls back to "modern" (resume) / "traditional" (cover) so existing
users are never blocked. The "AI chooses" path is a transparent HEURISTIC
(seniority/industry signals), NOT an LLM call.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import DEMO_SLUG, Candidate
from app.services.demo_candidate import get_candidate

_RESUME_FALLBACK = {"format": "modern", "custom": None}
_COVER_FALLBACK = {"format": "traditional", "custom": None}

_SENIOR_HINTS = ("senior", "staff", "principal", "lead", "head", "director", "vp", "chief")
_ACADEMIC_HINTS = ("phd", "professor", "researcher", "postdoc", "lecturer", "research scientist")


def _column(kind: str) -> str:
    return "default_cover_letter_format" if kind == "cover" else "default_resume_format"


def resolve_default(db: Session, user_id: int | None, kind: str) -> dict[str, Any]:
    """Return the user's saved default for `kind` ('resume'|'cover'), or the
    fallback when unset."""
    fallback = _COVER_FALLBACK if kind == "cover" else _RESUME_FALLBACK
    row = _candidate_row(db, user_id)
    if row is None:
        return dict(fallback)
    value = getattr(row, _column(kind), None)
    if isinstance(value, dict) and value.get("format"):
        return value
    return dict(fallback)


def save_default(
    db: Session, user_id: int | None, kind: str, value: dict[str, Any]
) -> dict[str, Any]:
    """Persist the user's default for `kind`. Creates the candidate row if the
    user only has the demo fallback so far."""
    row = _candidate_row(db, user_id)
    if row is None:
        # No per-user row yet — clone from demo so we don't mutate the shared
        # demo profile. (Matches how the profile editor seeds new users.)
        base = get_candidate(db, user_id=user_id)
        row = Candidate(slug=f"user-{user_id}", user_id=user_id, profile=base)
        db.add(row)
    setattr(row, _column(kind), value)
    db.commit()
    return value


def ai_choose_resume_format(profile: dict[str, Any]) -> dict[str, Any]:
    """Heuristic: academic signals → Classic (serif); senior → Modern; else
    Minimal. Deterministic, explainable, no LLM."""
    blob = _profile_text(profile)
    if any(h in blob for h in _ACADEMIC_HINTS):
        return {"format": "classic", "custom": None, "reason": "academic background"}
    if any(h in blob for h in _SENIOR_HINTS):
        return {"format": "modern", "custom": None, "reason": "senior experience level"}
    return {"format": "minimal", "custom": None, "reason": "clean, early-career fit"}


def ai_choose_cover_format(profile: dict[str, Any], tone: str | None = None) -> dict[str, Any]:
    """Heuristic for cover letters: academic/formal → Traditional; otherwise
    Modern. `tone` (if the user expressed one) nudges warm → Modern."""
    blob = _profile_text(profile)
    if (tone or "").lower() == "warm":
        return {"format": "modern", "custom": None, "reason": "warm tone"}
    if any(h in blob for h in _ACADEMIC_HINTS):
        return {"format": "traditional", "custom": None, "reason": "academic/formal background"}
    return {"format": "modern", "custom": None, "reason": "modern professional default"}


def match_uploaded_format(*, serif: bool, has_color: bool, dense: bool) -> dict[str, Any]:
    """Map simple style signals (from a DOCX/PDF heuristic analysis) to the
    closest pre-built. Used by the PDF 'closest match' path."""
    if serif:
        return {"format": "classic", "custom": None, "reason": "serif, traditional layout"}
    if has_color:
        return {"format": "modern", "custom": None, "reason": "sans with accent color"}
    if not dense:
        return {"format": "minimal", "custom": None, "reason": "airy, low-density layout"}
    return {"format": "plain", "custom": None, "reason": "dense, unstyled"}


def _candidate_row(db: Session, user_id: int | None) -> Candidate | None:
    if user_id is not None:
        row = db.execute(select(Candidate).where(Candidate.user_id == user_id)).scalar_one_or_none()
        if row is not None:
            return row
    return db.execute(select(Candidate).where(Candidate.slug == DEMO_SLUG)).scalar_one_or_none()


def _profile_text(profile: dict[str, Any]) -> str:
    parts = [str(profile.get("headline", "")), str(profile.get("summary", ""))]
    for e in profile.get("experience") or []:
        if isinstance(e, dict):
            parts.append(str(e.get("title", "")))
    return " ".join(parts).lower()
