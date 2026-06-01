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
from datetime import UTC, datetime, timedelta
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
    # Personal site / portfolio. Surfaced as a third link slot
    # alongside LinkedIn + GitHub — many resumes list it on the
    # contact line.
    website: str | None = None


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
    # Separate `field_of_study` and `gpa` slots so the UI can edit
    # each independently. The parser used to fold the major into the
    # degree blob; that's now split out.
    field_of_study: str | None = None
    location: str | None = None
    # Canonical date fields — `start` is enrolment, `end` is
    # graduation (or "Present"). `graduation` is the legacy alias
    # kept so DB rows from earlier migrations still validate.
    # `model_post_init` mirrors `end` ↔ `graduation` so both the
    # legacy tailor service (`graduation`) and the new UI (`end`)
    # observe the same value regardless of which side wrote it.
    start: str = ""
    end: str = ""
    graduation: str = Field(default="", description="Legacy alias for `end` (back-compat)")
    gpa: str | None = None
    # Course list when the resume includes a "Relevant Coursework:"
    # block. Empty by default — display + tailoring only render
    # this when non-empty.
    coursework: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        # Keep `end` and `graduation` in sync so callers that read
        # either field see a consistent value.
        if self.end and not self.graduation:
            object.__setattr__(self, "graduation", self.end)
        elif self.graduation and not self.end:
            object.__setattr__(self, "end", self.graduation)


class ProfileSkillGroup(BaseModel):
    """One row in the categorised-skills layout (`Cloud Platforms:
    AWS, Azure`). `category` is the heading from the resume; `items`
    is the comma-separated list under it.

    When the resume's skills are flat (ungrouped), the parser emits
    a single group with `category=None`. The frontend renders the
    category label only when present, so flat-skill resumes still
    look like a plain list."""

    category: str | None = None
    items: list[str] = Field(default_factory=list)


class ProfileProject(BaseModel):
    """One personal / professional project entry. Optional fields are
    null when the resume doesn't surface them — the tailor service
    only renders what's populated.

    `description` is a free-form blurb; `bullets` is the list of
    achievement bullets under the project header. Both can coexist —
    older resumes use one paragraph, newer ones use a name + bullet
    list. The parser fills whichever the source provides."""

    name: str
    description: str = ""
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    link: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class ProfileAchievement(BaseModel):
    """One award / honour / notable accomplishment.

    NOT for certifications — those are a separate, structurally
    different thing (named credentials with an issuer + sometimes a
    credential ID). The parser prompt explicitly tells the model to
    keep them apart so an `AWS Certified Solutions Architect` ends up
    in `certifications`, not here.
    """

    title: str
    description: str = ""
    date: str | None = None


class ProfileCertification(BaseModel):
    """One named credential / licence / certification.

    Distinct from `ProfileAchievement` — certifications have a
    specific issuer (AWS, Microsoft, PMI, the state bar, etc.) and
    often a credential ID; achievements are awards / honours /
    recognitions without that structure. The parser separates them
    on the section heading (`Certifications` / `Licenses` vs
    `Awards` / `Honors`).
    """

    name: str
    issuer: str | None = None
    date: str | None = None
    credential_id: str | None = None


class ProfileLanguage(BaseModel):
    """A spoken / written natural language plus the candidate's
    self-reported proficiency. NOT for programming languages — those
    live in `skills`."""

    name: str
    proficiency: str | None = None


class ProfileVolunteer(BaseModel):
    """Volunteer / community-service experience. Same shape as
    `ProfileExperience` minus the strictness — volunteer entries
    tend to have a single description rather than a bullet list,
    but the parser accepts either."""

    organization: str
    role: str | None = None
    description: str = ""
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    bullets: list[str] = Field(default_factory=list)


class ProfilePublication(BaseModel):
    """A published paper / article / book chapter. `authors` is a
    free-form string so the parser doesn't have to fight ordering
    or et-al formatting."""

    title: str
    venue: str | None = None
    date: str | None = None
    link: str | None = None
    authors: str | None = None


class ProfileAffiliation(BaseModel):
    """Professional affiliation / membership — IEEE, ACM, a state
    bar, an honour society, etc. `role` is `Member` / `Chair` /
    `Treasurer` when the candidate held one."""

    name: str
    role: str | None = None
    date: str | None = None


class ProfileAdditionalSection(BaseModel):
    """Catch-all for unrecognised section headings. Lets unusual
    resumes still surface their content (e.g. `Hobbies`, `Patents`,
    `Open-Source Contributions`, `Conference Talks`) rather than
    silently dropping everything the parser doesn't know how to
    file. `label` is the section heading from the resume; `content`
    is the body as plain text."""

    label: str
    content: str = ""


class Profile(BaseModel):
    """The candidate profile the tailor service runs against. Stored as
    JSON in `candidates.profile` — no migration is needed when fields
    are added below because the column is `JSON`.

    Field order here also serves as the *default* section order in the
    tailored output when the user's original resume doesn't pin a
    different one (see `tailor.py:_SYSTEM_GENERATE`)."""

    name: str
    headline: str | None = None
    # True when `headline` was derived by the parser (most recent
    # role + years of experience) rather than pulled verbatim from
    # the resume. The UI marks an inferred headline so the user
    # knows to edit if it doesn't match how they pitch themselves.
    headline_inferred: bool = False
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: ProfileLinks = Field(default_factory=ProfileLinks)
    summary: str = ""
    # Skills accept either the legacy flat list (one string per
    # entry) OR the categorised shape ([{category, items[]}]) the
    # new parser emits. Pydantic discriminates by element type —
    # legacy DB rows + the tailor service's flat output both still
    # validate cleanly. `flat_skills()` flattens for callers that
    # need a single list (e.g. the candidate fingerprint).
    skills: list[str] | list[ProfileSkillGroup] = Field(default_factory=list)
    experience: list[ProfileExperience] = Field(default_factory=list)
    education: list[ProfileEducation] = Field(default_factory=list)
    projects: list[ProfileProject] = Field(default_factory=list)
    achievements: list[ProfileAchievement] = Field(default_factory=list)
    certifications: list[ProfileCertification] = Field(default_factory=list)
    languages: list[ProfileLanguage] = Field(default_factory=list)
    volunteer: list[ProfileVolunteer] = Field(default_factory=list)
    publications: list[ProfilePublication] = Field(default_factory=list)
    affiliations: list[ProfileAffiliation] = Field(default_factory=list)
    additional_sections: list[ProfileAdditionalSection] = Field(default_factory=list)
    # Order the user's resume presents its sections in, when known.
    # Populated by the LLM parser; the tailor service uses it to
    # mirror the user's section ordering. Free-form strings so a
    # template that uses non-standard headers still survives.
    section_order: list[str] = Field(default_factory=list)

    # ── Compliance / EEO answers (the extension echoes these into ATS forms) ──
    # All saved by the user once in the "Form-filling guide" and stored as plain
    # strings matching common ATS option wording. NEVER parsed from a resume,
    # NEVER inferred. The column is JSON so these add no migration.
    #
    # requires_sponsorship / work_authorization: normal fields, fill when set.
    requires_sponsorship: str = ""  # e.g. "Yes" | "No"
    work_authorization: str = ""  # e.g. "Authorized to work in the US"
    # The EEO four DEFAULT TO BLANK and are filled ONLY when the user explicitly
    # sets them — blank means "leave the form field untouched" (the extension
    # never auto-selects a demographic answer the user didn't choose).
    veteran_status: str = ""  # the standard 4 self-identification options
    disability_status: str = ""  # "Yes" | "No" | "Decline to self-identify"
    race_ethnicity: str = ""
    gender: str = ""

    def flat_skills(self) -> list[str]:
        """Flatten the `skills` field to a plain list of strings.
        Use this in any code path that needs a flat list — the
        candidate fingerprint, the tailor prompt, etc. — so the
        union-shaped `skills` field is transparent downstream."""
        out: list[str] = []
        for item in self.skills:
            if isinstance(item, ProfileSkillGroup):
                out.extend(item.items)
            else:
                out.append(item)
        return out


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
    gpa: str | None = None
    coursework: list[str] = Field(default_factory=list)


