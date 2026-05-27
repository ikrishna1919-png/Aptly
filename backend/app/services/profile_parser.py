"""Parse a raw resume text into the structured Profile shape.

The profile editor's "Paste resume to autofill" feature POSTs the user's
raw resume text to `POST /api/admin/profile/parse`, which routes through
this module. We call Claude Sonnet 4.6 with strict structured output and
a TRUTHFUL-ONLY prompt — anything the source doesn't say stays empty.

Cost & latency are bounded:
  * Caller is the single admin user → no rate-limit concerns.
  * Input is capped at `_MAX_RESUME_CHARS` chars (12K, ~3K tokens).
  * `max_tokens=3000` keeps the response size predictable.
  * The Anthropic SDK call has an explicit `_REQUEST_TIMEOUT_SECONDS`
    deadline so we always return a clean 5xx instead of hanging — the
    SDK default is 10 minutes which is way longer than any upstream
    proxy will hold the connection open.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import anthropic
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

MODEL = "claude-sonnet-4-6"

# Tuned for the Render free-tier 100-second request timeout: we want OUR
# timeout to fire first so the user gets a readable 504 rather than a
# Render-issued 502 with an empty body.
_REQUEST_TIMEOUT_SECONDS = 90.0
_MAX_RESUME_CHARS = 12_000
_MAX_OUTPUT_TOKENS = 3000


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


# ─── Typed errors so the API layer can map cleanly to HTTP statuses ─────────


class ResumeParseError(RuntimeError):
    """Base class for any failure during resume parsing."""


class ResumeParseConfigError(ResumeParseError):
    """ANTHROPIC_API_KEY is missing, empty input, etc. — caller error."""


class ResumeParseTimeoutError(ResumeParseError):
    """The Anthropic call didn't complete within `_REQUEST_TIMEOUT_SECONDS`."""


class ResumeParseConnectionError(ResumeParseError):
    """Couldn't reach Anthropic at all (DNS, TLS, dropped connection)."""


# ─── Public API ─────────────────────────────────────────────────────────────


def parse_resume(
    text: str,
    *,
    settings: Settings | None = None,
    client: Anthropic | None = None,
) -> Profile:
    """Send `text` to Claude Sonnet 4.6 and return the parsed Profile.

    Always returns within `_REQUEST_TIMEOUT_SECONDS` + a small overhead:
    raises one of the typed `ResumeParseError` subclasses on any path
    that can't produce a Profile. Never hangs.
    """
    settings = settings or get_settings()
    if not settings.has_anthropic_key:
        raise ResumeParseConfigError(
            "Resume parsing requires ANTHROPIC_API_KEY to be configured on the backend."
        )
    if not text or not text.strip():
        raise ResumeParseConfigError("Resume text is empty.")

    api = _build_client(settings, client)
    clipped = text[:_MAX_RESUME_CHARS]
    truncated = len(text) > _MAX_RESUME_CHARS

    try:
        response = api.messages.create(
            model=MODEL,
            max_tokens=_MAX_OUTPUT_TOKENS,
            # Per-request override of the SDK's 10-minute default. The user
            # waits in the browser for this — we want a fast, clean 5xx if
            # Anthropic is slow, not an indefinite spin.
            timeout=_REQUEST_TIMEOUT_SECONDS,
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
    except anthropic.APITimeoutError as e:
        log.warning("resume parse timed out after %ss", _REQUEST_TIMEOUT_SECONDS)
        raise ResumeParseTimeoutError(
            f"Claude didn't respond within {int(_REQUEST_TIMEOUT_SECONDS)}s. "
            "Try a shorter resume, or retry — the API may be momentarily slow."
        ) from e
    except anthropic.APIConnectionError as e:
        log.warning("resume parse connection error: %s", e)
        raise ResumeParseConnectionError(
            f"Couldn't reach Claude: {e}. Check the backend's network egress and retry."
        ) from e
    except anthropic.APIStatusError as e:
        # 4xx/5xx from Anthropic itself (rate limit, overloaded, refusal,
        # bad request from a malformed schema, etc.). Surface a readable
        # message rather than letting the raw exception bubble.
        log.warning("resume parse API error %s: %s", e.status_code, e)
        raise ResumeParseError(
            f"Anthropic returned an error ({e.status_code}). Retry, or shorten the resume."
        ) from e

    body = _first_text(response)
    try:
        return Profile.model_validate_json(body)
    except Exception as e:  # noqa: BLE001 — pydantic.ValidationError + JSONDecodeError
        log.warning("resume parse: model returned invalid JSON: %s", e)
        raise ResumeParseError(
            "Claude's response didn't match the expected profile shape. Retry — "
            "this usually goes away on the next try."
        ) from e


# ─── Internals ──────────────────────────────────────────────────────────────


def _build_client(settings: Settings, client: Anthropic | None) -> Anthropic:
    if client is not None:
        return client
    from anthropic import Anthropic  # noqa: PLC0415 — lazy import

    # Top-level timeout matches the per-call override — defence in depth in
    # case a caller forgets to pass it.
    return Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )


