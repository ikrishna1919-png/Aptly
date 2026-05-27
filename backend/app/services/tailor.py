"""Resume tailoring against a job description, powered by Claude Sonnet 4.6.

Two operations:
    analyze(job) -> match score + gaps + 3 tailoring questions  (cached per job)
    generate(job, answers) -> ATS-optimized rewritten resume

When ANTHROPIC_API_KEY is unset the module enters "demo mode" — both
operations return deterministic mock data derived from the job. This keeps
the whole product functional in local dev and on a Render free plan
without a key. Mocks are clearly labeled so they can't be mistaken for
real model output.

The prompts are structured so Claude's prompt cache reuses the stable
prefix (system rules + candidate fingerprint) across requests:
    [system, candidate]  ← cached
    [job description, user answers]  ← volatile
The `cache_control` breakpoint sits on the last cached block.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.job import Job
from app.models.job_analysis import JobAnalysis
from app.services._anthropic_schema import prepare_schema
from app.services.demo_candidate import candidate_fingerprint, get_candidate
from app.sources._text import strip_html

# Hard cap on the JD text we send to Claude — long descriptions waste tokens
# and the marginal signal past ~6k chars is minimal. The truncation marker
# is visible to the model so it knows the text was clipped.
_MAX_JD_CHARS = 6000
_TRUNCATION_NOTE = "\n[truncated]"

# Hard cap on clarifying questions surfaced to the user. The prompt
# asks the model to self-limit, but we ALSO truncate in code so a
# verbose response can't slip past — six questions is the most we'll
# show regardless of what comes back.
_MAX_QUESTIONS = 6

if TYPE_CHECKING:
    from anthropic import Anthropic

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"


# ─── Output schemas ──────────────────────────────────────────────────────────


class Analysis(BaseModel):
    """Structured output of POST /api/tailor/analyze.

    Drives the 5-step ATS optimization spec — this object covers steps
    1, 2, 3, and 5 (keyword extraction, gap detection, gap-only
    questions, genuine-lack flags). Step 4 (rewriting) happens in
    `TailoredResume` after the user answers the questions.
    """

    match_score: int = Field(ge=0, le=100, description="Overall fit, 0-100")
    top_skills: list[str] = Field(
        description=(
            "Step 1: every hard skill, tool, certification, and keyword the JD "
            "screens for. Extract verbatim from the JD."
        )
    )
    matched: list[str] = Field(
        description="Step 2: top_skills the candidate already has on their resume."
    )
    gaps: list[str] = Field(
        description=(
            "Step 2: top_skills missing from the resume. Each is a candidate "
            "for confirmation via `questions` below."
        )
    )
    questions: list[str] = Field(
        description=(
            "Step 3: short yes/no questions, each asking whether the "
            "candidate genuinely has a MISSING skill from `gaps` but failed "
            "to list it. ONLY ask about missing skills — do NOT ask about "
            "anything in `matched`. Cap at MAX 6 questions, prioritized by "
            "impact (the JD's must-haves first). Empty list is valid when "
            "there are no gaps."
        ),
    )
    genuine_lacks: list[str] = Field(
        default_factory=list,
        description=(
            "Step 5: JD requirements the candidate genuinely lacks and cannot "
            "plausibly confirm (e.g. years of experience, hard credentials). "
            "These are surfaced honestly to the user — they don't get a "
            "question because no answer would change the truth."
        ),
    )


class ExperienceBullet(BaseModel):
    company: str
    title: str
    location: str | None = None
    dates: str
    bullets: list[str]


class TailoredResume(BaseModel):
    """The structured output of POST /api/tailor/generate."""

    summary: str = Field(description="2-4 sentence ATS-optimized professional summary")
    skills: list[str] = Field(description="Reordered + filtered skills relevant to this JD")
    experience: list[ExperienceBullet]
    education: list[str] = Field(description="One line per education entry")
    ats_notes: str = Field(
        description="Short note (2-4 sentences) explaining the tailoring choices."
    )


# Precompute once at import time so the rendered request bytes are stable
# (good for prompt caching too). The two schema-prep passes
# (additionalProperties:false + dropping unsupported range keywords) live in
# `app.services._anthropic_schema` and are shared with the profile parser.
ANALYSIS_SCHEMA: dict[str, Any] = prepare_schema(Analysis)
TAILORED_RESUME_SCHEMA: dict[str, Any] = prepare_schema(TailoredResume)


# ─── Public API ──────────────────────────────────────────────────────────────


def analyze_job(
    db: Session,
    job: Job,
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
    client: Anthropic | None = None,
) -> Analysis:
    """Return the cached analysis for `(user, job)` or compute a fresh
    one.

    The cache key combines the candidate fingerprint with the job's
    content hash, so analyses are reused as long as both sides are
    unchanged. Phase 5 makes the cache per-user — two users targeting
    the same job get independent analyses driven by their own
    profiles.
    """
    settings = settings or get_settings()
    candidate = get_candidate(db, user_id=user_id)
    candidate_fp = candidate_fingerprint(candidate)
    job_fp = job.content_hash or _fallback_job_hash(job)
    input_hash = hashlib.sha256(f"{candidate_fp}:{job_fp}".encode()).hexdigest()

    cached = db.execute(
        select(JobAnalysis).where(JobAnalysis.job_id == job.id, JobAnalysis.user_id == user_id)
    ).scalar_one_or_none()
    if cached is not None and cached.input_hash == input_hash:
        return _cap_questions(Analysis.model_validate(cached.analysis))

    if not settings.has_anthropic_key:
        analysis = _demo_analysis(job, candidate=candidate)
    else:
        analysis = _llm_analyze(job, candidate, client=client, settings=settings)
    # Hard ceiling, post-parse: the prompt asks for ≤6 but the model
    # has been known to over-deliver. Truncate so the user never sees
    # more than `_MAX_QUESTIONS`.
    analysis = _cap_questions(analysis)

    # Upsert by (user_id, job_id) — the new uniqueness constraint.
    if cached is None:
        db.add(
            JobAnalysis(
                user_id=user_id,
                job_id=job.id,
                input_hash=input_hash,
                analysis=analysis.model_dump(),
            )
        )
    else:
        cached.input_hash = input_hash
        cached.analysis = analysis.model_dump()
    db.commit()
    return analysis


def generate_resume(
    db: Session,
    job: Job,
    answers: dict[str, str],
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
    client: Anthropic | None = None,
) -> TailoredResume:
    """Produce an ATS-optimized resume for `(user, job)`, incorporating
    the user's answers to the tailoring questions. Not cached —
    answers vary per call."""
    settings = settings or get_settings()
    candidate = get_candidate(db, user_id=user_id)
    if not settings.has_anthropic_key:
        return _demo_resume(job, answers, candidate=candidate)
    return _llm_generate(job, answers, candidate, client=client, settings=settings)


# ─── LLM paths ────────────────────────────────────────────────────────────────

_SYSTEM_ANALYZE = (
    "You are an ATS optimization expert. Your job here is to RESEARCH the fit "
    "between a candidate and a target job — not to rewrite anything yet. You "
    "will be shown a CANDIDATE PROFILE and a TARGET JOB description. Execute "
    "these four steps in order and return the result as JSON matching the "
    "provided schema:\n"
    "\n"
    "1. KEYWORD EXTRACTION → `top_skills`. List every hard skill, tool, "
    "programming language, framework, cloud service, certification, and "
    "domain keyword the JD screens for. Pull terms verbatim from the JD so "
    "the wording matches what an ATS would key on. Do not include soft "
    'skills or fluff ("team player", "fast learner").\n'
    "\n"
    "2. CROSS-REFERENCE → `matched` / `gaps`. For each item in `top_skills`, "
    "classify it as `matched` (the candidate profile already lists it or "
    "clearly demonstrates it) or `gaps` (it's not on the resume). Be "
    "conservative — only put something in `matched` when you can point to "
    "concrete evidence in the candidate profile.\n"
    "\n"
    "3. GAP-ONLY QUESTIONS → `questions`. Produce short yes/no questions "
    "asking whether the candidate genuinely has a MISSING skill (from "
    "`gaps`) but failed to list it. Do NOT ask about anything in "
    "`matched`. NEVER invent skills — your questions are the only path by "
    "which a gap can be added later. If `gaps` is empty, return an empty "
    "`questions` list.\n"
    "\n"
    "   HARD CAP: AT MOST 6 questions. If `gaps` has more than 6 entries, "
    "   choose the 6 questions that would most change the candidate's "
    "   fit for THIS JD — prioritise the JD's stated must-haves and "
    "   high-frequency keywords over nice-to-haves. Drop the rest. "
    "   Never exceed 6 questions even if more gaps exist; the user "
    "   can't answer endless questions.\n"
    "\n"
    "5. GENUINE LACKS → `genuine_lacks`. Flag JD requirements the candidate "
    "genuinely lacks and cannot plausibly confirm via a question (e.g. "
    '"10+ years of X" when they have 2, hard credentials, specific '
    "degrees). These are surfaced to the user honestly; do not put them "
    "in `questions`.\n"
    "\n"
    "Output strictly the JSON schema requested — no prose.\n"
    "\n"
    "Constraints not enforced by the schema:\n"
    "- match_score MUST be an integer in [0, 100] (higher = better fit).\n"
    "- questions MUST be drawn from `gaps` only, MAX 6, prioritised by "
    "  impact on this JD."
)

_SYSTEM_GENERATE = (
    "You are an ATS optimization expert performing step 4 of the tailoring "
    "flow: rewriting the candidate's resume against the target JD. You will "
    "be shown the CANDIDATE PROFILE, the TARGET JOB, and the USER ANSWERS to "
    "the gap questions from the analyze step.\n"
    "\n"
    "CONFIRMED SKILLS = (1) every skill already in the candidate profile, "
    "PLUS (2) every gap-question skill the user answered AFFIRMATIVELY in "
    'USER ANSWERS. A blank, empty, or "no" answer means the skill is NOT '
    "confirmed — do NOT add it.\n"
    "\n"
    "Rules — break these and you fail the task:\n"
    "1. Never fabricate. Use ONLY confirmed skills, employers, titles, dates, "
    "   and outcomes. If the user didn't confirm a gap, that skill must NOT "
    "   appear anywhere in `skills`, `summary`, `experience`, or `education`.\n"
    "2. Mirror the JD's EXACT terminology for confirmed skills (\"Amazon Web "
    '   Services" if the JD says that; "AWS" if the JD says that). This is '
    "   the single biggest ATS lever.\n"
    "3. Format for ATS: standard section headers (Summary, Skills, "
    "   Experience, Education). NO tables, columns, graphics, or text "
    "   boxes — the DOCX renderer enforces this, but your output must be "
    "   plain linearly-parseable text. Bullets one line where possible.\n"
    "4. Bullets are achievement-oriented with METRICS. Strong action verbs. "
    '   Quantify wherever the source material allows ("reduced p95 latency '
    '   480ms→110ms", "adopted by 6 teams", "~3000 RPS"). Drop bullets '
    "   that are vague or irrelevant to this JD.\n"
    "5. Fit MAX 2 PAGES when rendered with standard ATS formatting. Be ruthless "
    "   about cutting irrelevant bullets and dropping skills the JD doesn't "
    "   screen for. Older / less-relevant roles get fewer bullets.\n"
    "\n"
    "In `ats_notes`, briefly explain (a) which JD-terminology choices you "
    "made, (b) which user-confirmed gaps you incorporated, and (c) any JD "
    "requirement that remains genuinely unmet so the user knows.\n"
    "\n"
    "Output strictly the JSON schema requested — no prose."
)


def _candidate_block(candidate: dict[str, Any]) -> str:
    return "CANDIDATE PROFILE (do not modify these facts):\n" + json.dumps(
        candidate, indent=2, sort_keys=True
    )


def _clean_jd(job: Job) -> str:
    """Sanitize + truncate the JD before it goes into the prompt.

    Safety net: even if a job row was ingested before the strip_html
    rewrite (or a future source forgets to clean its descriptions), we
    re-run the cleaner here so the model never sees raw HTML. Empty / None
    descriptions become a clear placeholder so the prompt is still
    well-formed.
    """
    raw = job.description or ""
    cleaned = strip_html(raw)
    if not cleaned:
        return "(no description provided)"
    if len(cleaned) > _MAX_JD_CHARS:
        cleaned = cleaned[: _MAX_JD_CHARS - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE
    return cleaned


def _job_block(job: Job) -> str:
    return (
        f"TARGET JOB:\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'unspecified'}\n"
        f"Skills detected: {', '.join(job.skills) if job.skills else '(none detected)'}\n\n"
        f"Job description:\n{_clean_jd(job)}"
    )


def _build_client(settings: Settings, client: Anthropic | None) -> Anthropic:
    if client is not None:
        return client
    from anthropic import Anthropic  # noqa: PLC0415 — lazy import

    return Anthropic(api_key=settings.anthropic_api_key)


def _llm_analyze(
    job: Job,
    candidate: dict[str, Any],
    *,
    client: Anthropic | None,
    settings: Settings,
) -> Analysis:
    api = _build_client(settings, client)
    response = api.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=[
            {"type": "text", "text": _SYSTEM_ANALYZE},
            {
                "type": "text",
                "text": _candidate_block(candidate),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    _job_block(job)
                    + "\n\nReturn the analysis as JSON matching the provided schema."
                ),
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}},
    )
    text = _first_text(response)
    return Analysis.model_validate_json(text)


def _llm_generate(
    job: Job,
    answers: dict[str, str],
    candidate: dict[str, Any],
    *,
    client: Anthropic | None,
    settings: Settings,
) -> TailoredResume:
    api = _build_client(settings, client)
    response = api.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=[
            {"type": "text", "text": _SYSTEM_GENERATE},
            {
                "type": "text",
                "text": _candidate_block(candidate),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    _job_block(job)
                    + "\n\nUser answers to tailoring questions (may be partial):\n"
                    + json.dumps(answers, indent=2)
                    + "\n\nReturn the tailored resume as JSON matching the provided schema. "
                    "Never invent facts not present in the candidate profile."
                ),
            }
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": TAILORED_RESUME_SCHEMA,
            }
        },
    )
    text = _first_text(response)
    return TailoredResume.model_validate_json(text)


def _first_text(response: Any) -> str:
    """Pull the first text block out of a Messages API response."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise RuntimeError("Anthropic response contained no text block")