class _LLMSkillGroup(BaseModel):
    """Resumes that group skills by category (e.g. Languages, Cloud,
    Frameworks) get returned in this shape — preserved so the model
    doesn't have to lose structure. The downstream Profile model
    accepts the grouped shape natively now (was flattened in an
    earlier version) so the category labels round-trip end-to-end."""

    category: str
    items: list[str] = Field(default_factory=list)


class _LLMProject(BaseModel):
    name: str | None = None
    description: str | None = None
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    link: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class _LLMAchievement(BaseModel):
    title: str | None = None
    description: str | None = None
    date: str | None = None


class _LLMCertification(BaseModel):
    name: str | None = None
    issuer: str | None = None
    date: str | None = None
    credential_id: str | None = None


class _LLMLanguage(BaseModel):
    name: str | None = None
    proficiency: str | None = None


class _LLMVolunteer(BaseModel):
    organization: str | None = None
    role: str | None = None
    description: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    bullets: list[str] = Field(default_factory=list)


class _LLMPublication(BaseModel):
    title: str | None = None
    venue: str | None = None
    date: str | None = None
    link: str | None = None
    authors: str | None = None


class _LLMAffiliation(BaseModel):
    name: str | None = None
    role: str | None = None
    date: str | None = None


class _LLMAdditionalSection(BaseModel):
    label: str | None = None
    content: str | None = None


class _LLMStructuralExtract(BaseModel):
    """The structured output the resume-parse Claude call returns. The
    `skills` field can be either a flat list (most resumes) OR a list
    of category groups (resumes with `Technical Skills:` /
    `Languages:` etc.). Pydantic produces an `anyOf` in the JSON schema
    that Anthropic accepts.

    Contact fields (`email`, `phone`, `location`, `linkedin_url`,
    `github_url`) live on the schema too — required by the PDF path,
    which deliberately does NOT pre-extract text via pdfplumber. The
    text-input path still prefers its regex-extracted contact fields
    (cheap, deterministic) and ignores these; the PDF path uses them.

    `section_order` records the resume's actual section ordering so the
    tailor service can mirror the candidate's voice / structure. Free-
    form strings (we don't constrain via enum) so a template that
    uses non-standard headers like "Selected Work" or "Highlights"
    still round-trips."""

    name: str | None = None
    headline: str | None = None
    summary: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    website_url: str | None = None
    experience: list[_LLMExperience] = Field(default_factory=list)
    education: list[_LLMEducation] = Field(default_factory=list)
    skills: list[str] | list[_LLMSkillGroup] = Field(default_factory=list)
    projects: list[_LLMProject] = Field(default_factory=list)
    achievements: list[_LLMAchievement] = Field(default_factory=list)
    certifications: list[_LLMCertification] = Field(default_factory=list)
    languages: list[_LLMLanguage] = Field(default_factory=list)
    volunteer: list[_LLMVolunteer] = Field(default_factory=list)
    publications: list[_LLMPublication] = Field(default_factory=list)
    affiliations: list[_LLMAffiliation] = Field(default_factory=list)
    additional_sections: list[_LLMAdditionalSection] = Field(default_factory=list)
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
    raw_sink: list[str] | None = None,
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
        llm = _llm_extract_structural(
            text, settings=settings, client=client, run_id=tag, raw_sink=raw_sink
        )
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
    return _apply_headline_inference(_merge(regex_profile, llm))


def _empty_profile() -> Profile:
    return Profile(name="")


def parse_resume_pdf(
    pdf_bytes: bytes,
    *,
    settings: Settings | None = None,
    client: Anthropic | None = None,
    run_id: str | None = None,
    raw_sink: list[str] | None = None,
) -> Profile:
    """PDF-input variant of `parse_resume`.

    **No pre-extraction.** The PDF bytes go straight to Claude as a
    base64 `document` content block. pdfplumber is NOT called on
    this path — text extraction with pdfplumber was producing
    space-stripped output ("AzureDevOps" instead of "Azure DevOps",
    "DataWarehousing" instead of "Data Warehousing") which the LLM
    then echoed in its structural output. Claude's native document
    understanding handles word spacing, multi-column layouts,
    bullets, and tables correctly; reading the PDF natively is what
    makes that work, and any text-extract step in front of it
    defeats the point.

    Contact fields (name, email, phone, location, links) also come
    from the LLM here — there's no regex fallback because there's
    no text view to run regex against. The text-paste path
    (`parse_resume`) still uses the regex contact pass because
    pasted text is, by definition, already correctly spaced.

    Same failure modes as `parse_resume`: any Anthropic error or
    wall-clock timeout returns an empty profile rather than
    fabricating one. The worker catches that and writes
    `status=failed` with the real exception so the user knows.
    """
    tag = f"parse_run={run_id}" if run_id else "parse_run=adhoc"
    settings = settings or get_settings()
    if not pdf_bytes:
        log.info("%s: empty PDF input, returning empty profile", tag)
        return _empty_profile()

    log.info(
        "%s: parse_resume_pdf received %d bytes — sending PDF directly to Claude as document",
        tag,
        len(pdf_bytes),
    )

    if not settings.has_anthropic_key:
        # No key → no LLM, and we refuse to fall back to pdfplumber
        # text. Surface an empty profile and let the worker write
        # `failed` with a clear message rather than serve
        # space-stripped text to the user.
        log.warning("%s: no ANTHROPIC_API_KEY for PDF path; returning empty profile", tag)
        return _empty_profile()

    log.info("%s: starting Anthropic structural extract (PDF document input)", tag)
    try:
        llm = _llm_extract_structural_pdf(
            pdf_bytes,
            settings=settings,
            client=client,
            run_id=tag,
            raw_sink=raw_sink,
        )
    except concurrent.futures.TimeoutError:
        log.warning(
            "%s: PDF LLM call exceeded wall-clock %.1fs; returning empty profile",
            tag,
            _LLM_HARD_TIMEOUT_SECONDS,
        )
        return _empty_profile()
    except Exception as exc:  # noqa: BLE001
        log.exception("%s: PDF LLM extraction failed; returning empty profile: %s", tag, exc)
        return _empty_profile()

    log.info("%s: Anthropic PDF extract returned; building profile from LLM-only result", tag)
    return _apply_headline_inference(_llm_to_profile(llm))


