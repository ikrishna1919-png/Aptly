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
import re
import time
from collections.abc import Callable
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

# Streaming generation (the run-based flow). The worker streams the model's
# JSON and writes throttled partial snapshots so the UI can reveal sections
# as they arrive. `_STREAM_SNAPSHOT_INTERVAL_SECONDS` bounds how often we
# attempt a (cheap) partial parse + DB write. `_GENERATE_HARD_TIMEOUT_SECONDS`
# is the wall-clock ceiling: a generation that blows past it is abandoned and
# the run is marked error (the partial content is preserved) rather than
# leaving the user waiting forever.
_STREAM_SNAPSHOT_INTERVAL_SECONDS = 1.0
_GENERATE_HARD_TIMEOUT_SECONDS = 90.0

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


class ContactLink(BaseModel):
    label: str = ""
    url: str = ""


class Contact(BaseModel):
    name: str = ""
    headline: str = ""
    location: str = ""
    email: str = ""
    phone: str = ""
    links: list[ContactLink] = Field(default_factory=list)


class ResumeMeta(BaseModel):
    """Render metadata. `mode` is chosen by the USER (frontend toggle),
    NOT the model; the server fills it in. `pages_estimate` is the
    measured rendered page count (1 or 2 after the hard cap)."""

    mode: str = "visual"
    pages_estimate: int = 1


class SkillGroup(BaseModel):
    category: str = ""
    items: list[str] = Field(default_factory=list)