# ─── Demo-mode (no API key) ──────────────────────────────────────────────────


def _cap_questions(analysis: Analysis) -> Analysis:
    """Truncate `questions` to `_MAX_QUESTIONS`. Returns the same
    instance when nothing needs trimming; otherwise a copy with the
    list shortened so the original (cached) payload isn't mutated."""
    if len(analysis.questions) <= _MAX_QUESTIONS:
        return analysis
    return analysis.model_copy(update={"questions": analysis.questions[:_MAX_QUESTIONS]})


def _question_for_gap(skill: str) -> str:
    """Deterministic question text used by demo mode AND by tests that need
    to map an answer back to its originating gap."""
    return f"[demo] Have you used {skill} in production?"


def _demo_analysis(job: Job, *, candidate: dict[str, Any]) -> Analysis:
    """Deterministic mock derived from the job's detected skills + candidate.

    Same job → same output every time. Clearly marked so it can't be mistaken
    for real model output (`[demo]` prefix on questions, `genuine_lacks`
    surfaced explicitly). Questions are **one-per-gap and ONLY about gaps** —
    matches the spec.
    """
    candidate_skills_lower = {s.lower() for s in candidate["skills"]}
    job_skills = list(job.skills or [])
    matched = [s for s in job_skills if s.lower() in candidate_skills_lower]
    gaps = [s for s in job_skills if s.lower() not in candidate_skills_lower]
    score = 60 + min(35, len(matched) * 5) - min(15, len(gaps) * 2)
    score = max(20, min(95, score))
    return Analysis(
        match_score=score,
        top_skills=job_skills or ["Communication", "Problem solving"],
        matched=matched,
        gaps=gaps,
        # One question per gap, capped at `_MAX_QUESTIONS` so demo mode
        # mirrors the same ceiling the live path enforces.
        questions=[_question_for_gap(g) for g in gaps[:_MAX_QUESTIONS]],
        # Demo can't tell apart "gap" from "genuinely lacks", so leave empty.
        genuine_lacks=[],
    )


