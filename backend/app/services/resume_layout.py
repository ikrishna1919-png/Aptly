"""Shared layout model for the tailored resume.

Both renderers (DOCX via python-docx, PDF via ReportLab) build their
output from the SAME ordered list of layout blocks produced here. That
is what guarantees the rendering-contract requirement: *both modes must
contain identical text content and wording — only the visual styling
differs.* If a renderer ever needs different text, it's a bug here, not
in the renderer.

Section order is the spec's closed list, fixed:
    Professional Summary, Skills, Experience, Education, Projects,
    Certifications
Sections with no content are omitted (no empty headings).

Two-line entry blocks (Experience / Education) are:
    line1 (bold):  title  /  degree[, field]
    line2 (light): "{company, location}"  ...  {dates}
In visual mode the renderer flushes `right` to the right margin (a clean
date column); in plain mode it joins `left | right` inline. The TEXT is
identical either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.tailor import TailoredResume

# Closed list of section headings — no other heading may appear.
HEADING_SUMMARY = "Professional Summary"
HEADING_SKILLS = "Skills"
HEADING_EXPERIENCE = "Experience"
HEADING_EDUCATION = "Education"
HEADING_PROJECTS = "Projects"
HEADING_CERTIFICATIONS = "Certifications"

# Plain-text separators — ASCII only. No middots / decorative glyphs, so
# the output stays ATS-safe and survives both docx and pdf encoding.
SEP = " | "


@dataclass
class Header:
    """Document header — name, optional tailored headline, contact +
    links lines. Never bulleted; rendered at the top of page one."""

    name: str
    headline: str
    contact_line: str
    links_line: str


@dataclass
class Heading:
    """A section heading from the closed list above."""

    text: str


@dataclass
class Para:
    """A run of body text (summary, a skills category line, a project
    description)."""

    text: str


@dataclass
class Entry:
    """A two-line entry block. `left` / `right` make up line 2; `right`
    is the date column. `right` (and/or `left`) may be empty."""

    line1: str
    left: str
    right: str


@dataclass
class Bullet:
    """A single achievement bullet. Rendered with a leading "- " in both
    modes (plain ASCII — no unicode bullet glyph), so the text content is
    identical across docx and pdf."""

    text: str


Block = Header | Heading | Para | Entry | Bullet


def _contact_line(resume: TailoredResume) -> str:
    bits = [resume.contact.location, resume.contact.email, resume.contact.phone]
    return SEP.join(b for b in bits if b)


def _links_line(resume: TailoredResume) -> str:
    parts: list[str] = []
    for link in resume.contact.links:
        label = (link.label or "").strip()
        url = (link.url or "").strip()
        if not url:
            continue
        parts.append(f"{label}: {url}" if label else url)
    return SEP.join(parts)


def _experience_dates(start: str, end: str) -> str:
    """Format an experience date range. Uses the word "to" for the range
    (the spec bans en/em dashes for ranges)."""
    start = (start or "").strip()
    end = (end or "").strip()
    if start and end:
        return f"{start} to {end}"
    return start or end


def build_blocks(resume: TailoredResume) -> list[Block]:
    """Flatten a `TailoredResume` into the ordered block list both
    renderers consume. Empty sections are skipped."""
    blocks: list[Block] = [
        Header(
            name=resume.contact.name,
            headline=resume.contact.headline,
            contact_line=_contact_line(resume),
            links_line=_links_line(resume),
        )
    ]

    # Professional Summary
    if resume.summary.strip():
        blocks.append(Heading(HEADING_SUMMARY))
        blocks.append(Para(resume.summary.strip()))

    # Skills — one line per labeled category.
    skill_groups = [g for g in resume.skills if g.items]
    if skill_groups:
        blocks.append(Heading(HEADING_SKILLS))
        for group in skill_groups:
            items = ", ".join(i for i in group.items if i and i.strip())
            if not items:
                continue
            label = (group.category or "").strip()
            blocks.append(Para(f"{label}: {items}" if label else items))

    # Experience — two-line block + bullets, reverse-chronological
    # (the model is instructed to order it; we preserve that order).
    if resume.experience:
        blocks.append(Heading(HEADING_EXPERIENCE))
        for exp in resume.experience:
            left = ", ".join(p for p in (exp.company, exp.location) if p and p.strip())
            blocks.append(
                Entry(
                    line1=exp.title,
                    left=left,
                    right=_experience_dates(exp.start_date, exp.end_date),
                )
            )
            for bullet in exp.bullets:
                if bullet and bullet.strip():
                    blocks.append(Bullet(bullet.strip()))

    # Education
    if resume.education:
        blocks.append(Heading(HEADING_EDUCATION))
        for edu in resume.education:
            line1 = edu.degree
            if edu.field and edu.field.strip():
                line1 = f"{edu.degree}, {edu.field}" if edu.degree else edu.field
            left = ", ".join(p for p in (edu.institution, edu.location) if p and p.strip())
            blocks.append(Entry(line1=line1, left=left, right=edu.graduation_date or ""))

    # Projects — name line, optional description, bullets.
    if resume.projects:
        blocks.append(Heading(HEADING_PROJECTS))
        for proj in resume.projects:
            blocks.append(Entry(line1=proj.name, left="", right=""))
            if proj.description and proj.description.strip():
                blocks.append(Para(proj.description.strip()))
            for bullet in proj.bullets:
                if bullet and bullet.strip():
                    blocks.append(Bullet(bullet.strip()))

    # Certifications — name (+ issuer) with the date in the date column.
    if resume.certifications:
        blocks.append(Heading(HEADING_CERTIFICATIONS))
        for cert in resume.certifications:
            line1 = cert.name
            if cert.issuer and cert.issuer.strip():
                line1 = f"{cert.name}, {cert.issuer}" if cert.name else cert.issuer
            blocks.append(Entry(line1=line1, left="", right=cert.date or ""))

    return blocks


__all__ = [
    "Header",
    "Heading",
    "Para",
    "Entry",
    "Bullet",
    "Block",
    "build_blocks",
    "SEP",
    "HEADING_SUMMARY",
    "HEADING_SKILLS",
    "HEADING_EXPERIENCE",
    "HEADING_EDUCATION",
    "HEADING_PROJECTS",
    "HEADING_CERTIFICATIONS",
]
