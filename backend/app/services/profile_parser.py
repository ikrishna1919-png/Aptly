"""Deterministic Python resume parser.

The "Paste resume to autofill" feature POSTs raw pasted text to
`POST /api/admin/profile/parse`. That input has already been
copy-pasted by the user — there's no image, no PDF, no DOCX layout —
so the parsing step is plain text-pattern extraction, not something
that needs an LLM. This module replaces the previous Anthropic-based
parser (which hit 400s and timeouts) with regex + heuristic extraction
that runs in milliseconds on the backend, has no external dependencies,
and never throws on bad input.

Public API and the Pydantic shape are unchanged so the frontend autofill
UI keeps working:

  * `parse_resume(text) -> Profile`   — best-effort extraction; an
    input that doesn't look like a resume returns an empty Profile
    (`name=""`, empty lists, null optionals) instead of raising. The
    frontend reads that as "fill the form manually".
  * `Profile` / `ProfileLinks` / `ProfileExperience` / `ProfileEducation`
    — same `Candidate.profile` shape the tailor service consumes via
    `get_candidate(db)`.

Anthropic stays in use ONLY for the tailoring step (`app/services/
tailor.py`). The resume-parse code path no longer imports `anthropic`
or touches any structured-output schema.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import Settings
from app.database import SessionLocal
from app.models.parse_run import (
    PARSE_STATUS_FAILED,
    PARSE_STATUS_RUNNING,
    PARSE_STATUS_SUCCESS,
    ParseRun,
)

log = logging.getLogger(__name__)

# Defensive cap on input size — the parser is linear-time per line and
# could in theory grind on absurd input. 200K characters is well above
# any real resume; truncate beyond that to keep the worker bounded.
_MAX_RESUME_CHARS = 200_000


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


# ─── Typed errors (kept for backwards compatibility) ────────────────────────


class ResumeParseError(RuntimeError):
    """Base class for resume-parse failures. The deterministic parser
    never raises this — it's retained so callers that previously
    caught `ResumeParseError` keep compiling. Future parse-related
    exceptions should subclass this."""


# ─── Patterns ───────────────────────────────────────────────────────────────


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Phone numbers in their common shapes. Allows `+1 (555) 123-4567`,
# `555.123.4567`, `555-123-4567`, `(555) 123 4567`, with optional
# international prefix. Length-validated by the digit count (10 or 11)
# inside `_extract_phone` to keep this regex permissive.
_PHONE_RE = re.compile(
    r"(?:(?:\+?\d{1,3})[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}",
)

_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9_\-./]+",
    re.IGNORECASE,
)
# `github.com/<user>` but not `<user>.github.io`. Stop at the next
# whitespace or `)` so trailing punctuation doesn't end up in the slug.
_GITHUB_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-./]*)?",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

# Date-range matcher: `Jan 2020 - Mar 2022`, `January 2020 – Present`,
# `2020 – present`, `2020-2022`, `Jan. 2020 to Present`, etc. Captures
# the two endpoints. Uses ASCII hyphen-minus (-), en-dash (–), em-dash
# (—), and the literal word "to" as separators.
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

# Bullet markers a typical resume uses. The exact glyph differs by
# template; covering the common set is enough to recognise the line as
# part of an entry's bullets rather than its header.
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[•◦▪●‣⁃*\-–—])\s+")

# Location heuristic: matches `City, ST` or `City, Country`. Used both
# for the top-of-resume contact location and for per-experience locations.
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

# Section-header recognisers. Each value is a tuple of acceptable
# header strings; the parser matches case-insensitively against the
# whole stripped line so "Skills" and "TECHNICAL SKILLS" both work.
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
    "education": (
        "education",
        "academic background",
        "educational background",
    ),
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


def parse_resume(text: str, *, settings: Settings | None = None) -> Profile:
    """Best-effort parse of pasted resume text. Always returns a Profile —
    fields that can't be confidently extracted are left empty / null and
    the frontend lets the user fill them in. `settings` is accepted to
    keep the call signature backwards-compatible with the previous
    Anthropic-based path; this function ignores it."""
    del settings  # no longer used; kept for caller compatibility
    if not isinstance(text, str) or not text.strip():
        return _empty_profile()

    text = text[:_MAX_RESUME_CHARS]
    lines = [ln.rstrip() for ln in text.splitlines()]

    sections = _segment_sections(lines)
    header_lines = sections.get("_preamble", [])
    # Many resumes don't have an explicit "Contact" section; the contact
    # info sits in the pre-amble before the first detected header.
    # Globs from any section are fine too — pull links + email + phone
    # from the whole text so a layout that puts the LinkedIn URL at the
    # bottom still extracts cleanly.
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


def _empty_profile() -> Profile:
    return Profile(name="")


# ─── Section segmentation ──────────────────────────────────────────────────


def _segment_sections(lines: list[str]) -> dict[str, list[str]]:
    """Walk the lines and chop them into sections by header. Lines
    before the first detected header land under `_preamble` (where the
    contact block usually lives). Unknown headers are folded into the
    most recently-opened section so a niche header like
    "VOLUNTEERING" doesn't make us drop content."""
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
    """Return the canonical section name if `line` looks like a section
    header; None otherwise. Headers are short, mostly word-only lines
    matching one of the known headings (case-insensitive)."""
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
    """Pick the first non-empty pre-amble line that looks like a name:
    no `@`, mostly letters, length 2–60. Falls back to `None` if the
    pre-amble is empty or the first line is junk."""
    for line in preamble_lines[:10]:
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > 60 or len(stripped) < 2:
            continue
        if "@" in stripped or any(ch.isdigit() for ch in stripped):
            continue
        # Strip role tag lines like "Software Engineer" that some
        # templates put first — heuristic: a name usually has at most
        # one comma and at least one capitalised word.
        if not any(w[:1].isupper() for w in stripped.split() if w):
            continue
        return stripped
    return None


