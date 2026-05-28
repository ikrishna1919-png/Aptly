"""Hybrid resume parser — regex for deterministic contact fields, Claude
for structural ones.

The "Paste resume to autofill" feature POSTs raw text to
`POST /api/profile/parse`. We used to run a pure-regex parser here. It
got the easy bits right (email, phone, LinkedIn, GitHub, location) but
was unreliable on the structural shape of experience / education
entries — title vs. company vs. dates vs. location all look like
"some capitalised words separated by punctuation" and regex can't tell
them apart confidently. Examples in the wild that broke:

  * "Senior Engineer, Acme Inc — San Francisco, CA"     (no @, no ·)
  * "Acme Inc\nSenior Engineer\nJan 2020 – Present"     (3-line block)
  * "Software Engineer (Backend)\nAcme · Remote · 2022" (parens + ·)

This module now takes a hybrid approach:

  * **Regex** (cheap, deterministic, never wrong) handles the contact
    fields: email, phone, LinkedIn URL, GitHub URL, personal website,
    location. These have hard-edged patterns and the LLM adds no value.
  * **Anthropic structured output** handles the structural fields:
    name, experience array, education array, skills. The model is
    prompted conservatively — return null for any field it can't
    confidently extract, never guess or fabricate.

When `ANTHROPIC_API_KEY` is empty (local dev, tests without a key) or
the LLM call fails / times out, we fall back to the regex extractor for
the structural fields too. Partial extraction is better than no
extraction — the frontend renders whatever fields came back and lets
the user fill the rest in by hand.

**Background-worker contract (don't break it):**

  * `_execute_parse_run` MUST write a terminal status — `success` or
    `failed` — on every code path. The try/except/finally below is
    structured so the `finally` clause writes a defensive `failed`
    row if the success + error branches both fail somehow. A row
    that sits at `running` forever is the failure mode this module
    is built to prevent.
  * Every step the worker takes emits an `INFO` log line keyed on
    `run_id`. The operator can grep one run_id in Render's logs and
    see exactly where the worker stopped if a row ever did get
    stuck.
  * The Anthropic call has a hard wall-clock ceiling (see
    `_LLM_HARD_TIMEOUT_SECONDS`) enforced by running the SDK call
    inside `concurrent.futures.ThreadPoolExecutor` and reading the
    future with `.result(timeout=…)`. The SDK's own `timeout=` is
    per-phase (connect / read / pool); a slow-stream or stuck pool
    can blow past it. The wall-clock ceiling guarantees control
    returns to the worker even when the underlying HTTP socket
    hangs.

Public API and the Pydantic shapes are unchanged so the frontend
autofill UI keeps working:

  * `parse_resume(text, *, settings=None) -> Profile`
  * `Profile` / `ProfileLinks` / `ProfileExperience` / `ProfileEducation`
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models.parse_run import (
    PARSE_STATUS_FAILED,
    PARSE_STATUS_RUNNING,
    PARSE_STATUS_SUCCESS,
    ParseRun,
)
from app.services._anthropic_schema import prepare_schema

if TYPE_CHECKING:
    from anthropic import Anthropic

log = logging.getLogger(__name__)

# Defensive cap on input size — the parser is linear-time per line and
# could in theory grind on absurd input. 200K characters is well above
# any real resume; truncate beyond that to keep the worker bounded.
_MAX_RESUME_CHARS = 200_000

# How much resume text we hand to Claude. Long resumes get truncated;
# the parser's accuracy past ~12k chars degrades anyway (it's almost
# always boilerplate / older role bullets by that point) and the
# truncation keeps token spend bounded.
_LLM_MAX_CHARS = 14_000

# Per-phase HTTP timeout passed to the Anthropic SDK. The SDK
# enforces this on connect / read / write / pool independently —
# it's NOT a wall-clock total. See `_LLM_HARD_TIMEOUT_SECONDS`
# below for the wall-clock ceiling that actually guarantees
# control returns.
_LLM_TIMEOUT_SECONDS = 30.0

# Hard wall-clock cap on the LLM call. Enforced via
# `concurrent.futures.Future.result(timeout=…)` around the SDK
# invocation — this is the failsafe that keeps a slow-streaming or
# stuck-pool HTTP connection from blocking the worker forever and
# leaving a parse run at `status=running`. Tuned generously (60s)
# so a real, slow but live Anthropic call still completes; we'd
# rather wait an extra 30s than wrongly fail a slow run.
_LLM_HARD_TIMEOUT_SECONDS = 60.0

MODEL = "claude-sonnet-4-6"


# ─── Schema (mirrors the Candidate.profile shape the tailor service reads) ──


class ProfileLinks(BaseModel):
    linkedin: str | None = None
    github: str | None = None


class ProfileExperience(BaseModel):
    company: str
    title: str
    location: str | None = None
    start: str = Field(description="YYYY-MM or YYYY")
    end: str = Field(description="YYYY-MM, YYYY, or 'Present'")
    bullets: list[str] = Field(default_factory=list)


class ProfileEducation(BaseModel):
    school: str
    degree: str
    location: str | None = None
    graduation: str = Field(default="", description="YYYY")


class ProfileProject(BaseModel):
    """One personal / professional project entry. Optional fields are
    null when the resume doesn't surface them — the tailor service
    only renders what's populated."""

    name: str
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    link: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class ProfileAchievement(BaseModel):
    """One award / honour / notable accomplishment."""

    title: str
    description: str = ""
    date: str | None = None


