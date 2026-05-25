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


def strip_html(s: str | None) -> str:
    """Decode entities, strip tags, normalize whitespace — *preserving*
    paragraph and list structure. Empty input returns "".

    The previous implementation collapsed every whitespace run to a single
    space, which turned a 2000-character JD into one wall of text. This
    version converts block-level tags (`<p>`, `<br>`, `<li>`, …) into
    newlines first so paragraphs and bullets survive into the cleaned form.
    """
    if not s:
        return ""

    # Important: convert tags FIRST, then decode entities. The reverse order
    # would turn `&lt;Tag&gt;` (literal text the author meant to show) into
    # `<Tag>` and then strip it as a phantom tag.

    # Bullets: open <li> becomes "\n- "; close </li> is just stripped along
    # with the rest of the tags below.
    text = _LI_OPEN_RE.sub("\n- ", s)
    # Other block tags become a single newline.
    text = _BLOCK_TAG_RE.sub("\n", text)
    # Strip every remaining tag.
    text = _TAG_RE.sub("", text)
    # Now safe to decode entities — anything decoded here is meant as literal
    # text, not markup.
    text = html.unescape(text)

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