def _extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else None


def _extract_phone(text: str) -> str | None:
    """Find the first phone-shaped sequence with exactly 10 or 11
    digits. The regex is permissive on separators; the digit-count
    check rejects accidental matches like ZIP codes or version
    strings."""
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
    """Look for a `City, ST` shape in the first few pre-amble lines.
    The location regex is specific enough that email addresses on the
    same line don't match — so we DON'T skip `@`-bearing lines:
    contact-block templates often pack name + email + phone + location
    onto one line separated by `·` or `|`."""
    for line in preamble_lines[:12]:
        m = _LOCATION_RE.search(line)
        if m:
            return m.group(0).strip()
    return None


# ─── Summary ────────────────────────────────────────────────────────────────


def _extract_summary(lines: list[str]) -> str:
    """Join the non-empty lines of the summary section into one
    whitespace-normalised paragraph. Caps at ~600 chars so a runaway
    section doesn't dominate the profile."""
    body = "\n".join(ln for ln in lines if ln.strip())
    body = re.sub(r"[ \t]+", " ", body).strip()
    if len(body) > 600:
        body = body[:600].rsplit(" ", 1)[0] + "…"
    return body


# ─── Skills ─────────────────────────────────────────────────────────────────


def _extract_skills(lines: list[str]) -> list[str]:
    """Split the skills section on commas / pipes / bullets, dedupe
    preserving order, trim each entry."""
    if not lines:
        return []
    combined = " ".join(ln for ln in lines if ln.strip())
    if not combined:
        return []
    # Strip common bullet markers first so they don't end up inside
    # the first skill's text.
    combined = _BULLET_PREFIX_RE.sub("", combined)
    # Split on commas, pipes, or bullet glyphs. Keep newlines so a
    # one-per-line layout works too — `splitlines()` handled that
    # already; we now split each line on the in-line separators.
    raw_parts = re.split(r"[,|·•/•]|\s{2,}|\s{0,}\n\s{0,}", combined)
    seen: set[str] = set()
    out: list[str] = []
    for part in raw_parts:
        item = part.strip(" .;:\t")
        if not item or len(item) > 80:
            continue
        # Drop trailing parenthetical metadata like `(advanced)`.
        item = re.sub(r"\s*\([^)]*\)\s*$", "", item)
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ─── Experience ────────────────────────────────────────────────────────────


def _extract_experience(lines: list[str]) -> list[ProfileExperience]:
    """Walk the experience section, group lines into entries anchored
    on date-range matches. Each entry contributes one
    `ProfileExperience` row."""
    if not lines:
        return []
    # First pass: pre-compute per-line metadata.
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

    # Second pass: collect entries. A date-range line opens a new entry
    # (or extends an under-construction one when title + company sat on
    # the previous line). Bullet lines belong to the most recent entry.
    entries: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for meta in line_meta:
        if meta["is_blank"]:
            # Blank lines are entry separators — only commit if we have
            # something real buffered AND we've already seen the entry's
            # date (otherwise the buffer is just the previous entry's
            # trailing whitespace).
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
    """Pull title / company / location / dates / bullets out of one
    experience-section entry block."""
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
        # No usable date — skip the entry. The Pydantic model requires
        # `start` and `end`; a date-less block is almost always a
        # leftover summary blurb rather than a real role.
        return None
    start = _normalise_date(date_match.group(1))
    end_raw = date_match.group(2)
    end = _normalise_date(end_raw)
    if end_raw.lower() in {"present", "current", "now"}:
        end = "Present"

    title, company, location = _split_title_company_location(header_lines, date_match.group(0))
    if not title and not company:
        # Couldn't recover either field — better to drop the entry
        # than to ship a `title=""` row the Pydantic model rejects.
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
    """Header lines for an entry tend to use one of:
      "Senior Engineer @ Acme — Detroit, MI"
      "Senior Engineer, Acme"
      "Acme · Senior Engineer"
      "Acme\nSenior Engineer\nJan 2020 – Present"
    Best-effort split. The first separator-tokenised line gives us
    title + company; the location is whatever City-ST shape we find on
    any header line."""
    # Strip the date substring itself from the header lines — it
    # otherwise pollutes the title/company guess.
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

    # Look at each header line: split on common title|company
    # separators and keep the first two-token line.
    for line in cleaned:
        # Drop trailing location, if any, so it doesn't end up as the
        # company field.
        if location and line.endswith(location):
            line = line[: -len(location)].rstrip(" \t,–—-|·•")
        parts = [p.strip() for p in re.split(r"\s+(?:@|at|·|•|\||—|–|,|-)\s+", line) if p.strip()]
        if len(parts) >= 2:
            return parts[0], parts[1], location

    # Two consecutive non-bullet header lines: assume first is one of
    # title/company and the second is the other. We can't tell which
    # is which deterministically; default to (title, company) which
    # matches the most common template.
    non_empty = [c for c in cleaned if c]
    if len(non_empty) >= 2:
        return non_empty[0], non_empty[1], location
    if non_empty:
        return non_empty[0], None, location
    return None, None, location