class Profile(BaseModel):
    """The candidate profile the tailor service runs against. Stored as
    JSON in `candidates.profile` — no migration is needed when fields
    are added below because the column is `JSON`.

    Field order here also serves as the *default* section order in the
    tailored output when the user's original resume doesn't pin a
    different one (see `tailor.py:_SYSTEM_GENERATE`)."""

    name: str
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: ProfileLinks = Field(default_factory=ProfileLinks)
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[ProfileExperience] = Field(default_factory=list)
    education: list[ProfileEducation] = Field(default_factory=list)
    projects: list[ProfileProject] = Field(default_factory=list)
    achievements: list[ProfileAchievement] = Field(default_factory=list)
    # Order the user's resume presents its sections in, when known.
    # Populated by the LLM parser; the tailor service uses it to
    # mirror the user's section ordering. Free-form strings so a
    # template that uses non-standard headers still survives.
    section_order: list[str] = Field(default_factory=list)


# ─── Typed errors (kept for backwards compatibility) ────────────────────────


class ResumeParseError(RuntimeError):
    """Base class for resume-parse failures. The parser never raises
    this on its own — `parse_resume` always returns a Profile — but
    callers that previously caught it keep compiling."""


# ─── LLM-side schema (what we ask Claude for) ───────────────────────────────


class _LLMExperience(BaseModel):
    """One work-experience entry as extracted by the model. Every field
    is nullable so the model can return null when it can't confidently
    extract a value — the prompt enforces that rule explicitly."""

    company: str | None = None
    title: str | None = None
    location: str | None = None
    start_date: str | None = Field(
        default=None,
        description="Free-form (e.g. 'Jan 2022', '2022-01', '2022'). Normalised post-parse.",
    )
    end_date: str | None = Field(
        default=None,
        description="Same format as start_date, or the literal string 'Present'.",
    )
    description_bullets: list[str] = Field(default_factory=list)


class _LLMEducation(BaseModel):
    school: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class _LLMSkillGroup(BaseModel):
    """Resumes that group skills by category (e.g. Languages, Cloud,
    Frameworks) get returned in this shape — preserved so the model
    doesn't have to lose structure. We flatten in post-processing
    because the downstream Profile model carries skills as a single
    list."""

    category: str
    items: list[str] = Field(default_factory=list)


class _LLMProject(BaseModel):
    name: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    link: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class _LLMAchievement(BaseModel):
    title: str | None = None
    description: str | None = None
    date: str | None = None


class _LLMStructuralExtract(BaseModel):
    """The structured output the resume-parse Claude call returns. The
    `skills` field can be either a flat list (most resumes) OR a list
    of category groups (resumes with `Technical Skills:` /
    `Languages:` etc.). Pydantic produces an `anyOf` in the JSON schema
    that Anthropic accepts.

    `section_order` records the resume's actual section ordering so the
    tailor service can mirror the candidate's voice / structure. Free-
    form strings (we don't constrain via enum) so a template that
    uses non-standard headers like "Selected Work" or "Highlights"
    still round-trips."""

    name: str | None = None
    experience: list[_LLMExperience] = Field(default_factory=list)
    education: list[_LLMEducation] = Field(default_factory=list)
    skills: list[str] | list[_LLMSkillGroup] = Field(default_factory=list)
    projects: list[_LLMProject] = Field(default_factory=list)
    achievements: list[_LLMAchievement] = Field(default_factory=list)
    section_order: list[str] = Field(default_factory=list)


# Precompute the schema once. `prepare_schema` strips the Anthropic-
# unsupported keywords (default, title, minLength, etc.) AND adds
# `additionalProperties: false` to every object node — both rules the
# tailor module also relies on.
_LLM_SCHEMA: dict[str, Any] = prepare_schema(_LLMStructuralExtract)


# ─── Patterns ───────────────────────────────────────────────────────────────


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Phone numbers in their common shapes. Length-validated by the digit
# count (10 or 11) inside `_extract_phone` to keep this regex permissive.
_PHONE_RE = re.compile(
    r"(?:(?:\+?\d{1,3})[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}",
)

_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9_\-./]+",
    re.IGNORECASE,
)
_GITHUB_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-./]*)?",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

_MONTH_GROUP = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATE_TOKEN = rf"(?:{_MONTH_GROUP}\.?\s+)?(?:19|20)\d{{2}}"
_DATE_RANGE_RE = re.compile(
    rf"({_DATE_TOKEN})\s*(?:[-–—]|to)\s*({_DATE_TOKEN}|Present|Current|Now)",
    re.IGNORECASE,
)

_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[•◦▪●‣⁃*\-–—])\s+")

_LOCATION_RE = re.compile(
    r"\b([A-Z][A-Za-z][A-Za-zÀ-ſ.'\- ]{0,40}?),\s+"
    r"(?:([A-Z]{2})\b|([A-Z][a-zA-ZÀ-ſ]+(?:\s+[A-Z][a-zA-ZÀ-ſ]+)?))"
)

