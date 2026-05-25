"""Parse a raw resume text into the structured Profile shape.

The profile editor's "Paste resume to autofill" feature POSTs the user's
raw resume text to `POST /api/admin/profile/parse`, which routes through
this module. We call Claude Sonnet 4.6 with strict structured output and
a TRUTHFUL-ONLY prompt — anything the source doesn't say stays empty.

Cost is bounded: caller is the single admin user; we cap the input at
20K chars and don't cache (each paste is unique, and the result is
reviewed before saving anyway).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.services._anthropic_schema import prepare_schema

if TYPE_CHECKING:
    from anthropic import Anthropic

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
_MAX_RESUME_CHARS = 20_000


# ─── Schema (mirrors the Candidate.profile shape used by the tailor service) ─


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


class Profile(BaseModel):
    """The candidate profile the tailor service runs against. Stored as
    JSON in `candidates.profile` (slug='demo' for the single-user phase)."""

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


PROFILE_SCHEMA: dict[str, Any] = prepare_schema(Profile)


# ─── Prompt ─────────────────────────────────────────────────────────────────


_SYSTEM_PARSE = (
    "You are a resume parser. The user pastes raw resume text; you extract "
    "the candidate's information into the JSON schema. RULES — break any "
    "of these and you fail the task:\n"
    "\n"
    "1. TRUTHFUL extraction only. Copy facts straight from the source. NEVER "
    "invent a company, title, date, bullet, skill, school, or credential "
    "that doesn't appear in the input.\n"
    "2. Preserve the candidate's own wording for the summary and bullets. "
    "Don't rewrite for tone. Light reformatting (trimming whitespace, "
    "splitting a bullet list) is fine.\n"
    "3. If a field isn't in the source, leave it empty / null. Do NOT fill "
    "with plausible-sounding placeholders.\n"
    "4. Skills: extract as a deduplicated flat list of distinct items. Drop "
    'soft-skill fluff ("team player", "hard worker"). Pull hard skills, '
    "tools, languages, frameworks, certifications.\n"
    '5. Dates: prefer YYYY-MM; YYYY alone is fine; use "Present" for the '
    "current role's end. If a date is fully absent, leave the string empty.\n"
    "6. Experience: order most-recent first. Bullets are an array of strings "
    "(one bullet per array element). Strip leading bullet markers like '•' "
    "or '-'.\n"
    "\n"
    "Output strictly the JSON schema requested — no prose."
)


# ─── Public API ─────────────────────────────────────────────────────────────


class ResumeParseError(RuntimeError):
    """Raised when parsing can't proceed (no API key) or the model output
    doesn't validate against the Profile schema."""


def parse_resume(
    text: str,
    *,
    settings: Settings | None = None,
    client: Anthropic | None = None,
) -> Profile:
    """Send `text` to Claude Sonnet 4.6 and return the parsed Profile.

    Raises ResumeParseError when ANTHROPIC_API_KEY isn't configured —
    parsing is an LLM-only operation and there's no meaningful demo
    fallback (unlike tailoring, which can deterministically mock from
    the job's detected skills).
    """
    settings = settings or get_settings()
    if not settings.has_anthropic_key:
        raise ResumeParseError(
            "Resume parsing requires ANTHROPIC_API_KEY to be configured on the backend."
        )
    if not text or not text.strip():
        raise ResumeParseError("Resume text is empty.")

    api = _build_client(settings, client)
    clipped = text[:_MAX_RESUME_CHARS]
    truncated = len(text) > _MAX_RESUME_CHARS

    response = api.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PARSE,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "Parse the resume below into the schema. Remember: "
                    "never invent facts; leave fields blank when the source "
                    "is silent.\n\n"
                    "--- RESUME TEXT ---\n" + clipped + ("\n[truncated]" if truncated else "")
                ),
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": PROFILE_SCHEMA}},
    )
    body = _first_text(response)
    return Profile.model_validate_json(body)


# ─── Internals ──────────────────────────────────────────────────────────────


def _build_client(settings: Settings, client: Anthropic | None) -> Anthropic:
    if client is not None:
        return client
    from anthropic import Anthropic  # noqa: PLC0415 — lazy import

    return Anthropic(api_key=settings.anthropic_api_key)


def _first_text(response: Any) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ResumeParseError("Anthropic response contained no text block")