def _first_text(response: Any) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ResumeParseError("Anthropic response contained no text block")


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
    """Write the terminal status onto a ParseRun row. Silent if the row
    is missing (e.g. operator-deleted) — log + move on rather than
    crash the worker."""
    with SessionLocal() as db:
        run = db.execute(select(ParseRun).where(ParseRun.run_id == run_id)).scalar_one_or_none()
        if run is None:
            log.warning("parse run %s row not found at finish", run_id)
            return
        run.status = status
        run.profile = profile
        run.error = error
        run.finished_at = datetime.now(UTC)
        db.commit()


def _execute_parse_run(run_id: str, text: str, settings: Settings) -> None:
    """Worker entrypoint. Calls `parse_resume` and writes the result —
    success or failure — onto the row. Never raises out: any exception
    after the typed-error catches lands as `status='failed'` with a
    generic error message, never an unhandled crash on the worker
    thread."""
    try:
        profile = parse_resume(text, settings=settings)
        _finish_parse(
            run_id,
            status=PARSE_STATUS_SUCCESS,
            profile=profile.model_dump(mode="json"),
            error=None,
        )
    except ResumeParseError as e:
        # Typed parse errors carry a user-friendly message — surface
        # verbatim so the frontend can show it.
        log.warning("parse run %s failed: %s", run_id, e)
        _finish_parse(
            run_id,
            status=PARSE_STATUS_FAILED,
            profile=None,
            error=str(e),
        )
    except Exception as e:  # noqa: BLE001
        log.exception("parse run %s unexpected failure", run_id)
        try:
            _finish_parse(
                run_id,
                status=PARSE_STATUS_FAILED,
                profile=None,
                error=f"Unexpected parse failure: {e}",
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to record parse-run %s failure — DB unreachable?", run_id)


def start_background_parse(text: str, settings: Settings) -> str:
    """Create a ParseRun row + spawn a worker. Returns the run_id so
    the HTTP handler can hand it back to the client immediately (202)
    and let the frontend poll for completion.

    Raises `ResumeParseConfigError` synchronously for caller-fixable
    issues (missing key, empty input) — those would otherwise just
    show up as `failed` rows for inputs the caller could have caught
    upfront. Everything else (network, timeout, bad JSON) is handled
    asynchronously inside `_execute_parse_run`.
    """
    if not settings.has_anthropic_key:
        raise ResumeParseConfigError(
            "Resume parsing requires ANTHROPIC_API_KEY to be configured on the backend."
        )
    if not text or not text.strip():
        raise ResumeParseConfigError("Resume text is empty.")

    run_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(ParseRun(run_id=run_id, status=PARSE_STATUS_RUNNING))
        db.commit()
    _launch_worker(_execute_parse_run, (run_id, text, settings))
    return run_id