_MONTHS = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "sept": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}

_SECTION_HEADERS: dict[str, tuple[str, ...]] = {
    "summary": (
        "summary",
        "professional summary",
        "career summary",
        "profile",
        "objective",
        "career objective",
        "about",
        "about me",
    ),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "work history",
        "career history",
        "relevant experience",
    ),
    "education": ("education", "academic background", "educational background"),
    "skills": (
        "skills",
        "technical skills",
        "core skills",
        "technologies",
        "tech stack",
        "core competencies",
        "technical competencies",
        "competencies",
    ),
    "projects": ("projects", "personal projects", "side projects"),
    "certifications": ("certifications", "certificates", "licenses"),
    "publications": ("publications", "papers"),
    "awards": ("awards", "honors", "achievements"),
}

_DEGREE_PATTERNS = re.compile(
    r"\b("
    r"B\.?S\.?(?:\s*c)?|B\.?A\.?|B\.?Sc\.?|"
    r"M\.?S\.?(?:\s*c)?|M\.?A\.?|M\.?Sc\.?|MBA|MEng|MPhil|MD|JD|"
    r"Ph\.?D\.?|D\.?Phil\.?|EdD|"
    r"Bachelor(?:'s)?(?:\s+of\s+[A-Za-z]+)?|"
    r"Master(?:'s)?(?:\s+of\s+[A-Za-z]+)?|"
    r"Doctorate|Doctoral|Associate(?:'s)?"
    r")\b",
    re.IGNORECASE,
)

_INSTITUTION_KEYWORDS_RE = re.compile(
    r"\b(University|College|Institute|Polytechnic|Academy|School of)\b",
    re.IGNORECASE,
)


# ─── Public API ─────────────────────────────────────────────────────────────


def parse_resume(
    text: str,
    *,
    settings: Settings | None = None,
    client: Anthropic | None = None,
    run_id: str | None = None,
) -> Profile:
    """Best-effort parse of pasted resume text. Always returns a Profile.

    Strategy:
      1. Run the regex extractors for everything — these become the
         fallback if the LLM call fails.
      2. If `ANTHROPIC_API_KEY` is configured, call Claude to extract
         the structural fields (name, experience, education, skills).
         Merge the LLM result over the regex fallback.
      3. Contact fields (email, phone, links, location) always come
         from regex — the LLM result for those is ignored.

    On any Anthropic error or timeout (including the wall-clock
    `_LLM_HARD_TIMEOUT_SECONDS` ceiling), we log the full error and
    return the regex-only result. Partial > empty.

    `run_id` is optional — when set, every log line includes it so
    the operator can grep one parse run in Render's logs without
    cross-talk from other concurrent parses.
    """
    tag = f"parse_run={run_id}" if run_id else "parse_run=adhoc"
    settings = settings or get_settings()
    if not isinstance(text, str) or not text.strip():
        log.info("%s: empty input, returning empty profile", tag)
        return _empty_profile()

    text = text[:_MAX_RESUME_CHARS]
    lines = [ln.rstrip() for ln in text.splitlines()]
    sections = _segment_sections(lines)
    header_lines = sections.get("_preamble", [])

    # Regex pass — runs unconditionally. Provides every contact field
    # AND the structural fallback.
    log.info("%s: running regex extract (input %d chars)", tag, len(text))
    regex_profile = _regex_extract(text, lines, sections, header_lines)
    log.info(
        "%s: regex extract complete (name=%r, experience=%d, education=%d)",
        tag,
        bool(regex_profile.name),
        len(regex_profile.experience),
        len(regex_profile.education),
    )

    if not settings.has_anthropic_key:
        log.info("%s: no ANTHROPIC_API_KEY — returning regex-only profile", tag)
        return regex_profile

    # LLM pass for structural fields. Failures don't propagate; we just
    # return the regex result. Three failure modes are caught here:
    #   1. The Anthropic SDK raises (auth error, rate limit, 500, etc.)
    #   2. The wall-clock ceiling fires (`TimeoutError`).
    #   3. The response doesn't parse as the expected schema.
    log.info("%s: starting Anthropic structural extract", tag)
    try:
        llm = _llm_extract_structural(text, settings=settings, client=client, run_id=tag)
    except concurrent.futures.TimeoutError:
        log.warning(
            "%s: LLM call exceeded wall-clock %.1fs; falling back to regex",
            tag,
            _LLM_HARD_TIMEOUT_SECONDS,
        )
        return regex_profile
    except Exception as exc:  # noqa: BLE001 — broad on purpose, fall through to regex
        # Log the full error so the operator has something to debug
        # with. `log.exception` includes the traceback.
        log.exception("%s: LLM extraction failed; falling back to regex: %s", tag, exc)
        return regex_profile

    log.info("%s: Anthropic extract returned; merging with regex profile", tag)
    return _merge(regex_profile, llm)


def _empty_profile() -> Profile:
    return Profile(name="")