class ExperienceEntry(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    start_date: str = Field(default="", description='Start date as "Mon YYYY", e.g. "Jan 2023"')
    end_date: str = Field(
        default="", description='End date as "Mon YYYY", or "Present" for the current role'
    )
    bullets: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    degree: str = ""
    field: str = ""
    institution: str = ""
    location: str = ""
    graduation_date: str = Field(default="", description='Graduation date as "Mon YYYY"')


class ProjectEntry(BaseModel):
    name: str = ""
    description: str = ""
    bullets: list[str] = Field(default_factory=list)


class CertificationEntry(BaseModel):
    name: str = ""
    issuer: str = ""
    date: str = Field(default="", description='Date as "Mon YYYY"')


class AtsBlock(BaseModel):
    matched_keywords: list[str] = Field(
        default_factory=list,
        description="JD keywords the tailored resume genuinely covers.",
    )
    missing_keywords: list[str] = Field(
        default_factory=list,
        description=(
            "JD keywords still not covered (reported honestly; not a target "
            "to fix by inventing content)."
        ),
    )
    score_estimate: int = Field(
        default=0,
        description=(
            "Rough, honest self-estimate of keyword coverage, 0-100. This is "
            "an observation, NOT a goal to maximize. Never invent content to "
            "raise it."
        ),
    )


class GeneratedResume(BaseModel):
    """What the model returns — the resume CONTENT. The server wraps this
    into a `TailoredResume` by attaching render `meta`. Sections with no
    content are returned as empty lists and omitted at render time."""

    contact: Contact = Field(default_factory=Contact)
    summary: str = Field(
        default="", description="2-4 sentence professional summary, no first person"
    )
    skills: list[SkillGroup] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    ats: AtsBlock = Field(default_factory=AtsBlock)


class TailoredResume(GeneratedResume):
    """The full tailored resume returned by the API + handed to the
    renderers: the generated content plus render `meta`."""

    meta: ResumeMeta = Field(default_factory=ResumeMeta)


# Precompute once at import time so the rendered request bytes are stable
# (good for prompt caching too). The two schema-prep passes
# (additionalProperties:false + dropping unsupported range keywords) live in
# `app.services._anthropic_schema` and are shared with the profile parser.
#
# The model is asked for `GeneratedResume` (content only) — `meta` is
# server-owned, so it's deliberately NOT in the schema sent to Anthropic.
ANALYSIS_SCHEMA: dict[str, Any] = prepare_schema(Analysis)
GENERATED_RESUME_SCHEMA: dict[str, Any] = prepare_schema(GeneratedResume)
# Kept under the historical name for any external importer; same object.
TAILORED_RESUME_SCHEMA: dict[str, Any] = GENERATED_RESUME_SCHEMA


# ─── Defensive sanitization ───────────────────────────────────────────────────
#
# The prompt forbids en/em dashes, decorative bullets, and smart quotes, but
# models slip. We walk every output string and replace them, logging a
# counter so we can see how often the rules are violated. Belt-and-suspenders
# with the prompt; the renderers should never see a disallowed character.

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

# char → replacement. en/em/figure/minus dashes → hyphen; decorative bullet
# glyphs → hyphen; smart quotes / primes → straight quotes.
_CHAR_REPLACEMENTS: dict[str, str] = {
    "–": "-",
    "—": "-",
    "‒": "-",
    "―": "-",
    "−": "-",
    "•": "-",
    "‣": "-",
    "◦": "-",
    "⁃": "-",
    "∙": "-",
    "·": "-",
    "●": "-",
    "▪": "-",
    "‧": "-",
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "′": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "″": '"',
}
_TRANSLATE_TABLE = {ord(k): v for k, v in _CHAR_REPLACEMENTS.items()}


def _sanitize_text(text: str, counter: dict[str, int]) -> str:
    """Replace every disallowed character, tallying each into `counter`."""
    for ch in text:
        if ord(ch) in _TRANSLATE_TABLE:
            counter[ch] = counter.get(ch, 0) + 1
    return text.translate(_TRANSLATE_TABLE)


def _sanitize_obj(obj: Any, counter: dict[str, int]) -> Any:
    if isinstance(obj, str):
        return _sanitize_text(obj, counter)
    if isinstance(obj, list):
        return [_sanitize_obj(x, counter) for x in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_obj(v, counter) for k, v in obj.items()}
    return obj


def sanitize_generated(gen: GeneratedResume) -> GeneratedResume:
    """Walk the generated resume's strings, replacing en/em dashes,
    decorative bullets, and smart quotes. Logs a counter when anything
    fired so we can see how often the model violates the character rules."""
    counter: dict[str, int] = {}
    cleaned = _sanitize_obj(gen.model_dump(), counter)
    if counter:
        total = sum(counter.values())
        detail = {f"U+{ord(k):04X}": v for k, v in counter.items()}
        log.info(
            "tailor: sanitized %d disallowed character(s) from model output: %s",
            total,
            detail,
        )
    return GeneratedResume.model_validate(cleaned)


def _fmt_month(value: str | None) -> str:
    """Normalise a date to "Mon YYYY". Leaves a bare year ("2018") or an
    already-formatted value untouched, and maps Present/Current → "Present".
    Never fabricates a month the source doesn't have."""
    v = (value or "").strip()
    if not v:
        return ""
    if v.lower() in {"present", "current", "now"}:
        return "Present"
    m = re.match(r"^(\d{4})-(\d{1,2})$", v)
    if m:
        year, mo = m.group(1), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{_MONTHS[mo - 1]} {year}"
    return v


def _links_from_candidate(candidate: dict[str, Any]) -> list[ContactLink]:
    """Build contact links from the authoritative profile. Handles both
    the `{linkedin, github, website}` dict shape and a list of
    `{label, url}` entries."""
    raw = candidate.get("links") or {}
    out: list[ContactLink] = []
    if isinstance(raw, dict):
        for key, label in (("linkedin", "LinkedIn"), ("github", "GitHub"), ("website", "Website")):
            url = raw.get(key)
            if url and str(url).strip():
                out.append(ContactLink(label=label, url=str(url).strip()))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("url"):
                out.append(
                    ContactLink(
                        label=str(item.get("label") or "").strip(),
                        url=str(item["url"]).strip(),
                    )
                )
    return out


def _reconcile_contact(gen: GeneratedResume, candidate: dict[str, Any]) -> GeneratedResume:
    """Force the factual contact fields from the authoritative candidate
    profile so the model can never fabricate or drift contact details.
    The model's tailored `headline` is kept (falling back to the profile's)."""
    c = gen.contact
    contact = Contact(
        name=(candidate.get("name") or c.name or "").strip(),
        headline=(c.headline or candidate.get("headline") or "").strip(),
        location=(candidate.get("location") or c.location or "").strip(),
        email=(candidate.get("email") or c.email or "").strip(),
        phone=(candidate.get("phone") or c.phone or "").strip(),
        links=_links_from_candidate(candidate) or c.links,
    )
    return gen.model_copy(update={"contact": contact})


# ─── Two-page hard cap ─────────────────────────────────────────────────────────


def _measure_pages(resume: TailoredResume) -> int:
    """Exact rendered page count via the PDF renderer (lazy import to
    avoid a cycle — pdf_export imports TailoredResume from here). We
    measure the VISUAL mode because it's the taller of the two, so a
    resume that fits visual also fits plain."""
    from app.services.pdf_export import count_pages  # noqa: PLC0415

    try:
        return count_pages(resume, mode="visual")
    except Exception as e:  # never let measurement break generation
        log.warning("tailor: page measurement failed, assuming 1 page: %s", e)
        return 1


def _truncate_to_two_pages(resume: TailoredResume) -> TailoredResume:
    """Deterministic last-resort trim when the model still overflows after
    the retry. Per spec: truncate the oldest role's bullets first, then
    drop the oldest role. Never touches the most recent role's existence
    and never invents content."""
    out = resume.model_copy(deep=True)
    guard = 0
    while _measure_pages(out) > 2 and guard < 200:
        guard += 1
        # Trim a bullet off the oldest role that still has more than one
        # (experience is reverse-chronological, so the last entry is oldest).
        trimmed = False
        for exp in reversed(out.experience):
            if len(exp.bullets) > 1:
                exp.bullets.pop()
                trimmed = True
                break
        if trimmed:
            continue
        # Every role is down to one bullet — drop the oldest role outright,
        # keeping at least one.
        if len(out.experience) > 1:
            out.experience.pop()
            continue
        break  # nothing left to safely trim
    return out


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
    stream_cb: Callable[[GeneratedResume], None] | None = None,
    deadline: float | None = None,
) -> TailoredResume:
    """Produce an ATS-standard resume for `(user, job)`, incorporating the
    user's answers to the tailoring questions. Not cached — answers vary
    per call.

    Pipeline: generate content → sanitize disallowed characters →
    reconcile contact from the authoritative profile → enforce the 2-page
    hard cap (one model retry with a tighten addendum, then deterministic
    trimming) → stamp measured `meta.pages_estimate`.

    When `stream_cb` is provided and a real key is configured, the FIRST
    production streams and hands each best-effort partial `GeneratedResume`
    to `stream_cb` (the run worker persists these as progress snapshots).
    The tighten retry is never streamed — it would rewind the UI. `deadline`
    (monotonic seconds) caps the streamed generation. Callers that omit
    `stream_cb` get the original synchronous behavior unchanged.
    """
    settings = settings or get_settings()
    candidate = get_candidate(db, user_id=user_id)
    has_key = settings.has_anthropic_key

    def _produce(addendum: str = "", *, stream: bool = False) -> TailoredResume:
        if not has_key:
            gen = _demo_resume(job, answers, candidate=candidate)
        elif stream and stream_cb is not None:
            gen = _llm_generate_streaming(
                job,
                answers,
                candidate,
                client=client,
                settings=settings,
                addendum=addendum,
                on_partial=stream_cb,
                deadline=deadline,
            )
        else:
            gen = _llm_generate(
                job, answers, candidate, client=client, settings=settings, addendum=addendum
            )
        gen = sanitize_generated(gen)
        gen = _reconcile_contact(gen, candidate)
        return TailoredResume(**gen.model_dump(), meta=ResumeMeta(mode="visual"))

    resume = _produce(stream=stream_cb is not None)
    pages = _measure_pages(resume)

    # Over budget: ask the model to tighten ONCE (only when we have a key —
    # the demo path is deterministic, so a retry would return the same thing).
    if pages > 2 and has_key:
        retry = _produce(addendum=_TIGHTEN_ADDENDUM)
        retry_pages = _measure_pages(retry)
        if retry_pages < pages:
            resume, pages = retry, retry_pages

    # Still over: deterministic trim (oldest role's bullets, then oldest role).
    if pages > 2:
        resume = _truncate_to_two_pages(resume)
        pages = _measure_pages(resume)

    resume.meta.pages_estimate = max(1, min(2, pages))
    return resume


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
    "You are an ATS resume writer. You rewrite a candidate's resume so it is "
    "tailored to a target job, follows strict ATS formatting standards, and "
    "stays 100% truthful. You will be shown the CANDIDATE PROFILE, the TARGET "
    "JOB, and the USER ANSWERS to the gap questions from the analyze step. "
    "Return ONLY JSON matching the provided schema — no prose.\n"
    "\n"
    "CONFIRMED CONTENT = (1) everything already in the candidate profile, "
    "PLUS (2) every gap-question skill the user answered AFFIRMATIVELY in "
    'USER ANSWERS. A blank, empty, or "no" answer means it is NOT confirmed '
    "— do NOT add it.\n"
    "\n"
    "════════ NON-NEGOTIABLE: NEVER FABRICATE ════════\n"
    "Use ONLY confirmed skills, employers, titles, dates, schools, project "
    "names, and outcomes. NEVER invent skills, metrics, numbers, "
    "achievements, employers, or roles the candidate does not have on file. "
    "If a bullet has no number in the profile, do not add one. Facts "
    "(company names, titles, dates, school names, degrees) must appear "
    "EXACTLY as in the profile. The `ats.score_estimate` is an honest "
    "observation, NOT a target — never invent content to raise it.\n"
    "\n"
    "════════ CONTENT RULES ════════\n"
    "- Reverse-chronological order in Experience and Education (most recent "
    "  first).\n"
    "- Naturally weave in keywords from the JOB DESCRIPTION, mirroring the "
    '  job\'s EXACT terminology (use "CI/CD" if the JD says "CI/CD"). No '
    "  keyword stuffing.\n"
    "- Each Experience bullet starts with a strong past-tense action verb "
    "  and, where the profile supports it, includes a quantified result. "
    "  3 to 5 bullets per role.\n"
    "- Group Skills into labeled categories (Languages, Frameworks, Tools, "
    '  Cloud, etc.). Each category is {"category": "...", "items": [...]}.\n'
    "  - LANGUAGES the candidate speaks go in a Skills category named "
    '    "Languages" (e.g. {"category":"Languages","items":["English","Hindi"]}).\n'
    '- Dates as "Mon YYYY" (e.g. "Jan 2023"). The current role\'s '
    '  end_date is "Present".\n'
    "- Past tense for prior roles; present tense ONLY for the current role.\n"
    "- No first-person pronouns. No filler ('responsible for', 'duties "
    "  included', 'team player', 'results-driven', 'proven track record', "
    "  'self-starter', 'detail-oriented').\n"
    "- ACHIEVEMENTS from the profile (awards, honours, recognitions): fold "
    "  them into the bullets of the Experience entry where they occurred. "
    "  NEVER create an Achievements section.\n"
    "- Profile data that has no natural home in the schema: OMIT it. Do not "
    "  invent a section for it.\n"
    "\n"
    "════════ CHARACTER & STYLE RULES (STRICT) ════════\n"
    "- NO en dashes (the U+2013 character) or em dashes (the U+2014 "
    '  character) anywhere in ANY output string. Use a hyphen "-" or '
    '  rewrite. For ranges use the word "to" (e.g. "2021 to 2024").\n'
    "- No emojis, no decorative symbols, no special unicode bullet "
    "  characters. Do not put bullet glyphs inside the bullet strings — "
    "  each bullet is plain text and the renderer adds the marker.\n"
    "- Use straight quotes (' and \") only. Every string must be clean "
    "  plain text, safe for both DOCX and PDF encoding.\n"
    "\n"
    "════════ SECTIONS (CLOSED LIST) ════════\n"
    "The ONLY sections that exist are, in this order: Professional Summary, "
    "Skills, Experience, Education, Projects, Certifications. There is no "
    "other section. Map profile data into these; return an empty array for "
    "any the candidate has no content for (it will be omitted).\n"
    "  - contact: name, a short tailored headline, location, email, phone, "
    "    and links (label + url) drawn from the profile. Do not invent "
    "    contact details.\n"
    "  - summary: 2-4 sentences, no first person. Concrete role + focus + "
    "    1-2 standout, real accomplishments.\n"
    "  - skills: confirmed skills only, grouped into labeled categories, "
    "    JD-relevant categories first.\n"
    "  - experience: every relevant role. 3-5 reframed bullets each, "
    "    achievements folded in.\n"
    "  - education / projects / certifications: from the profile; omit when "
    "    empty. Never fabricate an issuer or date.\n"
    "\n"
    "════════ LENGTH ════════\n"
    "Target a clean 1-2 page resume. Be concise: cut bullets the JD doesn't "
    "care about, and give older roles fewer bullets than recent ones.\n"
    "\n"
    "In `ats.matched_keywords` list JD keywords the resume genuinely covers; "
    "in `ats.missing_keywords` list JD keywords still not covered (reported "
    "honestly). Output strictly the JSON schema requested — no prose."
)