# ─── LLM extraction ─────────────────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "You extract structured fields from a candidate's resume (the "
    "input may be raw text or an attached PDF). Return JSON matching "
    "the provided schema.\n"
    "\n"
    "Be CONSERVATIVE. The cost of being wrong is worse than the cost "
    "of being incomplete:\n"
    "  * Return null for any field you cannot confidently extract.\n"
    "  * Never invent or guess a value. If the resume doesn't clearly "
    "    state the company, title, dates, or location, leave it null.\n"
    "  * Pull values VERBATIM from the resume. Do not paraphrase "
    "    titles or shorten company names.\n"
    "\n"
    "── Completeness ──\n"
    "  * Capture EVERY section that appears in the resume — summary, "
    "    experience, education, skills, projects, achievements, "
    "    certifications, languages, volunteer, publications, "
    "    affiliations — AND record their order in `section_order`. "
    "    Anything you can't fit into one of those buckets goes in "
    "    `additional_sections` as `{label, content}` so the user can "
    "    edit it manually; never silently drop content.\n"
    "  * In `experience`, return ONE entry for EVERY job listed — "
    "    never omit, never merge, never summarise multiple jobs into "
    "    one. If the resume lists six jobs, the experience array has "
    "    six entries. The hard rule: if the candidate's resume names "
    "    a role, it has its own object in `experience`.\n"
    "\n"
    "── Pairing ──\n"
    "  Each `experience` entry is ONE job and only one job. Inside a "
    "  single entry, `company`, `title`, `location`, `start_date`, "
    "  `end_date`, and `description_bullets` must ALL describe the "
    "  SAME job — never pair the title from one job with the company "
    "  from another, never attach bullets from job B to job A. When "
    "  in doubt about which job a bullet belongs to, look at the "
    "  visual grouping in the resume (indentation, blank lines, "
    "  proximity to the job header) and keep the bullet with its "
    "  parent job. A bullet you can't confidently assign to a job is "
    "  dropped, not pasted onto the wrong one.\n"
    "\n"
    "  Company vs. title is the single most-misclassified field "
    "  pair. The COMPANY is the employer / organisation — "
    "  ('Stripe', 'JPMorgan Chase', 'Google', 'Anduril Industries', "
    "  'SMBC'). The TITLE is the role — ('Senior Software Engineer', "
    "  'Data Analyst', 'Product Manager', 'Vice President'). Most "
    "  resume templates put the title on one line and the company "
    "  on the next; if they're on the same line the title is usually "
    "  first (before a comma / pipe / dot) and the company second. "
    "  Never swap them. If a header reads 'Senior Software Engineer "
    "  · Stripe · 2022 – Present', `title` is 'Senior Software "
    "  Engineer', `company` is 'Stripe'.\n"
    "\n"
    "── Layout patterns you MUST handle correctly ──\n"
    "\n"
    "PATTERN A — MULTIPLE ROLES UNDER ONE COMPANY. A single company "
    "header is often followed by TWO OR MORE roles at that same "
    "employer (promotions, internship → full-time, etc.). EVERY ROLE "
    "GETS ITS OWN `experience` ENTRY, all carrying the same company "
    "name. NEVER merge them; NEVER drop the second role; NEVER let "
    "one role's bullets bleed into another. Example layout:\n"
    "\n"
    "    SMBC Manu Bank — Scottsdale, AZ\n"
    "        Senior Data Engineer                  Aug 2023 – Present\n"
    "          • Designed a Medallion Architecture data lake…\n"
    "          • Partnered with stakeholders on PII handling…\n"
    "        Data Engineer Intern                  Jan 2023 – May 2023\n"
    "          • Built an AWS Glue pipeline processing 50TB…\n"
    "          • Optimised PySpark integration into Redshift…\n"
    "\n"
    "  CORRECT output: TWO experience entries — both with "
    "  `company='SMBC Manu Bank'`, both with `location='Scottsdale, "
    "  AZ'`; the first with `title='Senior Data Engineer'` and its "
    "  two bullets; the second with `title='Data Engineer Intern'` "
    "  and its two bullets. If the resume lists Capgemini (one "
    "  role) + Soulpage (two roles) + SMBC (two roles), you emit "
    "  FIVE experience entries — not three, not four.\n"
    "\n"
    "PATTERN B — TAB-SEPARATED LINES. DOCX / structured resumes "
    "often put two pieces of information on the same logical line, "
    "separated by a TAB character so they render left- and right-"
    "aligned. The most common shapes:\n"
    "    Company\\tLocation\n"
    "    Title\\tDates\n"
    "  Recognise the tab and split correctly. `company` is the LEFT "
    "  side of the first line, `location` is the right; `title` is "
    "  the LEFT side of the second line, the date range is the "
    "  right. NEVER swap — the location is not the company, the "
    "  dates are not the title. If a single line reads "
    "  'Senior Data Engineer\\tAug 2023 – Present', `title` is "
    "  'Senior Data Engineer' and the dates feed `start_date` / "
    "  `end_date`. Do NOT put 'Aug 2023 – Present' into `title`.\n"
    "\n"
    "PATTERN C — CATEGORISED SKILLS. When the resume groups skills "
    "by category, EMIT GROUPS, NOT FRAGMENTS. The shape is "
    "`{category, items}` — `category` is the label as written; "
    "`items` is the list of skills under it. Example layout:\n"
    "\n"
    "    Cloud Platforms: AWS (S3, Glue, EMR, Redshift), Azure (Data "
    "    Factory, ADLS, Databricks)\n"
    "    ETL & Data Engineering: Spark, Kafka, Airflow, DBT\n"
    "    Languages: Python, SQL\n"
    "\n"
    "  CORRECT output: three groups —\n"
    "    {category: 'Cloud Platforms', items: ['AWS (S3, Glue, EMR, "
    "    Redshift)', 'Azure (Data Factory, ADLS, Databricks)']}\n"
    "    {category: 'ETL & Data Engineering', items: ['Spark', "
    "    'Kafka', 'Airflow', 'DBT']}\n"
    "    {category: 'Languages', items: ['Python', 'SQL']}\n"
    "\n"
    "  Do NOT shred a category — 'Cloud Platforms: AWS, Azure' is "
    "  ONE group with TWO items, not two top-level entries called "
    "  'Cloud Platforms: AWS' and 'Azure'. When the resume's skills "
    "  list is genuinely ungrouped (no category labels at all), "
    "  return a flat list of strings instead.\n"
    "\n"
    "PATTERN D — CONTENT OVER HEADING WHEN CLASSIFYING CERTS vs. "
    "AWARDS. Resumes sometimes file an AWARD under a 'Certifications' "
    "heading, or a CERTIFICATION under 'Honours'. Classify by what "
    "the line ACTUALLY IS, not by the heading it's filed under:\n"
    "  - 'AWS Certified Data Analytics – Specialty' → certifications "
    "    (named credential with an issuer), even if listed under "
    "    'Awards'.\n"
    "  - '\"Pat on the Back\" Award, issued by VP at Capgemini' → "
    "    achievements (an award / recognition with no issuer org or "
    "    credential ID), even if listed under 'Certifications'.\n"
    "  Rule of thumb: if the line has an `issuer` org (AWS, "
    "  Microsoft, Oracle, PMI…) and reads like a credential, it's a "
    "  CERTIFICATION. If it reads like a recognition / award / honour "
    "  / commendation — even a quirky internal one — it's an "
    "  ACHIEVEMENT.\n"
    "\n"
    "Field-by-field guidance:\n"
    "  - name: the candidate's full name, exactly as written at the "
    "    top of the resume. null if the resume doesn't start with a "
    "    clearly-formatted name.\n"
    "  - headline: a one-line professional tagline from the top of "
    "    the resume (e.g. 'Senior Software Engineer', 'ML Engineer "
    "    · 8 years'). Pull it VERBATIM when the resume has one. "
    "    Leave null when the resume has no headline — downstream "
    "    code will infer one from the most recent role + years of "
    "    experience.\n"
    "  - summary: any 'Summary' / 'Profile' / 'About' / 'Objective' "
    "    block at the top of the resume, verbatim (one paragraph, "
    "    whitespace-normalised). Null when the resume has no such "
    "    block.\n"
    "  - email / phone / location / linkedin_url / github_url / "
    "    website_url: the candidate's contact info, parsed out of "
    "    the contact block (usually under the name). `website_url` "
    "    is a personal site / portfolio (NOT LinkedIn or GitHub).\n"
    "  - experience: see the pairing + completeness rules above. "
    "    `company` is the employer name; `title` is the role; do NOT "
    "    swap them. `start_date` / `end_date` are free-form date "
    "    strings as they appear in the resume (e.g. 'Jan 2022', "
    "    '2022', '2022-01'); end_date is the literal string 'Present' "
    "    for ongoing roles. `description_bullets` is the achievement "
    "    bullets VERBATIM as a list — one string per bullet, stripped "
    "    of the leading bullet glyph (•, –, *, etc.) but with the "
    "    rest of the bullet preserved word-for-word. A multi-line "
    "    bullet stays as ONE entry in the list; don't split it on "
    "    line breaks.\n"
    "  - education: one entry per institution. `school` is the "
    "    institution; `degree` is the credential (B.S., M.A., Ph.D., "
    "    etc.); `field_of_study` is the major (separate field — do "
    "    not pack it into `degree`); `gpa` is the GPA when stated "
    "    (e.g. '3.85/4.0' or '3.85'), null otherwise. "
    "    `start_date` / `end_date` are the attendance window in the "
    "    same free-form date shape used for experience (e.g. 'Aug "
    "    2014' → 'May 2018'). `coursework` is the list of courses "
    "    if the resume includes a 'Relevant Coursework:' block — "
    "    one course per array entry; leave empty when the resume "
    "    doesn't list courses.\n"
    "  - skills: PREFER the grouped `{category, items}` shape when "
    "    the resume groups skills by category. Examples: 'Cloud "
    "    Platforms: Azure, AWS, GCP', 'Languages: Python, Go, Java', "
    "    'Tools: Docker, Kubernetes'. Keep each group's items "
    "    together — do NOT split a category mid-list (e.g. don't "
    "    produce 'Cloud Platforms: Azure (Data Factory' / 'ADLS' as "
    "    separate entries; preserve 'Cloud Platforms' → ['Azure', "
    "    'AWS', 'GCP'] as one group). Only return a flat list of "
    "    strings when the resume itself uses one ungrouped list.\n"
    "  - projects: personal or professional projects under a 'Projects' / "
    "    'Personal Projects' / 'Side Projects' / 'Selected Work' header. "
    "    `name` is the project title; `description` is one or two "
    "    sentences when the resume includes a paragraph blurb (leave "
    "    empty otherwise); `bullets` is the list of achievement / "
    "    feature bullets under the project — one string per bullet, "
    "    same rules as experience bullets; `technologies` is the "
    "    stack as a string list (only if the resume explicitly "
    "    enumerates one); `link` is the URL if one is given. Omit "
    "    the section entirely if the resume has none.\n"
    "  - achievements: AWARDS, HONOURS, RECOGNITIONS — things like "
    "    'Dean's List', 'Employee of the Year', '1st place in X "
    "    competition', 'Fulbright Scholar'. Distinct from project "
    "    bullets and from experience-section achievements — only "
    "    what's filed under its own header like 'Awards', 'Honors', "
    "    'Achievements', 'Recognition'. NOT for certifications — see "
    "    the next field.\n"
    "  - certifications: NAMED CREDENTIALS, LICENSES, professional "
    "    certifications — things with an `issuer` and sometimes a "
    "    credential ID. Examples: 'AWS Certified Solutions Architect "
    "    – Associate' (issuer: Amazon Web Services), 'Project "
    "    Management Professional (PMP)' (issuer: PMI), 'Certified "
    "    Public Accountant' (issuer: AICPA), 'Microsoft Certified: "
    "    Azure Administrator Associate', 'Series 7'. Found under "
    "    headers like 'Certifications', 'Licenses', 'Licenses & "
    "    Certifications', 'Professional Certifications'. `name` is "
    "    the credential title; `issuer` is the org that grants it "
    "    (null if not stated); `date` is the earn / issue date as it "
    "    appears (null if not stated); `credential_id` is the ID "
    "    string when the resume includes one. Omit the section if "
    "    the resume has none.\n"
    "  - languages: SPOKEN / WRITTEN natural languages — English, "
    "    Spanish, Mandarin, Hindi, etc. NOT programming languages "
    "    (those live in `skills`). `name` is the language; "
    "    `proficiency` is the candidate's self-reported level "
    "    ('Native', 'Fluent', 'Conversational', 'B2', etc.) when "
    "    stated. Found under headers like 'Languages', 'Language "
    "    Skills', 'Language Proficiency'.\n"
    "  - volunteer: community-service / non-profit / pro-bono "
    "    experience under headers like 'Volunteer Experience', "
    "    'Community Service', 'Volunteer Work'. Same shape as a job "
    "    entry — `organization` (the org name), `role`, optional "
    "    description / bullets / dates.\n"
    "  - publications: papers, articles, book chapters, posters "
    "    under headers like 'Publications', 'Papers', 'Selected "
    "    Publications'. `title` is the paper title; `venue` is the "
    "    journal / conference / book; `authors` is the verbatim "
    "    author line (string, not a list — preserve the order and "
    "    et-al formatting the candidate used).\n"
    "  - affiliations: professional memberships / affiliations under "
    "    headers like 'Professional Affiliations', 'Memberships', "
    "    'Professional Memberships'. `name` is the organisation "
    "    (IEEE, ACM, the local bar association, etc.); `role` is "
    "    the candidate's role within the org if stated ('Member', "
    "    'Treasurer', etc.).\n"
    "  - additional_sections: any section heading that doesn't fit "
    "    the buckets above (e.g. 'Hobbies', 'Patents', 'Conference "
    "    Talks', 'Open-Source Contributions', 'Coursework'). One "
    "    entry per section, `label` is the heading from the resume, "
    "    `content` is the body as plain text. This is the catch-all "
    "    so unusual resumes don't lose content silently.\n"
    "  - section_order: a list of the resume's section headings in "
    "    the order they appear, lowercased. Use the canonical names "
    "    where possible: 'summary', 'experience', 'projects', "
    "    'skills', 'education', 'achievements', 'certifications', "
    "    'languages', 'volunteer', 'publications', 'affiliations'. "
    "    Custom sections get their `label` (lowercased) in the same "
    "    list. Used by downstream tooling to mirror the candidate's "
    "    preferred ordering.\n"
    "\n"
    "Misclassification traps to avoid:\n"
    "  * A certification (e.g. 'AWS Certified Cloud Practitioner') is "
    "    NOT an achievement. It goes in `certifications`, not "
    "    `achievements`. The rule of thumb: if it has an issuer "
    "    organisation or a credential ID, it's a certification.\n"
    "  * An award (e.g. 'Dean's List, Fall 2022') is NOT a "
    "    certification — it goes in `achievements`.\n"
    "  * A spoken language (e.g. 'Spanish — Fluent') is NOT a skill "
    "    — it goes in `languages`. A programming language IS a skill.\n"
    "  * A volunteer role (e.g. 'Habitat for Humanity, Team Lead') "
    "    is NOT a paid job — it goes in `volunteer`, not "
    "    `experience`.\n"
    "  * Never swap company and title. The title is the role; the "
    "    company is the employer.\n"
    "\n"
    "Output strictly the JSON schema requested — no prose, no markdown."
)