# ─── LLM extraction ─────────────────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "You extract structured fields from a candidate's pasted resume "
    "text. You will be shown the raw resume; return JSON matching the "
    "provided schema.\n"
    "\n"
    "Be CONSERVATIVE. The cost of being wrong is worse than the cost "
    "of being incomplete:\n"
    "  * Return null for any field you cannot confidently extract.\n"
    "  * Never invent or guess a value. If the resume doesn't clearly "
    "    state the company, title, dates, or location, leave it null.\n"
    "  * Pull values VERBATIM from the resume text. Do not paraphrase "
    "    titles or shorten company names.\n"
    "\n"
    "Field-by-field guidance:\n"
    "  - name: the candidate's full name, exactly as written at the "
    "    top of the resume. null if the resume doesn't start with a "
    "    clearly-formatted name.\n"
    "  - experience: each entry is one role. `company` is the "
    "    employer name; `title` is the role; do NOT swap them. "
    "    `start_date` / `end_date` are free-form date strings as they "
    "    appear in the resume (e.g. 'Jan 2022', '2022', '2022-01'); "
    "    end_date is the literal string 'Present' for ongoing roles. "
    "    `description_bullets` is the achievement bullets verbatim, "
    "    one string per bullet, stripped of leading glyphs.\n"
    "  - education: one entry per institution. `school` is the "
    "    institution; `degree` is the credential (B.S., M.A., Ph.D., "
    "    etc.); `field_of_study` is the major (separate field — do "
    "    not pack it into `degree`).\n"
    "  - skills: a flat list of strings IF the resume lists skills as "
    "    one ungrouped collection. If the resume groups skills by "
    "    category (e.g. 'Languages: Python, Go; Cloud: AWS, GCP'), "
    "    return a list of `{category, items}` objects so the "
    "    structure is preserved.\n"
    "  - projects: personal or professional projects under a 'Projects' / "
    "    'Personal Projects' / 'Side Projects' / 'Selected Work' header. "
    "    `name` is the project title; `description` is one or two "
    "    sentences; `technologies` is the stack as a string list (only "
    "    if the resume explicitly enumerates one); `link` is the URL if "
    "    one is given. Omit the section entirely if the resume has none.\n"
    "  - achievements: awards, honours, notable accomplishments under "
    "    headers like 'Awards', 'Honors', 'Achievements', 'Recognition'. "
    "    Distinct from project bullets and from experience-section "
    "    achievements — only what's filed under its own header. Omit if "
    "    the resume has none.\n"
    "  - section_order: a list of the resume's section headings in the "
    "    order they appear, lowercased. Example: ['summary', 'experience', "
    "    'projects', 'skills', 'education']. Used by downstream tooling "
    "    to mirror the candidate's preferred ordering.\n"
    "\n"
    "Output strictly the JSON schema requested — no prose, no markdown."
)