def _is_affirmative(answer: str | None) -> bool:
    """Treat an answer as confirmation if it's non-empty and not a clear no."""
    if not answer:
        return False
    a = answer.strip().lower()
    if not a:
        return False
    if a in {"no", "n", "nope", "never", "not really", "false"}:
        return False
    return True


def _demo_resume(job: Job, answers: dict[str, str], *, candidate: dict[str, Any]) -> TailoredResume:
    """Echo the canonical candidate back, plus any gap skills the user
    confirmed via their answers. Skills the user didn't confirm are never
    added — same rule the live prompt enforces."""
    # Pull confirmed gaps out of the answer keys (they were generated by
    # `_question_for_gap`, so we can recover the skill name).
    confirmed_gaps: list[str] = []
    for question, answer in answers.items():
        if not _is_affirmative(answer):
            continue
        prefix = "[demo] Have you used "
        suffix = " in production?"
        if question.startswith(prefix) and question.endswith(suffix):
            skill = question[len(prefix) : -len(suffix)]
            confirmed_gaps.append(skill)

    # Lead with user-confirmed gaps (the JD specifically asked about
    # them), then the candidate's existing skills. Otherwise the [:18]
    # truncate below could drop a freshly-confirmed skill while keeping
    # ones the JD doesn't even screen for.
    candidate_lower = {x.lower() for x in candidate["skills"]}
    skills = [s for s in confirmed_gaps if s.lower() not in candidate_lower] + list(
        candidate["skills"]
    )

    summary = candidate["summary"]
    if confirmed_gaps:
        summary = (
            f"{summary} (demo: also confirmed via user answers — " f"{', '.join(confirmed_gaps)})"
        )

    return TailoredResume(
        summary=summary,
        skills=skills[:18],
        experience=[
            ExperienceBullet(
                company=e["company"],
                title=e["title"],
                location=e.get("location"),
                dates=f"{e['start']} – {e['end']}",
                bullets=list(e["bullets"]),
            )
            for e in candidate["experience"]
        ],
        education=[
            f"{ed['degree']}, {ed['school']} ({ed['graduation']})" for ed in candidate["education"]
        ],
        ats_notes=(
            "[demo mode] No ANTHROPIC_API_KEY is configured, so this resume is "
            f"the canonical candidate profile tailored against “{job.title}” "
            "using a deterministic mock. Skills the user confirmed via answers "
            "are folded in; unconfirmed gap skills are NOT added. Set "
            "ANTHROPIC_API_KEY on the backend to get a real Claude-generated rewrite."
        ),
    )


def _fallback_job_hash(job: Job) -> str:
    """If a job row has no content_hash (older rows), derive a deterministic
    fingerprint from its visible fields."""
    payload = json.dumps(
        {
            "title": job.title,
            "company": job.company,
            "description": job.description or "",
            "skills": sorted(job.skills or []),
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()