def _llm_extract_structural(
    text: str,
    *,
    settings: Settings,
    client: Anthropic | None = None,
    run_id: str | None = None,
    raw_sink: list[str] | None = None,
) -> _LLMStructuralExtract:
    """Text-input variant: send the resume as plain text. Used by the
    paste path and the DOCX path. `raw_sink` is an optional mutable
    list — when provided, the raw JSON string the model returned
    (BEFORE parsing) gets appended. Used by the worker to persist the
    raw output on the ParseRun row for triage."""
    payload = text[:_LLM_MAX_CHARS]
    user_content = [
        {
            "type": "text",
            "text": (
                "Raw resume text:\n---\n"
                + payload
                + "\n---\n\nReturn the extracted fields as JSON matching the schema."
            ),
        }
    ]
    return _run_llm_extract(
        user_content=user_content,
        kind="text",
        size_note=f"{len(payload)} chars",
        settings=settings,
        client=client,
        run_id=run_id,
        raw_sink=raw_sink,
    )


def _llm_extract_structural_pdf(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    client: Anthropic | None = None,
    run_id: str | None = None,
    raw_sink: list[str] | None = None,
) -> _LLMStructuralExtract:
    """PDF-input variant: hand the raw PDF to Claude as a `document`
    content block. Claude's document understanding handles bullets,
    multi-column layouts, tables, and styled lists far more reliably
    than text we extracted up-front via pdfplumber — which is the
    point of this whole code path.

    The PDF is base64-encoded and inlined into the user-message
    content. The Anthropic SDK + API accepts PDFs natively via this
    shape; no preprocessing on our side beyond the encoding."""
    import base64  # noqa: PLC0415 — keep cold-start light

    data_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    user_content = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": data_b64,
            },
        },
        {
            "type": "text",
            "text": (
                "The attached PDF is the candidate's resume — read the "
                "RENDERED PAGES directly (do NOT treat this as raw text). "
                "Preserve word spacing as it appears visually: 'Azure "
                "DevOps' is two words, not 'AzureDevOps'. Read every page, "
                "every column, every bullet, every table cell.\n"
                "\n"
                "Extract the candidate's contact info into the top-level "
                "`name`, `email`, `phone`, `location`, `linkedin_url`, and "
                "`github_url` fields — there is no separate contact-field "
                "extractor on this path, the LLM is the only source.\n"
                "\n"
                "Critical for THIS document:\n"
                "  * Return ONE experience entry per job, even when the "
                "    candidate has held multiple roles at the same employer "
                "    or moved across employers in succession. If the resume "
                "    lists five jobs, the `experience` array has five "
                "    entries. Do not collapse roles together.\n"
                "  * Pair company / title / dates / bullets WITHIN each job "
                "    — never pair a title from one job with the company "
                "    from another. The company is the employer; the title "
                "    is the role.\n"
                "  * If the document has a Projects section, populate "
                "    `projects`. If it has a Certifications / Licenses "
                "    section, populate `certifications`. If it has an "
                "    Awards / Honors / Achievements section, populate "
                "    `achievements`. Don't drop any of these.\n"
                "\n"
                "Return the extracted fields as JSON matching the schema."
            ),
        },
    ]
    return _run_llm_extract(
        user_content=user_content,
        kind="pdf",
        size_note=f"{len(pdf_bytes)} bytes",
        settings=settings,
        client=client,
        run_id=run_id,
        raw_sink=raw_sink,
    )