# Appended to the user message on the single retry when the first render
# came out over the 2-page cap. Asks the model to tighten rather than the
# server bluntly truncating.
_TIGHTEN_ADDENDUM = (
    "\n\nIMPORTANT: the previous version rendered to more than 2 pages. "
    "Tighten it to fit 2 pages: shorten wordy bullets, keep 3-4 bullets on "
    "recent roles and 2-3 on older ones, and trim the oldest, least-relevant "
    "roles' detail first. Do NOT remove the most recent role and do NOT "
    "invent anything. Same JSON schema."
)


def _candidate_block(candidate: dict[str, Any]) -> str:
    return "CANDIDATE PROFILE (do not modify these facts):\n" + json.dumps(
        candidate, indent=2, sort_keys=True
    )


def _flat_skills(candidate: dict[str, Any]) -> list[str]:
    """Flatten the candidate's `skills` field to a list of strings.

    The Profile model accepts either the legacy flat list OR a
    list of `{category, items[]}` groups (the new categorised shape
    the parser emits). This helper is the only place tailor code
    needs to know about the union — every consumer below reads from
    here so the gap-matching logic stays simple."""
    raw = candidate.get("skills") or []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            for s in item.get("items") or []:
                if isinstance(s, str) and s.strip():
                    out.append(s.strip())
        elif isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


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