def _llm_extract_structural(
    text: str,
    *,
    settings: Settings,
    client: Anthropic | None = None,
    run_id: str | None = None,
) -> _LLMStructuralExtract:
    api = _build_client(settings, client)
    # Truncate the input that goes to the model. Past ~14k characters
    # we're almost certainly past the useful structural content (older
    # bullets, references, hobbies) and the LLM accuracy on the head of
    # the resume matters far more.
    payload = text[:_LLM_MAX_CHARS]

    def _call() -> Any:
        return api.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Raw resume text:\n---\n"
                        + payload
                        + "\n---\n\nReturn the extracted fields as JSON matching the schema."
                    ),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _LLM_SCHEMA}},
        )

    # Hard wall-clock ceiling. The Anthropic SDK's `timeout=` is
    # per-phase and a slow-streaming response can blow past it; this
    # wrapper guarantees control returns to the caller after at most
    # `_LLM_HARD_TIMEOUT_SECONDS`. `concurrent.futures.TimeoutError`
    # propagates up to `parse_resume`, which catches it + falls
    # back to the regex result.
    #
    # The pool is shut down with `wait=False` on timeout. That way
    # we don't wait for the stuck SDK call's thread to finish — it
    # gets abandoned as a daemon-style background thread that'll
    # eventually finish on its own when the underlying HTTP call's
    # per-phase timeout (`_LLM_TIMEOUT_SECONDS`) fires. The worker
    # we care about already moved on.
    tag = run_id or "parse_run=adhoc"
    log.info(
        "%s: messages.create starting (model=%s, payload=%d chars, hard_timeout=%.1fs)",
        tag,
        MODEL,
        len(payload),
        _LLM_HARD_TIMEOUT_SECONDS,
    )
    # `thread_name_prefix` makes a stuck worker easy to spot in
    # py-spy / thread dumps.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="resume-llm")
    try:
        future = pool.submit(_call)
        try:
            response = future.result(timeout=_LLM_HARD_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            log.warning(
                "%s: messages.create exceeded %.1fs wall clock — abandoning thread",
                tag,
                _LLM_HARD_TIMEOUT_SECONDS,
            )
            # Don't block on the stuck thread; abandon it.
            raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    log.info("%s: messages.create returned", tag)
    return _LLMStructuralExtract.model_validate_json(_first_text(response))


def _build_client(settings: Settings, client: Anthropic | None) -> Anthropic:
    """Construct (or return) an Anthropic client with the parser's
    timeout applied. Tests monkeypatch this to inject a mock."""
    if client is not None:
        return client
    from anthropic import Anthropic  # noqa: PLC0415 — lazy import

    return Anthropic(api_key=settings.anthropic_api_key, timeout=_LLM_TIMEOUT_SECONDS)


def _first_text(response: Any) -> str:
    """Pull the first text block out of a Messages API response."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ResumeParseError("Anthropic response contained no text block")


# ─── Merge: structural from LLM, contact from regex ────────────────────────


def _merge(regex_profile: Profile, llm: _LLMStructuralExtract) -> Profile:
    """Build the final Profile: contact fields from `regex_profile`,
    structural fields from `llm` where present (otherwise the regex
    fallback). An empty experience list from the LLM keeps the regex
    list — partial > empty.

    Projects, achievements, and section_order come from the LLM only
    — the regex extractor never tried to populate them, so there's no
    fallback to merge against."""
    name = (llm.name or regex_profile.name or "").strip()

    llm_experience = [_to_profile_experience(e) for e in llm.experience]
    llm_experience = [e for e in llm_experience if e is not None]
    experience = llm_experience or regex_profile.experience

    llm_education = [_to_profile_education(e) for e in llm.education]
    llm_education = [e for e in llm_education if e is not None]
    education = llm_education or regex_profile.education

    llm_skills = _flatten_skills(llm.skills)
    skills = llm_skills or regex_profile.skills

    projects = [p for p in (_to_profile_project(p) for p in llm.projects) if p is not None]
    achievements = [
        a for a in (_to_profile_achievement(a) for a in llm.achievements) if a is not None
    ]
    section_order = [s.strip().lower() for s in llm.section_order if s and s.strip()]

    return Profile(
        name=name,
        headline=regex_profile.headline,
        email=regex_profile.email,
        phone=regex_profile.phone,
        location=regex_profile.location,
        links=regex_profile.links,
        summary=regex_profile.summary,
        skills=skills,
        experience=experience,
        education=education,
        projects=projects,
        achievements=achievements,
        section_order=section_order,
    )


def _to_profile_project(entry: _LLMProject) -> ProfileProject | None:
    name = (entry.name or "").strip()
    if not name:
        return None
    return ProfileProject(
        name=name,
        description=(entry.description or "").strip(),
        technologies=[t.strip() for t in entry.technologies if t and t.strip()],
        link=(entry.link or "").strip() or None,
        start_date=(entry.start_date or "").strip() or None,
        end_date=(entry.end_date or "").strip() or None,
    )


def _to_profile_achievement(entry: _LLMAchievement) -> ProfileAchievement | None:
    title = (entry.title or "").strip()
    if not title:
        return None
    return ProfileAchievement(
        title=title,
        description=(entry.description or "").strip(),
        date=(entry.date or "").strip() or None,
    )


def _to_profile_experience(entry: _LLMExperience) -> ProfileExperience | None:
    """Convert an LLM experience entry to the Profile shape. Drops
    entries where neither title nor company was confidently extracted —
    those are noise rows the LLM hallucinated structure into."""
    company = (entry.company or "").strip()
    title = (entry.title or "").strip()
    if not company and not title:
        return None
    start = _normalise_date(entry.start_date or "")
    end_raw = (entry.end_date or "").strip()
    if end_raw.lower() in {"present", "current", "now"}:
        end = "Present"
    else:
        end = _normalise_date(end_raw)
    location = (entry.location or "").strip() or None
    bullets = [b.strip() for b in entry.description_bullets if b and b.strip()]
    return ProfileExperience(
        company=company,
        title=title,
        location=location,
        start=start,
        end=end,
        bullets=bullets[:20],
    )


def _to_profile_education(entry: _LLMEducation) -> ProfileEducation | None:
    school = (entry.school or "").strip()
    degree = (entry.degree or "").strip()
    field = (entry.field_of_study or "").strip()
    if not school and not degree and not field:
        return None
    # Combine degree + field of study into the single `degree` slot the
    # downstream Profile model carries. "B.S." + "Computer Science" →
    # "B.S. Computer Science".
    if degree and field:
        degree_full = f"{degree} {field}"
    else:
        degree_full = degree or field
    location = (entry.location or "").strip() or None
    graduation = _extract_graduation_year(entry.end_date or entry.start_date or "")
    return ProfileEducation(
        school=school,
        degree=degree_full,
        location=location,
        graduation=graduation,
    )


def _extract_graduation_year(s: str) -> str:
    """Pull the last YYYY out of a date string. `'2018'`, `'2014 - 2018'`,
    `'May 2018'` all yield `'2018'`. Empty string when no year found."""
    if not s:
        return ""
    years = _YEAR_RE.findall(s)
    if not years:
        return ""
    return years[-1]


def _flatten_skills(skills: list[str] | list[_LLMSkillGroup]) -> list[str]:
    """The Profile model carries skills as a flat list. If the LLM
    returned category groups, flatten them — categories are dropped
    because there's nowhere to store them downstream. Dedupe
    case-insensitively, preserving the first-seen order."""
    flat: list[str] = []
    for item in skills:
        if isinstance(item, _LLMSkillGroup):
            flat.extend(item.items)
        elif isinstance(item, str):
            flat.append(item)
    seen: set[str] = set()
    out: list[str] = []
    for s in flat:
        s = s.strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# ─── Regex extraction (fallback + contact-field source of truth) ────────────


def _regex_extract(
    text: str,
    lines: list[str],
    sections: dict[str, list[str]],
    header_lines: list[str],
) -> Profile:
    name = _extract_name(header_lines)
    email = _extract_email(text)
    phone = _extract_phone(text)
    linkedin = _extract_linkedin(text)
    github = _extract_github(text)
    location = _extract_contact_location(header_lines)
    summary = _extract_summary(sections.get("summary", []))
    skills = _extract_skills(sections.get("skills", []))
    experience = _extract_experience(sections.get("experience", []))
    education = _extract_education(sections.get("education", []))

    return Profile(
        name=name or "",
        headline=None,
        email=email,
        phone=phone,
        location=location,
        links=ProfileLinks(linkedin=linkedin, github=github),
        summary=summary,
        skills=skills,
        experience=experience,
        education=education,
    )


# ─── Section segmentation ──────────────────────────────────────────────────


def _segment_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"_preamble": []}
    current = "_preamble"
    for line in lines:
        section = _header_to_section(line)
        if section is not None:
            current = section
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _header_to_section(line: str) -> str | None:
    stripped = line.strip().rstrip(":")
    if not stripped or len(stripped) > 60:
        return None
    lower = stripped.lower()
    for canonical, aliases in _SECTION_HEADERS.items():
        if lower in aliases:
            return canonical
    return None


# ─── Contact-line extractors ───────────────────────────────────────────────


def _extract_name(preamble_lines: list[str]) -> str | None:
    for line in preamble_lines[:10]:
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > 60 or len(stripped) < 2:
            continue
        if "@" in stripped or any(ch.isdigit() for ch in stripped):
            continue
        if not any(w[:1].isupper() for w in stripped.split() if w):
            continue
        return stripped
    return None


def _extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else None


def _extract_phone(text: str) -> str | None:
    for m in _PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) in (10, 11):
            return m.group(0).strip()
    return None


def _extract_linkedin(text: str) -> str | None:
    m = _LINKEDIN_RE.search(text)
    if not m:
        return None
    return _strip_url_trailing_punct(m.group(0))


def _extract_github(text: str) -> str | None:
    m = _GITHUB_RE.search(text)
    if not m:
        return None
    return _strip_url_trailing_punct(m.group(0))


def _strip_url_trailing_punct(url: str) -> str:
    return url.rstrip(").,;:")


def _extract_contact_location(preamble_lines: list[str]) -> str | None:
    for line in preamble_lines[:12]:
        m = _LOCATION_RE.search(line)
        if m:
            return m.group(0).strip()
    return None


# ─── Summary ────────────────────────────────────────────────────────────────


def _extract_summary(lines: list[str]) -> str:
    body = "\n".join(ln for ln in lines if ln.strip())
    body = re.sub(r"[ \t]+", " ", body).strip()
    if len(body) > 600:
        body = body[:600].rsplit(" ", 1)[0] + "…"
    return body


# ─── Skills (regex fallback) ────────────────────────────────────────────────


def _extract_skills(lines: list[str]) -> list[str]:
    if not lines:
        return []
    combined = " ".join(ln for ln in lines if ln.strip())
    if not combined:
        return []
    combined = _BULLET_PREFIX_RE.sub("", combined)
    raw_parts = re.split(r"[,|·•/•]|\s{2,}|\s{0,}\n\s{0,}", combined)
    seen: set[str] = set()
    out: list[str] = []
    for part in raw_parts:
        item = part.strip(" .;:\t")
        if not item or len(item) > 80:
            continue
        item = re.sub(r"\s*\([^)]*\)\s*$", "", item)
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ─── Experience (regex fallback) ────────────────────────────────────────────


def _extract_experience(lines: list[str]) -> list[ProfileExperience]:
    if not lines:
        return []
    line_meta: list[dict[str, Any]] = []
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            line_meta.append({"line": "", "is_blank": True, "date": None, "is_bullet": False})
            continue
        date = _DATE_RANGE_RE.search(stripped)
        line_meta.append(
            {
                "line": stripped,
                "is_blank": False,
                "date": date,
                "is_bullet": bool(_BULLET_PREFIX_RE.match(ln)),
            }
        )

    entries: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for meta in line_meta:
        if meta["is_blank"]:
            if current and any(m.get("date") for m in current):
                entries.append(current)
                current = []
            continue
        if meta["date"] is not None and current and any(m.get("date") for m in current):
            entries.append(current)
            current = []
        current.append(meta)
    if current and any(m.get("date") for m in current):
        entries.append(current)

    out: list[ProfileExperience] = []
    for entry in entries:
        exp = _entry_to_experience(entry)
        if exp is not None:
            out.append(exp)
    return out


def _entry_to_experience(entry: list[dict[str, Any]]) -> ProfileExperience | None:
    bullets: list[str] = []
    header_lines: list[str] = []
    date_match = None
    for meta in entry:
        if meta["is_bullet"]:
            bullets.append(_BULLET_PREFIX_RE.sub("", meta["line"]).strip())
            continue
        if meta["date"] is not None and date_match is None:
            date_match = meta["date"]
        header_lines.append(meta["line"])

    if date_match is None:
        return None
    start = _normalise_date(date_match.group(1))
    end_raw = date_match.group(2)
    end = _normalise_date(end_raw)
    if end_raw.lower() in {"present", "current", "now"}:
        end = "Present"

    title, company, location = _split_title_company_location(header_lines, date_match.group(0))
    if not title and not company:
        return None
    return ProfileExperience(
        company=company or "",
        title=title or "",
        location=location,
        start=start,
        end=end,
        bullets=bullets[:20],
    )


def _split_title_company_location(
    header_lines: list[str], date_text: str
) -> tuple[str | None, str | None, str | None]:
    cleaned: list[str] = []
    for line in header_lines:
        without_date = line.replace(date_text, "").strip(" \t,–—-|·•")
        if without_date:
            cleaned.append(without_date)

    location: str | None = None
    for line in cleaned:
        m = _LOCATION_RE.search(line)
        if m:
            location = m.group(0).strip()
            break

    for line in cleaned:
        if location and line.endswith(location):
            line = line[: -len(location)].rstrip(" \t,–—-|·•")
        parts = [p.strip() for p in re.split(r"\s+(?:@|at|·|•|\||—|–|,|-)\s+", line) if p.strip()]
        if len(parts) >= 2:
            return parts[0], parts[1], location

    non_empty = [c for c in cleaned if c]
    if len(non_empty) >= 2:
        return non_empty[0], non_empty[1], location
    if non_empty:
        return non_empty[0], None, location
    return None, None, location


# ─── Education (regex fallback) ─────────────────────────────────────────────


def _extract_education(lines: list[str]) -> list[ProfileEducation]:
    if not lines:
        return []
    blocks: list[list[str]] = []
    current: list[str] = []
    for ln in lines:
        if not ln.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(ln.strip())
    if current:
        blocks.append(current)
    if not blocks:
        return []

    if len(blocks) == 1 and len(blocks[0]) >= 4:
        splat: list[list[str]] = []
        buf: list[str] = []
        for ln in blocks[0]:
            if _INSTITUTION_KEYWORDS_RE.search(ln) and buf:
                splat.append(buf)
                buf = []
            buf.append(ln)
        if buf:
            splat.append(buf)
        if len(splat) > 1:
            blocks = splat

    out: list[ProfileEducation] = []
    for block in blocks:
        edu = _block_to_education(block)
        if edu is not None:
            out.append(edu)
    return out


def _block_to_education(block: list[str]) -> ProfileEducation | None:
    school: str | None = None
    degree_text: str | None = None
    location: str | None = None
    graduation: str = ""
    for raw_line in block:
        line = raw_line.strip()
        loc_match = _LOCATION_RE.fullmatch(line)
        if loc_match:
            if location is None:
                location = loc_match.group(0).strip()
            continue
        if school is None and _INSTITUTION_KEYWORDS_RE.search(line):
            school = _strip_degree_remainder(line)
        if degree_text is None:
            m = _DEGREE_PATTERNS.search(line)
            if m:
                degree_text = _trim_degree_line(line, m.start())
        if location is None:
            loc = _LOCATION_RE.search(line)
            if loc:
                location = loc.group(0).strip()
        if not graduation:
            years = _YEAR_RE.findall(line)
            if years:
                graduation = years[-1]

    if not school and not degree_text:
        return None
    return ProfileEducation(
        school=school or "",
        degree=degree_text or "",
        location=location,
        graduation=graduation,
    )


_TRAILING_LOCATION_RE = re.compile(
    r",\s*[A-Z][A-Za-zÀ-ſ.'\- ]+?,\s*(?:[A-Z]{2}|[A-Z][a-zA-ZÀ-ſ]+)\s*$"
)


def _strip_degree_remainder(line: str) -> str:
    out = line.strip()
    m_loc = _TRAILING_LOCATION_RE.search(out)
    if m_loc:
        out = out[: m_loc.start()].rstrip(" ,—–-")
    m_deg = _DEGREE_PATTERNS.search(out)
    if m_deg and m_deg.start() > 0:
        return out[: m_deg.start()].rstrip(" ,—–-")
    return out


def _trim_degree_line(line: str, start: int) -> str:
    tail = line[start:]
    tail = _YEAR_RE.sub("", tail)
    tail = _LOCATION_RE.sub("", tail)
    tail = re.sub(r"[\s,;–—-]+$", "", tail)
    return tail.strip()


# ─── Date helpers ───────────────────────────────────────────────────────────


def _normalise_date(token: str) -> str:
    """Turn `Jan 2020` / `January 2020` / `2020` / `2020-01` / `Present`
    into the canonical `YYYY-MM` / `YYYY` / `Present` form. Empty input
    or unrecognised input → empty string."""
    if not token:
        return ""
    s = token.strip()
    low = s.lower()
    if low in {"present", "current", "now"}:
        return "Present"
    # Already in YYYY-MM form? Pass through after light validation.
    m_iso = re.match(r"^(\d{4})-(\d{1,2})(?:-\d{1,2})?$", s)
    if m_iso:
        year = m_iso.group(1)
        month = m_iso.group(2).zfill(2)
        return f"{year}-{month}"
    m = re.match(rf"({_MONTH_GROUP})\.?\s+(\d{{4}})", s, re.IGNORECASE)
    if m:
        month_key = m.group(1).lower().rstrip(".")
        month = _MONTHS.get(month_key, "")
        if month:
            return f"{m.group(2)}-{month}"
        return m.group(2)
    year = _YEAR_RE.search(s)
    if year:
        return year.group(0)
    return ""


# ─── Background runner ──────────────────────────────────────────────────────


def _launch_worker(target, args: tuple) -> None:
    """Indirection so tests can monkey-patch to run the worker inline.

    Production: daemon thread. Tests: replace with `lambda t, a: t(*a)`
    to drive the worker synchronously and assert the terminal state."""
    threading.Thread(target=target, args=args, daemon=True).start()


def _finish_parse(
    run_id: str,
    *,
    status: str,
    profile: dict[str, Any] | None,
    error: str | None,
) -> None:
    with SessionLocal() as db:
        run = db.execute(select(ParseRun).where(ParseRun.run_id == run_id)).scalar_one_or_none()
        if run is None:
            log.warning("parse_run=%s: row not found at finish — was it deleted?", run_id)
            return
        run.status = status
        run.profile = profile
        run.error = error
        run.finished_at = datetime.now(UTC)
        db.commit()


def _execute_parse_run(run_id: str, text: str, settings: Settings | None = None) -> None:
    """Background-worker entrypoint.

    Contract: this function MUST write a terminal `ParseRun.status`
    (either `success` or `failed`) on every code path before
    returning. A row that sits at `running` forever is the failure
    mode the worker exists to prevent, and the `try/except/finally`
    structure below is what guarantees it:

      * Happy path → `status=success` with the parsed profile.
      * Caught exception (LLM hang, schema error, anything) →
        `status=failed` with the real exception message.
      * Defensive fallback in `finally` → if neither branch above
        managed to write a terminal status (e.g. the DB blip
        recovered between the two writes), one final write attempts
        to mark the run failed so the polling client doesn't wait
        forever.

    Every step emits an INFO log line tagged with `parse_run=<id>`
    so the operator can grep one run's lifecycle in the Render
    logs.
    """
    tag = f"parse_run={run_id}"
    log.info("%s: worker started (input %d chars)", tag, len(text or ""))
    terminal_written = False
    try:
        profile = parse_resume(text, settings=settings, run_id=tag)
        log.info("%s: parse_resume returned — writing success", tag)
        _finish_parse(
            run_id,
            status=PARSE_STATUS_SUCCESS,
            profile=profile.model_dump(mode="json"),
            error=None,
        )
        terminal_written = True
        log.info("%s: terminal status=success written", tag)
    except Exception as e:  # noqa: BLE001
        # Surface the REAL exception message to the user — they
        # need to know whether the API key is wrong, the file
        # couldn't be read, or the model returned bad JSON.
        log.exception("%s: worker caught unhandled exception", tag)
        message = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        try:
            _finish_parse(
                run_id,
                status=PARSE_STATUS_FAILED,
                profile=None,
                error=f"Parse failed — {message}",
            )
            terminal_written = True
            log.info("%s: terminal status=failed written", tag)
        except Exception:  # noqa: BLE001
            log.exception("%s: failed to record failed status (DB unreachable?)", tag)
    finally:
        # Defensive last-resort write. If BOTH the success and the
        # error branches above somehow didn't write a terminal
        # status (e.g. the success-path DB commit raised AND the
        # error-path commit also raised), this is the row's last
        # chance to leave `running` before the worker thread exits.
        if not terminal_written:
            log.warning("%s: no terminal status was written — writing defensive failed row", tag)
            try:
                _finish_parse(
                    run_id,
                    status=PARSE_STATUS_FAILED,
                    profile=None,
                    error=(
                        "Parse worker exited without recording a result. "
                        "See backend logs for details."
                    ),
                )
                log.info("%s: defensive failed row written from finally", tag)
            except Exception:  # noqa: BLE001
                # Truly unrecoverable — DB is down. The row stays at
                # `running` but the polling client will hit its own
                # ceiling and surface a retry to the user.
                log.exception("%s: even the defensive write failed; row stays at running", tag)


def start_background_parse(
    text: str,
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
) -> str:
    """Create a ParseRun row + spawn a worker. Returns the run_id so
    the HTTP handler can hand it back to the client immediately (202)
    and let the frontend poll for completion.

    `user_id` ownership is set at row-creation time so the polling
    endpoint can filter by it — without that filter a guessed `run_id`
    would leak another user's parsed profile. The caller's `settings`
    is captured here (rather than read inside the worker) so that the
    HTTP-layer dependency override is honoured — `get_settings()`
    inside a background thread sees the raw env, not the FastAPI
    override."""
    run_id = uuid.uuid4().hex
    settings = settings or get_settings()
    with SessionLocal() as db:
        db.add(ParseRun(run_id=run_id, status=PARSE_STATUS_RUNNING, user_id=user_id))
        db.commit()
    _launch_worker(_execute_parse_run, (run_id, text, settings))
    return run_id