def _run_llm_extract(
    *,
    user_content: list[dict],
    kind: str,
    size_note: str,
    settings: Settings,
    client: Anthropic | None,
    run_id: str | None,
    raw_sink: list[str] | None = None,
) -> _LLMStructuralExtract:
    """Shared transport for both the text- and PDF-input variants.
    Hard wall-clock ceiling + abandon-the-thread-on-timeout pattern
    is the same for both — see the long comment block below."""
    api = _build_client(settings, client)

    def _call() -> Any:
        return api.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_config={"format": {"type": "json_schema", "schema": _LLM_SCHEMA}},
        )

    # Hard wall-clock ceiling. The Anthropic SDK's `timeout=` is
    # per-phase and a slow-streaming response can blow past it; this
    # wrapper guarantees control returns to the caller after at most
    # `_LLM_HARD_TIMEOUT_SECONDS`. `concurrent.futures.TimeoutError`
    # propagates up to `parse_resume` / `parse_resume_pdf`, which
    # catches it + falls back to the regex result.
    #
    # The pool is shut down with `wait=False` on timeout. That way
    # we don't wait for the stuck SDK call's thread to finish — it
    # gets abandoned as a daemon-style background thread that'll
    # eventually finish on its own when the underlying HTTP call's
    # per-phase timeout (`_LLM_TIMEOUT_SECONDS`) fires. The worker
    # we care about already moved on.
    tag = run_id or "parse_run=adhoc"
    log.info(
        "%s: messages.create starting (model=%s, kind=%s, payload=%s, hard_timeout=%.1fs)",
        tag,
        MODEL,
        kind,
        size_note,
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
    # Pull the raw JSON text out of the response BEFORE parsing. We
    # log a truncated form so the operator can see exactly what the
    # model returned in the Render logs (one grep on the run_id),
    # and we hand the full text to `raw_sink` so the worker can
    # persist it on the ParseRun row for triage. Storing the raw
    # output is the single most useful debugging hook when a parse
    # comes back "wrong" — it lets the operator distinguish an
    # extraction problem (the model didn't return what we'd hoped)
    # from a mapping / display problem (we lost something between
    # the model output and the `profile` column).
    raw_text = _first_text(response)
    log.info("%s: raw LLM JSON (%d chars): %s", tag, len(raw_text), _truncate(raw_text, 4000))
    if raw_sink is not None:
        raw_sink.append(raw_text)
    return _LLMStructuralExtract.model_validate_json(raw_text)


def _truncate(text: str, limit: int) -> str:
    """Cap log-line length so a huge structured-output payload
    doesn't blow out the log volume on a free-tier hosting plan.
    The full payload is still persisted on the ParseRun row when
    `raw_sink` is wired up — this truncation only affects what
    lands in the streaming Render log."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[+{len(text) - limit} chars]"


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
    """Build the final Profile from a regex contact-field pass + LLM
    structural extract. Used by the TEXT-input path (paste / DOCX);
    the PDF path uses `_llm_to_profile` because it skips pdfplumber
    entirely.

    Contact fields preferred from the regex pass when present (cheap
    and deterministic); LLM values fall in only as a last-resort
    fallback. Every list-typed section comes from the LLM —
    projects, achievements, certifications, languages, volunteer,
    publications, affiliations, additional_sections — the regex
    extractor doesn't populate any of those.
    """
    name = (llm.name or regex_profile.name or "").strip()

    llm_experience = [_to_profile_experience(e) for e in llm.experience]
    llm_experience = [e for e in llm_experience if e is not None]
    experience = llm_experience or regex_profile.experience

    llm_education = [_to_profile_education(e) for e in llm.education]
    llm_education = [e for e in llm_education if e is not None]
    education = llm_education or regex_profile.education

    llm_skills = _to_profile_skills(llm.skills)
    skills = llm_skills or regex_profile.skills

    profile_bits = _build_llm_only_sections(llm)
    links = ProfileLinks(
        linkedin=regex_profile.links.linkedin or profile_bits["linkedin"],
        github=regex_profile.links.github or profile_bits["github"],
        website=profile_bits["website"],  # regex doesn't extract website
    )

    headline = (llm.headline or "").strip() or regex_profile.headline
    summary = (llm.summary or "").strip() or regex_profile.summary

    return Profile(
        name=name,
        headline=headline,
        headline_inferred=False,  # set below by `_apply_headline_inference`
        email=regex_profile.email or profile_bits["email"],
        phone=regex_profile.phone or profile_bits["phone"],
        location=regex_profile.location or profile_bits["location"],
        links=links,
        summary=summary,
        skills=skills,
        experience=experience,
        education=education,
        projects=profile_bits["projects"],
        achievements=profile_bits["achievements"],
        certifications=profile_bits["certifications"],
        languages=profile_bits["languages"],
        volunteer=profile_bits["volunteer"],
        publications=profile_bits["publications"],
        affiliations=profile_bits["affiliations"],
        additional_sections=profile_bits["additional_sections"],
        section_order=profile_bits["section_order"],
    )


def _llm_to_profile(llm: _LLMStructuralExtract) -> Profile:
    """Build a Profile from the LLM's structural extract ALONE — no
    regex source.

    Used by the PDF path, where running any text-extractor would
    defeat the purpose of sending the PDF directly to Claude (the
    pdfplumber output was producing space-stripped words like
    'AzureDevOps' that the LLM then echoed). The contact fields
    (email / phone / location / links) come from the LLM-extracted
    values on `_LLMStructuralExtract` directly.
    """
    name = (llm.name or "").strip()

    experience = [e for e in (_to_profile_experience(x) for x in llm.experience) if e is not None]
    education = [e for e in (_to_profile_education(x) for x in llm.education) if e is not None]
    skills = _to_profile_skills(llm.skills)
    profile_bits = _build_llm_only_sections(llm)

    return Profile(
        name=name,
        headline=(llm.headline or "").strip() or None,
        headline_inferred=False,
        email=profile_bits["email"],
        phone=profile_bits["phone"],
        location=profile_bits["location"],
        links=ProfileLinks(
            linkedin=profile_bits["linkedin"],
            github=profile_bits["github"],
            website=profile_bits["website"],
        ),
        summary=(llm.summary or "").strip() or "",
        skills=skills,
        experience=experience,
        education=education,
        projects=profile_bits["projects"],
        achievements=profile_bits["achievements"],
        certifications=profile_bits["certifications"],
        languages=profile_bits["languages"],
        volunteer=profile_bits["volunteer"],
        publications=profile_bits["publications"],
        affiliations=profile_bits["affiliations"],
        additional_sections=profile_bits["additional_sections"],
        section_order=profile_bits["section_order"],
    )


def _build_llm_only_sections(llm: _LLMStructuralExtract) -> dict:
    """Single place that converts every LLM-only section into its
    Profile-side counterpart. Both `_merge` (text path) and
    `_llm_to_profile` (PDF path) read from this so the two paths
    stay in lock-step — a section added here is automatically
    surfaced on both inputs."""
    return {
        "projects": [p for p in (_to_profile_project(p) for p in llm.projects) if p is not None],
        "achievements": [
            a for a in (_to_profile_achievement(a) for a in llm.achievements) if a is not None
        ],
        "certifications": [
            c for c in (_to_profile_certification(c) for c in llm.certifications) if c is not None
        ],
        "languages": [x for x in (_to_profile_language(x) for x in llm.languages) if x is not None],
        "volunteer": [
            v for v in (_to_profile_volunteer(v) for v in llm.volunteer) if v is not None
        ],
        "publications": [
            p for p in (_to_profile_publication(p) for p in llm.publications) if p is not None
        ],
        "affiliations": [
            a for a in (_to_profile_affiliation(a) for a in llm.affiliations) if a is not None
        ],
        "additional_sections": [
            s for s in (_to_profile_additional(s) for s in llm.additional_sections) if s is not None
        ],
        "section_order": [s.strip().lower() for s in llm.section_order if s and s.strip()],
        "email": (llm.email or "").strip() or None,
        "phone": (llm.phone or "").strip() or None,
        "location": (llm.location or "").strip() or None,
        "linkedin": (llm.linkedin_url or "").strip() or None,
        "github": (llm.github_url or "").strip() or None,
        "website": (llm.website_url or "").strip() or None,
    }


def _to_profile_language(entry: _LLMLanguage) -> ProfileLanguage | None:
    name = (entry.name or "").strip()
    if not name:
        return None
    return ProfileLanguage(name=name, proficiency=(entry.proficiency or "").strip() or None)


def _to_profile_volunteer(entry: _LLMVolunteer) -> ProfileVolunteer | None:
    org = (entry.organization or "").strip()
    role = (entry.role or "").strip()
    if not org and not role:
        # No anchor at all — drop the noise row.
        return None
    return ProfileVolunteer(
        organization=org or role,  # at least one is non-empty per the check
        role=role or None,
        description=(entry.description or "").strip(),
        location=(entry.location or "").strip() or None,
        start_date=(entry.start_date or "").strip() or None,
        end_date=(entry.end_date or "").strip() or None,
        bullets=_normalise_bullets(entry.bullets),
    )


def _to_profile_publication(entry: _LLMPublication) -> ProfilePublication | None:
    title = (entry.title or "").strip()
    if not title:
        return None
    return ProfilePublication(
        title=title,
        venue=(entry.venue or "").strip() or None,
        date=(entry.date or "").strip() or None,
        link=(entry.link or "").strip() or None,
        authors=(entry.authors or "").strip() or None,
    )


def _to_profile_affiliation(entry: _LLMAffiliation) -> ProfileAffiliation | None:
    name = (entry.name or "").strip()
    if not name:
        return None
    return ProfileAffiliation(
        name=name,
        role=(entry.role or "").strip() or None,
        date=(entry.date or "").strip() or None,
    )


def _to_profile_additional(entry: _LLMAdditionalSection) -> ProfileAdditionalSection | None:
    label = (entry.label or "").strip()
    content = (entry.content or "").strip()
    if not label and not content:
        return None
    return ProfileAdditionalSection(label=label or "Additional", content=content)


# ── Headline inference ─────────────────────────────────────────────────────


def _apply_headline_inference(profile: Profile) -> Profile:
    """If the resume didn't surface a headline, derive a suggested
    one from the most recent role + years of experience. Mark it as
    inferred so the UI flags it for the user to confirm or edit.

    Rule: NEVER add seniority adjectives or specialisations that
    aren't on the resume — only restate what's already there. The
    inference output is shape `'<title> · N years experience'`
    (e.g. `'Senior Data Engineer · 7 years experience'`). If the
    candidate has no usable experience data, leave the headline
    null — better to ship nothing than to fabricate.
    """
    if profile.headline and profile.headline.strip():
        # User-provided headline — leave it alone. `headline_inferred`
        # stays False, which is the truthful state.
        return profile

    if not profile.experience:
        # Nothing to infer from.
        return profile

    # Most-recent role: the first entry in `experience` (parser
    # preserves resume order, and resumes are reverse-chronological).
    most_recent = profile.experience[0]
    title = (most_recent.title or "").strip()
    if not title:
        return profile

    years = _estimate_total_experience_years(profile.experience)
    if years is None or years <= 0:
        headline = title
    else:
        headline = f"{title} · {years} year{'s' if years != 1 else ''} experience"
    return profile.model_copy(update={"headline": headline, "headline_inferred": True})


_DATE_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _estimate_total_experience_years(experience: list[ProfileExperience]) -> int | None:
    """Crude estimate: subtract the earliest start year from the
    latest end year (treating 'Present' as the current year). Good
    enough for a headline — within ±1 year — and the user edits if
    it's off. Returns None when no usable dates are found."""
    starts: list[int] = []
    ends: list[int] = []
    for entry in experience:
        for raw in (entry.start, entry.end):
            if not raw:
                continue
            if raw.lower() in {"present", "current", "now"}:
                ends.append(datetime.now(UTC).year)
                continue
            m = _DATE_YEAR_RE.search(raw)
            if m:
                year = int(m.group(0))
                # Heuristic: the EARLIER date is a start; the LATER
                # is an end. We don't bother distinguishing here —
                # we want the overall min and max.
                starts.append(year)
                ends.append(year)
    if not starts or not ends:
        return None
    return max(ends) - min(starts)


def _to_profile_project(entry: _LLMProject) -> ProfileProject | None:
    name = (entry.name or "").strip()
    if not name:
        return None
    return ProfileProject(
        name=name,
        description=(entry.description or "").strip(),
        bullets=_normalise_bullets(entry.bullets),
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


def _to_profile_certification(entry: _LLMCertification) -> ProfileCertification | None:
    name = (entry.name or "").strip()
    if not name:
        return None
    return ProfileCertification(
        name=name,
        issuer=(entry.issuer or "").strip() or None,
        date=(entry.date or "").strip() or None,
        credential_id=(entry.credential_id or "").strip() or None,
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
    bullets = _normalise_bullets(entry.description_bullets)
    return ProfileExperience(
        company=company,
        title=title,
        location=location,
        start=start,
        end=end,
        bullets=bullets[:20],
    )


_EMBEDDED_BULLET_SPLIT_RE = re.compile(r"\n+\s*(?:[•◦▪●‣⁃*–—-]\s+|\(?\d+[\.)]\s+)|(?:\n\s*\n)")


def _normalise_bullets(raw_bullets: list[str]) -> list[str]:
    """Defensive split for the "run-on blob" failure mode where the
    LLM returns concatenated bullets in a single string instead of a
    list. The prompt asks for one-bullet-per-list-item, but if the
    model slips, we'd rather split here than render `"Did X. Did Y.
    Did Z."` as a single bullet on the profile.

    Rules:
      * Strip leading/trailing whitespace per item.
      * Drop empty items.
      * Split any item that contains an embedded newline followed by
        a bullet glyph (`•`, `-`, `*`, numeric `1.` / `1)`) OR a
        blank line — both unambiguous signals of multiple bullets
        glued into one string.
      * Single multi-line bullets without those markers are LEFT
        ALONE — wrapping inside one bullet shouldn't split.
    """
    out: list[str] = []
    for raw in raw_bullets or []:
        if not raw:
            continue
        text = raw.strip()
        if not text:
            continue
        parts = _EMBEDDED_BULLET_SPLIT_RE.split(text)
        for part in parts:
            cleaned = (part or "").strip(" \t\n\r•◦▪●‣⁃*–—-")
            if cleaned:
                out.append(cleaned)
    return out


def _to_profile_education(entry: _LLMEducation) -> ProfileEducation | None:
    school = (entry.school or "").strip()
    degree = (entry.degree or "").strip()
    field = (entry.field_of_study or "").strip()
    if not school and not degree and not field:
        return None
    location = (entry.location or "").strip() or None
    start_raw = (entry.start_date or "").strip()
    end_raw = (entry.end_date or "").strip()
    # Keep full dates ("2022-01", "May 2018", "Present"…) when the
    # model returned them; fall back to just the year for the
    # legacy `graduation` field so the tailor service still has the
    # short form it expects.
    start = _normalise_date(start_raw) if start_raw else ""
    if end_raw.lower() in {"present", "current", "now"}:
        end = "Present"
    else:
        end = _normalise_date(end_raw) if end_raw else ""
    graduation = _extract_graduation_year(end_raw or start_raw)
    coursework = [c.strip() for c in (entry.coursework or []) if c and c.strip()]
    return ProfileEducation(
        school=school,
        # Keep `degree` to just the credential — `field_of_study`
        # has its own slot now so the UI can edit each
        # independently. Legacy rows with a combined "B.S. Computer
        # Science" string still load cleanly via Pydantic.
        degree=degree,
        field_of_study=field or None,
        location=location,
        start=start,
        end=end,
        graduation=graduation,
        gpa=(entry.gpa or "").strip() or None,
        coursework=coursework,
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


def _to_profile_skills(
    skills: list[str] | list[_LLMSkillGroup],
) -> list[str] | list[ProfileSkillGroup]:
    """Convert the LLM's skills field into the Profile shape.

    Rule: PRESERVE category labels when the LLM returned grouped
    skills (e.g. `Cloud Platforms: AWS, Azure` should stay one
    group, not get shredded into two fragments). When the resume
    has no categories, return a flat list of strings — same shape
    legacy code paths expected.

    The Profile model accepts either shape natively (`list[str] |
    list[ProfileSkillGroup]`) so this output round-trips end-to-end.
    """
    # Grouped path: build ProfileSkillGroup rows directly, dedupe
    # within each group case-insensitively while preserving order.
    grouped_present = any(isinstance(s, _LLMSkillGroup) for s in skills)
    if grouped_present:
        groups: list[ProfileSkillGroup] = []
        for item in skills:
            if not isinstance(item, _LLMSkillGroup):
                # Mixed shape (rare): treat stray strings as a
                # category-less group at the end so nothing drops.
                if isinstance(item, str) and item.strip():
                    groups.append(ProfileSkillGroup(category=None, items=[item.strip()]))
                continue
            category = (item.category or "").strip() or None
            cleaned = _dedupe_preserving_order(item.items)
            if cleaned:
                groups.append(ProfileSkillGroup(category=category, items=cleaned))
        return groups

    # Flat path: dedupe + return as plain strings.
    return _dedupe_preserving_order([s for s in skills if isinstance(s, str)])


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        s = (raw or "").strip()
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
    raw_llm_output: dict[str, Any] | None = None,
) -> None:
    """Write the terminal status onto the ParseRun row. `raw_llm_output`
    is the verbatim structured-output JSON the model returned (as a
    parsed dict) — persisted so the operator can triage a bad parse
    without re-running the upload. None means either the LLM branch
    didn't run (regex-only fallback) or the failure happened before
    the LLM call returned."""
    with SessionLocal() as db:
        run = db.execute(select(ParseRun).where(ParseRun.run_id == run_id)).scalar_one_or_none()
        if run is None:
            log.warning("parse_run=%s: row not found at finish — was it deleted?", run_id)
            return
        run.status = status
        run.profile = profile
        run.error = error
        run.raw_llm_output = raw_llm_output
        run.finished_at = datetime.now(UTC)
        db.commit()


def _coerce_raw_output(raw_sink: list[str]) -> dict[str, Any] | None:
    """Convert the raw-output sink (the LLM's verbatim JSON text)
    into a dict suitable for the `raw_llm_output` JSON column. A
    parse failure here is non-fatal — we store the raw text under
    a `_raw` key so the row still carries something to triage,
    rather than losing the payload entirely."""
    if not raw_sink:
        return None
    import json as _json  # noqa: PLC0415

    text = raw_sink[-1]
    try:
        parsed = _json.loads(text)
    except (ValueError, TypeError):
        return {"_raw_unparseable_text": text[:20000]}
    if not isinstance(parsed, dict):
        # Schema returns an object at the top level — anything else
        # is unexpected but worth keeping verbatim for triage.
        return {"_raw_non_dict": parsed}
    return parsed


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
    raw_sink: list[str] = []
    try:
        profile = parse_resume(text, settings=settings, run_id=tag, raw_sink=raw_sink)
        log.info("%s: parse_resume returned — writing success", tag)
        _finish_parse(
            run_id,
            status=PARSE_STATUS_SUCCESS,
            profile=profile.model_dump(mode="json"),
            error=None,
            raw_llm_output=_coerce_raw_output(raw_sink),
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
                raw_llm_output=_coerce_raw_output(raw_sink),
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


def _execute_parse_run_pdf(run_id: str, pdf_bytes: bytes, settings: Settings | None = None) -> None:
    """PDF-input variant of `_execute_parse_run`. Same terminal-status
    guarantees, same `try/except/finally` shape — only the inner
    parse call changes."""
    tag = f"parse_run={run_id}"
    log.info("%s: PDF worker started (input %d bytes)", tag, len(pdf_bytes or b""))
    terminal_written = False
    raw_sink: list[str] = []
    try:
        profile = parse_resume_pdf(pdf_bytes, settings=settings, run_id=tag, raw_sink=raw_sink)
        log.info("%s: parse_resume_pdf returned — writing success", tag)
        _finish_parse(
            run_id,
            status=PARSE_STATUS_SUCCESS,
            profile=profile.model_dump(mode="json"),
            error=None,
            raw_llm_output=_coerce_raw_output(raw_sink),
        )
        terminal_written = True
        log.info("%s: terminal status=success written", tag)
    except Exception as e:  # noqa: BLE001
        log.exception("%s: PDF worker caught unhandled exception", tag)
        message = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        try:
            _finish_parse(
                run_id,
                status=PARSE_STATUS_FAILED,
                profile=None,
                error=f"Parse failed — {message}",
                raw_llm_output=_coerce_raw_output(raw_sink),
            )
            terminal_written = True
            log.info("%s: terminal status=failed written", tag)
        except Exception:  # noqa: BLE001
            log.exception("%s: failed to record failed status (DB unreachable?)", tag)
    finally:
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


def start_background_parse_pdf(
    pdf_bytes: bytes,
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
) -> str:
    """PDF-input twin of `start_background_parse`. Spawns
    `_execute_parse_run_pdf` instead so the worker sends the PDF to
    Claude as a `document` content block — far more reliable than
    text we'd extract upstream."""
    run_id = uuid.uuid4().hex
    settings = settings or get_settings()
    with SessionLocal() as db:
        db.add(ParseRun(run_id=run_id, status=PARSE_STATUS_RUNNING, user_id=user_id))
        db.commit()
    _launch_worker(_execute_parse_run_pdf, (run_id, pdf_bytes, settings))
    return run_id


# How long a row may sit at `running` before the startup sweep treats
# it as orphaned. A real parse finishes well inside a minute (60s hard
# LLM ceiling + a few seconds of bookkeeping), so 5 minutes is
# comfortably past any legitimate in-flight parse — anything older was
# abandoned by a process that died mid-parse.
_ORPHAN_RUNNING_AFTER = timedelta(minutes=5)


def sweep_orphaned_parse_runs() -> int:
    """Mark long-`running` ParseRun rows as failed. Returns the count.

    The worker's try/except/finally guarantees a terminal status as
    long as the *process* survives. The one gap it can't cover is the
    process itself dying mid-parse (OOM, Render cold-start eviction, a
    hard restart): the daemon thread is killed before `finally` runs,
    so the row stays `running` forever and the polling client waits out
    its full ceiling for a parse that can never finish.

    Run this once at startup to reap those: any row still `running` and
    older than `_ORPHAN_RUNNING_AFTER` is flipped to `failed` with a
    clear, user-presentable message. Rows newer than the cutoff are
    left alone — they may belong to a parse the previous process was
    legitimately still running when this one booted (unlikely given a
    restart, but the cutoff makes the sweep safe regardless).

    Best-effort: a DB hiccup here is logged and swallowed so it can
    never keep the API from booting.
    """
    cutoff = datetime.now(UTC) - _ORPHAN_RUNNING_AFTER
    try:
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(ParseRun).where(
                        ParseRun.status == PARSE_STATUS_RUNNING,
                        ParseRun.started_at < cutoff,
                    )
                )
                .scalars()
                .all()
            )
            for run in rows:
                run.status = PARSE_STATUS_FAILED
                run.error = (
                    "Parse was interrupted by a server restart before it "
                    "could finish. Please upload your resume again."
                )
                run.finished_at = datetime.now(UTC)
            if rows:
                db.commit()
                log.warning(
                    "startup sweep: marked %d orphaned parse_run(s) as failed",
                    len(rows),
                )
            return len(rows)
    except Exception:  # noqa: BLE001
        log.exception("startup sweep: failed to reap orphaned parse_runs")
        return 0