# ─── Education ──────────────────────────────────────────────────────────────


def _extract_education(lines: list[str]) -> list[ProfileEducation]:
    """Group education-section lines into entries (blank-line separated
    or institution/degree-line anchored) and pull school / degree /
    graduation year out of each."""
    if not lines:
        return []
    # Split into blocks on blank lines.
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

    # If everything ran together as one block but mentions multiple
    # institutions, split again on lines that start a new institution.
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
        # A line that's pure "City, ST" parses as the entry's location
        # — and crucially we do NOT run the degree regex on it,
        # because two-letter state codes like `MA` would otherwise
        # match the bare-letter `M.A.` degree pattern and clobber the
        # real degree on the next line.
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
                # The graduation year is the last YYYY on the line —
                # `2014 - 2018` should yield "2018".
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
    """Drop trailing degree text (", Bachelor's in CS") AND a trailing
    inline location (", Berkeley, CA") from a school line so the
    `school` field is just the institution name. Templates often pack
    "University, City, ST" onto one line and we don't want the city/
    state to leak into the school name."""
    out = line.strip()
    # Trailing `, City, ST` (or `, City, Country`) gets stripped first.
    # The dedicated trailing-anchored regex is stricter than the
    # general `_LOCATION_RE` so it won't eat parts of the institution
    # name itself.
    m_loc = _TRAILING_LOCATION_RE.search(out)
    if m_loc:
        out = out[: m_loc.start()].rstrip(" ,—–-")
    m_deg = _DEGREE_PATTERNS.search(out)
    if m_deg and m_deg.start() > 0:
        return out[: m_deg.start()].rstrip(" ,—–-")
    return out


def _trim_degree_line(line: str, start: int) -> str:
    """Trim a degree line to the degree phrase + nearby qualifier
    (e.g. "B.S. Computer Science"). Strips trailing year + location
    that may have been concatenated."""
    tail = line[start:]
    tail = _YEAR_RE.sub("", tail)
    tail = _LOCATION_RE.sub("", tail)
    tail = re.sub(r"[\s,;–—-]+$", "", tail)
    return tail.strip()


# ─── Date helpers ───────────────────────────────────────────────────────────


def _normalise_date(token: str) -> str:
    """Turn `Jan 2020` / `January 2020` / `2020` / `Present` into the
    `YYYY-MM` / `YYYY` / `Present` form the Profile model expects."""
    if not token:
        return ""
    s = token.strip()
    low = s.lower()
    if low in {"present", "current", "now"}:
        return "Present"
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
    """Worker entrypoint. The deterministic `parse_resume` never
    raises on input — `_execute_parse_run` therefore only ever lands
    on the `failed` branch when something underneath (e.g. the DB)
    blows up, which is the right shape."""
    try:
        profile = parse_resume(text, settings=settings)
        _finish_parse(
            run_id,
            status=PARSE_STATUS_SUCCESS,
            profile=profile.model_dump(mode="json"),
            error=None,
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

    The parse itself runs in milliseconds now (no Anthropic round-trip),
    but the background-job + polling shape is preserved so the
    frontend code path is unchanged and any future heavy parsing
    (e.g. PDF upload) can slot in without revisiting the API contract.
    """
    del settings  # parser doesn't use settings; keep the parameter for
    # backwards compatibility with callers.

    run_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(ParseRun(run_id=run_id, status=PARSE_STATUS_RUNNING))
        db.commit()
    _launch_worker(_execute_parse_run, (run_id, text, None))  # type: ignore[arg-type]
    return run_id
