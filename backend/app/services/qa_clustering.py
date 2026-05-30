"""Semantic clustering + the learning loop for saved application answers.

`lookup` resolves an incoming form question to a saved answer through three
escalating tiers (exact → fuzzy → semantic LLM), and `save` clusters a new
answer onto an existing canonical or creates a new one. Inverse questions
("authorized to work WITHOUT sponsorship?" vs "need sponsorship?") are
detected by the LLM and the boolean answer is flipped — never for free text.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.saved_qa_pair import SavedQAPair
from app.services.tailor import ANALYZE_MODEL, _build_client, _extract_json_object

log = logging.getLogger(__name__)

_FUZZY_THRESHOLD = 0.87
_YES = {"yes", "y", "true"}
_NO = {"no", "n", "false"}
_BOOLEAN_FIELDS = {"radio", "checkbox", "select"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def _invert_answer(answer: str) -> str | None:
    """Flip a yes/no answer. None when the answer isn't clearly boolean (we
    never invert prose)."""
    a = answer.strip().lower()
    if a in _YES:
        return "No"
    if a in _NO:
        return "Yes"
    return None


def _user_pairs(db: Session, user_id: int) -> list[SavedQAPair]:
    return list(
        db.execute(select(SavedQAPair).where(SavedQAPair.user_id == user_id)).scalars().all()
    )


def _bump(db: Session, pair: SavedQAPair) -> None:
    pair.times_used += 1
    pair.last_used_at = datetime.now(UTC)
    db.commit()


def lookup(
    db: Session,
    *,
    user_id: int,
    question_text: str,
    field_type: str,
    settings: Settings | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Resolve `question_text` to a saved answer. Returns
    {answer, canonical_question, confidence, is_inverse}. confidence is one of
    'exact' | 'fuzzy' | 'semantic' | 'none'."""
    settings = settings or get_settings()
    pairs = _user_pairs(db, user_id)
    none = {"answer": None, "canonical_question": None, "confidence": "none", "is_inverse": False}
    if not pairs:
        return none

    nq = _norm(question_text)

    # Tier 1: exact (canonical or any captured variant).
    for p in pairs:
        forms = [p.question_canonical, *(p.question_examples or [])]
        if any(_norm(f) == nq for f in forms):
            _bump(db, p)
            return {
                "answer": p.answer,
                "canonical_question": p.question_canonical,
                "confidence": "exact",
                "is_inverse": False,
            }

    # Tier 2: fuzzy.
    best, best_ratio = None, 0.0
    for p in pairs:
        for f in [p.question_canonical, *(p.question_examples or [])]:
            r = SequenceMatcher(None, nq, _norm(f)).ratio()
            if r > best_ratio:
                best, best_ratio = p, r
    if best is not None and best_ratio >= _FUZZY_THRESHOLD:
        _bump(db, best)
        return {
            "answer": best.answer,
            "canonical_question": best.question_canonical,
            "confidence": "fuzzy",
            "is_inverse": False,
        }

    # Tier 3: semantic (LLM). Needs a key; otherwise treat as novel.
    if not settings.has_anthropic_key:
        return none
    match_idx, inverse = _semantic_match(question_text, pairs, settings=settings, client=client)
    if match_idx < 0:
        return none
    p = pairs[match_idx]
    answer = p.answer
    if inverse:
        if field_type in _BOOLEAN_FIELDS:
            inv = _invert_answer(p.answer)
            if inv is None:
                return none  # can't safely invert this answer
            answer = inv
        else:
            return none  # never invert free-text prose
    # Learn the variant so next time it's an exact/fuzzy hit (free).
    p.question_examples = [*(p.question_examples or []), question_text][:25]
    _bump(db, p)
    return {
        "answer": answer,
        "canonical_question": p.question_canonical,
        "confidence": "semantic",
        "is_inverse": inverse,
    }


_SEMANTIC_SYSTEM = (
    "You match a NEW job-application question to a list of questions the user "
    'has already answered. Return ONLY JSON: {"match_index": <int>, '
    '"inverse": <bool>}. `match_index` is the 0-based index of the '
    "semantically-equivalent question, or -1 if none match. Set `inverse` true "
    "ONLY when the new question is the LOGICAL OPPOSITE of the matched one "
    "(e.g. 'authorized to work without sponsorship' vs 'require sponsorship'). "
    "No markdown, no prose."
)


def _semantic_match(
    question_text: str,
    pairs: list[SavedQAPair],
    *,
    settings: Settings,
    client: Any | None,
) -> tuple[int, bool]:
    api = _build_client(settings, client)
    catalog = "\n".join(f"{i}: {p.question_canonical}" for i, p in enumerate(pairs))
    try:
        resp = api.messages.create(
            model=ANALYZE_MODEL,
            max_tokens=200,
            system=[{"type": "text", "text": _SEMANTIC_SYSTEM}],
            messages=[
                {
                    "role": "user",
                    "content": f"PREVIOUS QUESTIONS:\n{catalog}\n\nNEW QUESTION:\n{question_text}",
                }
            ],
        )
        text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
        obj = _extract_json_object(text)
        idx = int(obj.get("match_index", -1))
        inverse = bool(obj.get("inverse", False))
        if 0 <= idx < len(pairs):
            return idx, inverse
    except Exception:  # noqa: BLE001 — clustering is best-effort; novel on failure
        log.exception("qa semantic match failed")
    return -1, False


def save(
    db: Session,
    *,
    user_id: int,
    question_text: str,
    answer: str,
    field_type: str,
    source_ats: str | None,
    source_url: str | None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> SavedQAPair:
    """Save an answer: cluster onto an existing canonical (add the variant +
    refresh the answer) or create a new canonical."""
    settings = settings or get_settings()
    pairs = _user_pairs(db, user_id)
    nq = _norm(question_text)

    # Exact/fuzzy cluster check.
    target: SavedQAPair | None = None
    for p in pairs:
        forms = [p.question_canonical, *(p.question_examples or [])]
        if any(_norm(f) == nq for f in forms) or any(
            SequenceMatcher(None, nq, _norm(f)).ratio() >= _FUZZY_THRESHOLD for f in forms
        ):
            target = p
            break
    # Semantic cluster check (non-inverse only — an inverse match is a
    # different answer, so it stays its own canonical).
    if target is None and settings.has_anthropic_key and pairs:
        idx, inverse = _semantic_match(question_text, pairs, settings=settings, client=client)
        if idx >= 0 and not inverse:
            target = pairs[idx]

    now = datetime.now(UTC)
    if target is not None:
        if _norm(question_text) not in {
            _norm(f) for f in [target.question_canonical, *(target.question_examples or [])]
        }:
            target.question_examples = [*(target.question_examples or []), question_text][:25]
        target.answer = answer  # user's latest intent wins
        target.field_type = field_type or target.field_type
        target.updated_at = now
        db.commit()
        return target

    pair = SavedQAPair(
        id=uuid.uuid4().hex,
        user_id=user_id,
        question_canonical=question_text,
        question_examples=[question_text],
        answer=answer,
        field_type=field_type or "text",
        source_ats=source_ats,
        source_url=source_url,
    )
    db.add(pair)
    db.commit()
    return pair