def _generate_user_content(job: Job, answers: dict[str, str], addendum: str = "") -> str:
    """Build the GENERATE user message. Beyond the JD + raw answers it appends
    an explicit EXCLUSION list — the gap questions the user did NOT confirm —
    so the model is told, in so many words, never to add those skills. The
    system prompt already treats blank/"no" as unconfirmed; this is
    belt-and-suspenders for the central no-fabrication rule (and is unit-tested
    to ensure a declined skill never appears)."""
    excluded = [q for q, a in answers.items() if not _is_affirmative(a)]
    parts = [
        _job_block(job),
        "\n\nUser answers to tailoring questions (may be partial):\n",
        json.dumps(answers, indent=2),
    ]
    if excluded:
        parts.append(
            "\n\nThe user did NOT confirm the skills referenced by these questions. "
            "NEVER add these skills to the resume and never imply the candidate "
            "has them:\n- " + "\n- ".join(excluded)
        )
    parts.append(
        "\n\nReturn the tailored resume as JSON matching the provided schema. "
        "Never invent facts not present in the candidate profile." + addendum
    )
    return "".join(parts)


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
    addendum: str = "",
) -> GeneratedResume:
    api = _build_client(settings, client)
    user_content = _generate_user_content(job, answers, addendum)
    response = api.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=[
            {"type": "text", "text": _SYSTEM_GENERATE},
            {
                "type": "text",
                "text": _candidate_block(candidate),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": GENERATED_RESUME_SCHEMA,
            }
        },
    )
    text = _first_text(response)
    return GeneratedResume.model_validate_json(text)


