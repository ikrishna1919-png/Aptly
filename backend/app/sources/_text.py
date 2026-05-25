"""Shared text helpers for ATS adapters."""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    """Decode entities and strip tags. Cheap and good enough for keyword
    matching and snippet display."""
    if not s:
        return ""
    text = html.unescape(s)
    text = _TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


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
