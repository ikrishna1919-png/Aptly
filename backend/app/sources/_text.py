"""Shared text helpers for ATS adapters."""

from __future__ import annotations

import html
import re

# Tags that introduce a hard structural break — paragraphs, headings,
# list items, table rows, line breaks. Replaced with a newline before tags
# are stripped, so we keep readable structure rather than mashing the whole
# JD onto one line.
_BLOCK_TAG_RE = re.compile(
    r"</?\s*(?:p|div|br|hr|li|tr|table|h[1-6]|ul|ol|blockquote|pre|article|section)" r"\b[^>]*>",
    re.IGNORECASE,
)
# List items become "- " prefixed lines for readability + LLM friendliness.
_LI_OPEN_RE = re.compile(r"<\s*li\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
# Horizontal whitespace — includes NBSP (\xa0) so `&nbsp;` runs are
# normalized to plain spaces. \n is intentionally NOT included so paragraph
# breaks survive.
_HSPACE_RE = re.compile("[ \t\f\v\xa0]+")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")
# Recognizable HTML signal so backfill jobs can be detected cheaply.
_HTML_SIGNAL_RE = re.compile(r"<[a-z!/][^>]*>|&[a-z#0-9]{2,8};", re.IGNORECASE)


def clean_html(s: str | None) -> str:
    """Decode HTML entities, normalize whitespace; *keep* tags.

    Many ATSes serve job descriptions as double-encoded HTML — the
    response body contains literal `&lt;p&gt;…&lt;/p&gt;` text. Storing
    that as-is gives the frontend a wall of escaped markup to render.
    `html.unescape` rewrites the entities back to the tags they
    represent, leaving the markup as real HTML the frontend can then
    sanitize + render.

    The result is intended for storage on `Job.description` and for
    rendering (server-side sanitized) in the UI. The heuristics + the
    AI prompt path want plain text instead — use `strip_html` for
    those.

    Defensive: returns `""` for None, empty strings, AND any non-str
    input (a misbehaving API returning a number / dict for the JD
    field would otherwise crash `html.unescape`).
    """
    if not isinstance(s, str) or not s:
        return ""
    # html.unescape is idempotent; calling it on already-decoded HTML
    # is a no-op. Whitespace is collapsed conservatively so the stored
    # HTML stays compact without breaking <pre>/<code> blocks
    # noticeably — runs of horizontal whitespace become one space,
    # consecutive blank lines become at most two newlines.
    text = html.unescape(s)
    text = _HSPACE_RE.sub(" ", text)
    text = _MANY_NEWLINES_RE.sub("\n\n", text)
    return text.strip()


def strip_html(s: str | None) -> str:
    """Decode entities, strip tags, normalize whitespace — *preserving*
    paragraph and list structure. Empty input returns "".

    Decodes entities BEFORE stripping tags so double-encoded HTML
    (`&lt;p&gt;…&lt;/p&gt;`, common in Greenhouse / Lever responses)
    is handled correctly. The previous order left those tags intact in
    the output because the entity-decode pass ran last, after the tag
    stripper had already passed over the raw text.

    Block-level tags (`<p>`, `<br>`, `<li>`, …) become newlines first
    so paragraphs and bullets survive into the cleaned form, instead
    of collapsing into a single wall of text.

    Defensive against non-str input — same contract as `clean_html`.
    """
    if not isinstance(s, str) or not s:
        return ""

    # Decode first — any `&lt;` etc. in the input is rewritten to its
    # literal tag form before the tag stripper runs. For job postings,
    # the rare downside (a literal `&lt;Tag&gt;` someone wrote as
    # displayable text gets stripped) is far less common than the
    # actual upside (double-encoded JD HTML cleans correctly).
    text = html.unescape(s)
    # Bullets: open <li> becomes "\n- "; close </li> is just stripped along
    # with the rest of the tags below.
    text = _LI_OPEN_RE.sub("\n- ", text)
    # Other block tags become a single newline.
    text = _BLOCK_TAG_RE.sub("\n", text)
    # Strip every remaining tag.
    text = _TAG_RE.sub("", text)

    # Collapse horizontal whitespace runs but keep newlines as structure.
    text = _HSPACE_RE.sub(" ", text)
    # Trim each line.
    text = "\n".join(line.strip() for line in text.split("\n"))
    # Collapse 3+ blank lines to a single blank line between paragraphs.
    text = _MANY_NEWLINES_RE.sub("\n\n", text)

    return text.strip()


def looks_like_html(s: str | None) -> bool:
    """Cheap detector for descriptions that still carry HTML markup or
    encoded entities. Used by the backfill CLI to skip rows that are
    already clean."""
    if not s:
        return False
    return bool(_HTML_SIGNAL_RE.search(s))


# --- Heuristics ---------------------------------------------------------------

_REMOTE_HINTS = ("remote", "work from anywhere", "wfh", "fully remote")
_ONSITE_HINTS = ("on-site", "on site", "onsite", "in office", "in-office")


def infer_remote(location: str | None, description: str | None) -> bool | None:
    """Return True/False if we can tell, else None.

    We only commit to a value when the signal is unambiguous; otherwise we
    leave it unknown — the user explicitly wants us to NOT fake fields.
    """
    haystack = " ".join(s.lower() for s in (location, description) if s)
    if not haystack:
        return None
    has_remote = any(h in haystack for h in _REMOTE_HINTS)
    has_onsite = any(h in haystack for h in _ONSITE_HINTS)
    if has_remote and not has_onsite:
        return True
    if has_onsite and not has_remote:
        return False
    return None


_SPONSOR_YES = (
    "we sponsor visa",
    "visa sponsorship is available",
    "visa sponsorship available",
    "we will sponsor",
    "willing to sponsor",
    "h-1b sponsorship",
    "h1b sponsorship",
    "sponsor work visa",
)
_SPONSOR_NO = (
    "we do not sponsor",
    "no visa sponsorship",
    "unable to sponsor",
    "cannot sponsor",
    "without sponsorship now or in the future",
    "must be authorized to work in the",
    "no sponsorship",
)


def infer_sponsorship(description: str | None) -> bool | None:
    """Return True if the JD explicitly states sponsorship is offered,
    False if it explicitly states it isn't, None otherwise.

    Per CLAUDE.md: never fake the sponsorship flag. Default is unknown."""
    if not description:
        return None
    text = description.lower()
    yes = any(p in text for p in _SPONSOR_YES)
    no = any(p in text for p in _SPONSOR_NO)
    if yes and not no:
        return True
    if no and not yes:
        return False
    return None