def _first_text(response: Any) -> str:
    """Pull the first text block out of a Messages API response."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise RuntimeError("Anthropic response contained no text block")


def loads_partial(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a partially-streamed JSON object.

    The generate step streams a single JSON object whose top-level keys
    arrive roughly in schema order (contact, summary, skills, experience,
    …). To reveal sections as they land we parse the LARGEST prefix of
    *complete* top-level members and drop the one still in flight — so a
    section never flickers in half-formed. Strings, nesting, and escapes
    are tracked so commas/braces inside values don't fool the scan.

    Returns a dict (possibly `{}` when the object has opened but no member
    is complete yet) or None when nothing usable can be recovered. Never
    raises — a snapshot we can't parse is simply skipped.
    """
    s = text.strip()
    if not s:
        return None
    # Fast path: the buffer is already a complete object.
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001 — fall through to the lenient path
        pass

    start = s.find("{")
    if start == -1:
        return None

    depth = 0
    in_str = False
    escape = False
    top_commas: list[int] = []  # indices of commas between top-level members
    closed_at: int | None = None
    for i in range(start, len(s)):
        ch = s[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_str:
                escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                closed_at = i
                break
        elif ch == "," and depth == 1:
            top_commas.append(i)

    if closed_at is not None:
        try:
            obj = json.loads(s[start : closed_at + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:  # noqa: BLE001
            pass

    if not top_commas:
        # Object opened but not a single complete member yet.
        return {}

    # Close after the last complete member (everything before the final
    # top-level comma is fully formed). Fall back to fewer members if a
    # member somehow still won't parse.
    for comma in reversed(top_commas):
        try:
            obj = json.loads(s[start:comma] + "}")
            return obj if isinstance(obj, dict) else None
        except Exception:  # noqa: BLE001
            continue
    return {}


def _llm_generate_streaming(
    job: Job,
    answers: dict[str, str],
    candidate: dict[str, Any],
    *,
    client: Anthropic | None,
    settings: Settings,
    addendum: str = "",
    on_partial: Callable[[GeneratedResume], None] | None = None,
    deadline: float | None = None,
) -> GeneratedResume:
    """Streaming twin of `_llm_generate`.

    Identical prompt + schema; the only difference is transport. We accumulate
    the streamed JSON text and, at most once per `_STREAM_SNAPSHOT_INTERVAL_
    SECONDS`, hand a best-effort partial `GeneratedResume` to `on_partial` so
    the worker can persist a progress snapshot. If the model exceeds
    `deadline` (monotonic seconds), we raise `TimeoutError` so the worker can
    record a clean terminal error instead of hanging.

    Falls back to the final message text when the streamed text deltas are
    empty (defensive — keeps correctness even if structured output doesn't
    surface through `text_stream` on some SDK/path), at the cost of progressive
    reveal in that edge case only.
    """
    api = _build_client(settings, client)
    user_content = _generate_user_content(job, answers, addendum)
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": 4000,
        "system": [
            {"type": "text", "text": _SYSTEM_GENERATE},
            {
                "type": "text",
                "text": _candidate_block(candidate),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [{"role": "user", "content": user_content}],
        "output_config": {"format": {"type": "json_schema", "schema": GENERATED_RESUME_SCHEMA}},
    }

    chunks: list[str] = []
    last_emit = 0.0
    with api.messages.stream(**kwargs) as stream:
        for delta in stream.text_stream:
            chunks.append(delta)
            now = time.monotonic()
            if deadline is not None and now > deadline:
                raise TimeoutError("resume generation exceeded the time budget")
            if on_partial is not None and (now - last_emit) >= _STREAM_SNAPSHOT_INTERVAL_SECONDS:
                last_emit = now
                partial = loads_partial("".join(chunks))
                if partial:
                    try:
                        on_partial(GeneratedResume.model_validate(partial))
                    except Exception:  # noqa: BLE001 — a bad snapshot is non-fatal
                        pass
        text = "".join(chunks)
        if not text.strip():
            text = _first_text(stream.get_final_message())
    return GeneratedResume.model_validate_json(text)


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
    candidate_skills_lower = {s.lower() for s in _flat_skills(candidate)}
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


def _demo_resume(
    job: Job, answers: dict[str, str], *, candidate: dict[str, Any]
) -> GeneratedResume:
    """Deterministic mock in the new ATS schema. Echoes the candidate
    profile, folds in any gap skills the user confirmed, and emits the
    spec's categorized-skills / dated-entry / ats shapes. Skills the user
    didn't confirm are never added — same rule the live prompt enforces."""
    # Recover confirmed gaps from the answer keys (generated by
    # `_question_for_gap`, so the skill name is parseable back out).
    confirmed_gaps: list[str] = []
    for question, answer in answers.items():
        if not _is_affirmative(answer):
            continue
        prefix = "[demo] Have you used "
        suffix = " in production?"
        if question.startswith(prefix) and question.endswith(suffix):
            confirmed_gaps.append(question[len(prefix) : -len(suffix)])

    candidate_skills = _flat_skills(candidate)
    candidate_lower = {x.lower() for x in candidate_skills}
    # Lead with user-confirmed gaps (the JD asked about them), then the
    # candidate's existing skills.
    confirmed_first = [s for s in confirmed_gaps if s.lower() not in candidate_lower]
    ordered_skills = confirmed_first + candidate_skills

    skill_groups: list[SkillGroup] = []
    if ordered_skills:
        skill_groups.append(SkillGroup(category="Skills", items=ordered_skills[:24]))
    # Spoken languages → their own Skills category (spec routing rule).
    spoken = [
        str(lang.get("name")).strip()
        for lang in (candidate.get("languages") or [])
        if isinstance(lang, dict) and lang.get("name")
    ]
    if spoken:
        skill_groups.append(SkillGroup(category="Languages", items=spoken))

    experience = [
        ExperienceEntry(
            title=e.get("title", ""),
            company=e.get("company", ""),
            location=e.get("location") or "",
            start_date=_fmt_month(e.get("start")),
            end_date=_fmt_month(e.get("end")),
            bullets=list(e.get("bullets") or []),
        )
        for e in (candidate.get("experience") or [])
    ]

    education = [
        EducationEntry(
            degree=ed.get("degree", ""),
            field=ed.get("field") or "",
            institution=ed.get("school") or ed.get("institution") or "",
            location=ed.get("location") or "",
            graduation_date=_fmt_month(ed.get("graduation") or ed.get("graduation_date")),
        )
        for ed in (candidate.get("education") or [])
    ]

    projects = [
        ProjectEntry(
            name=p.get("name", ""),
            description=p.get("description") or "",
            bullets=list(p.get("bullets") or []),
        )
        for p in (candidate.get("projects") or [])
        if isinstance(p, dict) and p.get("name")
    ]

    certifications = [
        CertificationEntry(
            name=c.get("name", ""),
            issuer=c.get("issuer") or "",
            date=_fmt_month(c.get("date")),
        )
        for c in (candidate.get("certifications") or [])
        if isinstance(c, dict) and c.get("name")
    ]

    # ATS keyword coverage from the job's detected skills.
    job_skills = list(job.skills or [])
    confirmed_lower = candidate_lower | {g.lower() for g in confirmed_gaps}
    matched = [s for s in job_skills if s.lower() in confirmed_lower]
    missing = [s for s in job_skills if s.lower() not in confirmed_lower]
    score = max(0, min(100, round(100 * len(matched) / len(job_skills)))) if job_skills else 0

    return GeneratedResume(
        contact=Contact(
            name=candidate.get("name", ""),
            headline=candidate.get("headline") or "",
            location=candidate.get("location") or "",
            email=candidate.get("email") or "",
            phone=candidate.get("phone") or "",
            links=_links_from_candidate(candidate),
        ),
        summary=candidate.get("summary") or "",
        skills=skill_groups,
        experience=experience,
        education=education,
        projects=projects,
        certifications=certifications,
        ats=AtsBlock(matched_keywords=matched, missing_keywords=missing, score_estimate=score),
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
