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
from app.services.demo_candidate import candidate_fingerprint, get_candidate
from app.sources._text import strip_html

# Hard cap on the JD text we send to Claude — long descriptions waste tokens
# and the marginal signal past ~6k chars is minimal. The truncation marker
# is visible to the model so it knows the text was clipped.
_MAX_JD_CHARS = 6000
_TRUNCATION_NOTE = "\n[truncated]"

if TYPE_CHECKING:
    from anthropic import Anthropic

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"


# ─── Output schemas ──────────────────────────────────────────────────────────


class Analysis(BaseModel):
    """The structured output of POST /api/tailor/analyze."""

    match_score: int = Field(ge=0, le=100, description="Overall fit, 0-100")
    top_skills: list[str] = Field(description="3-7 skills the JD emphasizes most")
    matched: list[str] = Field(description="Candidate skills that align with the JD")
    gaps: list[str] = Field(description="Skills/experience the JD wants that are weak or absent")
    questions: list[str] = Field(
        min_length=3,
        max_length=3,
        description="Three short questions whose answers would let us tailor the resume better.",
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


# ─── Schema preparation ─────────────────────────────────────────────────────
#
# Anthropic's structured-output validator requires every object node in the
# JSON schema to set `additionalProperties: false` explicitly — otherwise the
# request 400s with "For 'object' type, 'additionalProperties' must be
# explicitly set to false". Pydantic's `model_json_schema()` doesn't add it,
# so we walk the rendered schema (root + every nested object in $defs,
# properties, items, and combinator branches) and inject it everywhere.


def _strictify_object_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively set `additionalProperties: false` on every object node.

    Walks: the root, `$defs`/`definitions`, `properties` values, `items` (and
    its sub-schemas when it's a list), and `anyOf` / `oneOf` / `allOf` branches.
    Pydantic-generated `$ref` chains are followed via the resolved `$defs` —
    we don't dereference, we just set the flag on the definition itself.
    """

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        # Treat as object schema if the node declares "object" OR carries
        # properties — both are common in Pydantic output.
        node_type = node.get("type")
        is_object = node_type == "object" or "properties" in node
        if is_object and "additionalProperties" not in node:
            node["additionalProperties"] = False

        for key in ("properties", "patternProperties", "$defs", "definitions"):
            if key in node and isinstance(node[key], dict):
                for sub in node[key].values():
                    walk(sub)

        if "items" in node:
            walk(node["items"])
        if "prefixItems" in node:
            walk(node["prefixItems"])

        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                walk(node[key])

    walk(schema)
    return schema


# Precompute once at import time so the rendered request bytes are stable
# (good for prompt caching too).
ANALYSIS_SCHEMA: dict[str, Any] = _strictify_object_schema(Analysis.model_json_schema())
TAILORED_RESUME_SCHEMA: dict[str, Any] = _strictify_object_schema(
    TailoredResume.model_json_schema()
)


# ─── Public API ──────────────────────────────────────────────────────────────


def analyze_job(
    db: Session,
    job: Job,
    *,
    settings: Settings | None = None,
    client: Anthropic | None = None,
) -> Analysis:
    """Return the cached analysis for `job` or compute a fresh one.

    The cache key combines the candidate fingerprint with the job's content
    hash, so analyses are reused as long as both sides are unchanged.
    """
    settings = settings or get_settings()
    candidate = get_candidate(db)
    candidate_fp = candidate_fingerprint(candidate)
    job_fp = job.content_hash or _fallback_job_hash(job)
    input_hash = hashlib.sha256(f"{candidate_fp}:{job_fp}".encode()).hexdigest()

    cached = db.execute(
        select(JobAnalysis).where(JobAnalysis.job_id == job.id)
    ).scalar_one_or_none()
    if cached is not None and cached.input_hash == input_hash:
        return Analysis.model_validate(cached.analysis)

    if not settings.has_anthropic_key:
        analysis = _demo_analysis(job, candidate=candidate)
    else:
        analysis = _llm_analyze(job, candidate, client=client, settings=settings)

    # Upsert by job_id (unique).
    if cached is None:
        db.add(
            JobAnalysis(
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
    settings: Settings | None = None,
    client: Anthropic | None = None,
) -> TailoredResume:
    """Produce an ATS-optimized resume for `job`, incorporating the user's
    answers to the tailoring questions. Not cached — answers vary per call."""
    settings = settings or get_settings()
    candidate = get_candidate(db)
    if not settings.has_anthropic_key:
        return _demo_resume(job, answers, candidate=candidate)
    return _llm_generate(job, answers, candidate, client=client, settings=settings)


# ─── LLM paths ────────────────────────────────────────────────────────────────

_SYSTEM_ANALYZE = (
    "You are an expert resume reviewer and ATS coach. You will be shown a "
    "candidate profile and a job description. Score the fit, identify matched "
    "skills and gaps, and produce three short tailoring questions whose answers "
    "would let us write a stronger, more targeted resume. Be honest about gaps; "
    "do NOT invent experience the candidate doesn't have. Output strictly the "
    "JSON schema requested — no prose."
)

_SYSTEM_GENERATE = (
    "You are an ATS-aware resume writer. Rewrite the candidate's resume to "
    "match the target job while staying STRICTLY truthful — you may reframe, "
    "reorder, and emphasize, but you may NEVER fabricate experience, skills, "
    "employers, or outcomes that aren't in the candidate profile. Use the "
    "user's answers to add detail where the source material is thin. Optimize "
    "for ATS: mirror keywords from the JD where the candidate genuinely has "
    "them, use strong action verbs, keep bullets one line where possible, and "
    "drop irrelevant skills. Output strictly the JSON schema requested."
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


def _demo_analysis(job: Job, *, candidate: dict[str, Any]) -> Analysis:
    """Deterministic mock derived from the job's detected skills + candidate.

    Same job → same output every time. Clearly marked so it can't be mistaken
    for real model output, e.g. one of the questions explicitly says "[demo]".
    """
    candidate_skills_lower = {s.lower() for s in candidate["skills"]}
    job_skills = list(job.skills or [])
    matched = [s for s in job_skills if s.lower() in candidate_skills_lower]
    gaps = [s for s in job_skills if s.lower() not in candidate_skills_lower]
    score = 60 + min(35, len(matched) * 5) - min(15, len(gaps) * 2)
    score = max(20, min(95, score))
    return Analysis(
        match_score=score,
        top_skills=job_skills[:5] or ["Communication", "Problem solving"],
        matched=matched or candidate["skills"][:5],
        gaps=gaps or ["(demo) no obvious gaps detected"],
        questions=[
            f"[demo] Which of your past projects best demonstrates fit for {job.title}?",
            "[demo] What measurable impact (metric + number) are you most proud of?",
            f"[demo] Why are you interested in {job.company} specifically?",
        ],
    )


def _demo_resume(job: Job, answers: dict[str, str], *, candidate: dict[str, Any]) -> TailoredResume:
    """Echo the canonical candidate back in the structured shape, with the
    user's answers folded into the summary so the round-trip still feels live."""
    answer_blob = " | ".join(v for v in answers.values() if v).strip()
    summary = candidate["summary"]
    if answer_blob:
        summary = f"{summary} (demo: incorporating — {answer_blob})"
    return TailoredResume(
        summary=summary,
        skills=candidate["skills"][:15],
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
            "using a deterministic mock. Set ANTHROPIC_API_KEY on the backend to "
            "get a real Claude-generated rewrite."
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
