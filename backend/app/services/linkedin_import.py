"""Parse a LinkedIn data-export ZIP into the Aptly profile shape.

User-initiated, defensible: the user requests their archive from LinkedIn
(Settings → Data Privacy → Get a copy of your data), LinkedIn emails a ZIP,
the user uploads it here. NO scraping, no LinkedIn login, no ToS issue.

We read the standard CSVs (Profile.csv, Positions.csv, Education.csv,
Skills.csv) and map them into the profile schema. Returns a partial profile
the caller merges with the user's existing one (the UI reviews conflicts).
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from typing import Any

log = logging.getLogger(__name__)


def _read_csv(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    """Read a CSV from the archive, tolerant of the folder LinkedIn nests files
    in and of case differences."""
    target = None
    for n in zf.namelist():
        base = n.rsplit("/", 1)[-1].lower()
        if base == name.lower():
            target = n
            break
    if target is None:
        return []
    raw = zf.read(target).decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(raw)))


def _get(row: dict[str, str], *keys: str) -> str:
    for k in keys:
        for actual, val in row.items():
            if actual.strip().lower() == k.lower() and val and val.strip():
                return val.strip()
    return ""


def parse_linkedin_zip(data: bytes) -> dict[str, Any]:
    """Map a LinkedIn export ZIP into a partial profile dict. Best-effort —
    missing files just yield empty sections. Raises ValueError on a non-ZIP."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError("That doesn't look like a ZIP file.") from e

    profile: dict[str, Any] = {}

    # Profile.csv — name, headline, summary, location.
    prof_rows = _read_csv(zf, "Profile.csv")
    if prof_rows:
        r = prof_rows[0]
        first = _get(r, "First Name")
        last = _get(r, "Last Name")
        name = (first + " " + last).strip()
        if name:
            profile["name"] = name
        headline = _get(r, "Headline")
        if headline:
            profile["headline"] = headline
        summary = _get(r, "Summary")
        if summary:
            profile["summary"] = summary
        loc = _get(r, "Geo Location", "Location")
        if loc:
            profile["location"] = loc

    # Positions.csv — experience.
    experience: list[dict[str, Any]] = []
    for r in _read_csv(zf, "Positions.csv"):
        title = _get(r, "Title")
        company = _get(r, "Company Name", "Company")
        if not title and not company:
            continue
        desc = _get(r, "Description")
        experience.append(
            {
                "title": title,
                "company": company,
                "location": _get(r, "Location"),
                "start": _get(r, "Started On", "Start Date"),
                "end": _get(r, "Finished On", "End Date") or "Present",
                "bullets": [b.strip() for b in desc.split("\n") if b.strip()][:6],
            }
        )
    if experience:
        profile["experience"] = experience

    # Education.csv.
    education: list[dict[str, Any]] = []
    for r in _read_csv(zf, "Education.csv"):
        school = _get(r, "School Name", "School")
        if not school:
            continue
        education.append(
            {
                "school": school,
                "degree": _get(r, "Degree Name", "Degree"),
                "field": _get(r, "Notes", "Field Of Study"),
                "start": _get(r, "Start Date"),
                "end": _get(r, "End Date"),
            }
        )
    if education:
        profile["education"] = education

    # Skills.csv.
    skills = [_get(r, "Name", "Skill") for r in _read_csv(zf, "Skills.csv")]
    skills = [s for s in skills if s]
    if skills:
        profile["skills"] = skills[:40]

    return profile


def diff_against_existing(existing: dict[str, Any], imported: dict[str, Any]) -> dict[str, Any]:
    """Classify each imported section as 'new' (existing is empty) or 'conflict'
    (existing already has data) so the UI can let the user choose. Scalar
    fields compare by presence; list sections compare by emptiness."""
    out: dict[str, Any] = {"new": {}, "conflict": {}}
    for key, value in imported.items():
        cur = existing.get(key)
        has_cur = bool(cur) if not isinstance(cur, str) else bool(cur.strip())
        bucket = "conflict" if has_cur else "new"
        out[bucket][key] = {"imported": value, "existing": cur}
    return out
