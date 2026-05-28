"""Render a `TailoredResume` to a DOCX byte stream with clean ATS formatting.

ATS conventions followed:
  - Single column, left-aligned, no tables or columns
  - Standard font (Calibri), 11pt body / 20pt name
  - Standard section headings (Summary / Skills / Experience / Education)
  - No headers/footers, no images, no text boxes
  - **Date + location right-aligned via a right-aligned TAB STOP**, NOT
    tables — `Title, Company \t Location · Dates` on a single line.
  - Output condensed to fit within 2 pages (estimated heuristically;
    aggressive callers can verify exact pages by round-tripping through
    LibreOffice).
"""

from __future__ import annotations

import io
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.shared import Pt, RGBColor

from app.services.demo_candidate import DEMO_CANDIDATE
from app.services.tailor import TailoredResume

# --- Heuristics for 2-page condensation -------------------------------------
#
# python-docx doesn't render to pages, so we can't measure exact page count
# without spinning up Word/LibreOffice. We estimate by line count and trim
# until we're under budget. Numbers tuned against the typical letter page
# (8.5"×11", 1" margins, Calibri 11pt body, 20pt heading): ~50 readable
# lines per page, ~95 characters per line.

_MAX_PAGES = 2
_LINE_CHARS = 90
# Tuned conservatively: letter / 1" margins / Calibri 11pt body / 20pt
# heading / paragraph spacing eats real estate, so a packed page is
# closer to 42 rendered lines than the raw 50 you'd get on plain text.
_LINES_PER_PAGE = 42
# Each paragraph carries vertical overhead (space_before/after) beyond
# the wrapped text. Count one extra "phantom" line per paragraph so the
# estimate accounts for spacing.
_PARA_OVERHEAD = 0.4


def _estimate_line_count(resume: TailoredResume) -> int:
    """Conservative estimate of rendered line count.

    Counts wrapped lines (long bullets/summaries take multiple lines) plus
    structural overhead (section headings, blank lines between entries).
    """

    def para(text: str, indent_pad: int = 0) -> float:
        """Estimated rendered lines for one paragraph — wrapped text plus
        per-paragraph spacing overhead."""
        if not text:
            wrapped = 1
        else:
            wrapped = max(1, (len(text) + indent_pad + _LINE_CHARS - 1) // _LINE_CHARS)
        return wrapped + _PARA_OVERHEAD

    lines = 0.0
    # Header: name + headline + contact + horizontal rule.
    lines += 4 * (1 + _PARA_OVERHEAD)

    # Summary
    lines += para("Summary heading")
    lines += para(resume.summary)

    # Skills
    lines += para("Skills heading")
    lines += para(", ".join(resume.skills))

    # Experience
    lines += para("Experience heading")
    for exp in resume.experience:
        lines += para(f"{exp.title}, {exp.company}")
        for bullet in exp.bullets:
            lines += para(bullet, indent_pad=2)

    # Education
    lines += para("Education heading")
    for ed in resume.education:
        lines += para(ed)

    return int(lines + 0.5)


def _condense_for_two_pages(resume: TailoredResume) -> TailoredResume:
    """Trim bullets and oldest entries until the estimate fits 2 pages.

    Trim order: longest-bulleted role first (cap to 3 bullets per role),
    then drop the oldest experience entry, then trim education to one
    entry. We never invent or rewrite text — only drop.
    """
    budget = _MAX_PAGES * _LINES_PER_PAGE
    out = resume.model_copy(deep=True)

    # 1) Cap bullets per role at 3 (most-recent role keeps up to 5).
    if _estimate_line_count(out) > budget:
        for i, exp in enumerate(out.experience):
            cap = 5 if i == 0 else 3
            if len(exp.bullets) > cap:
                exp.bullets = exp.bullets[:cap]
            if _estimate_line_count(out) <= budget:
                return out

    # 2) Drop the oldest experience entries one at a time (keep at least 1).
    while _estimate_line_count(out) > budget and len(out.experience) > 1:
        out.experience.pop()

    # 3) Trim education to one entry.
    if _estimate_line_count(out) > budget and len(out.education) > 1:
        out.education = out.education[:1]

    # 4) Last resort: cap to 2 bullets per role.
    while _estimate_line_count(out) > budget:
        trimmed = False
        for exp in out.experience:
            if len(exp.bullets) > 2:
                exp.bullets.pop()
                trimmed = True
                if _estimate_line_count(out) <= budget:
                    return out
        if not trimmed:
            break

    return out


# --- DOCX construction ------------------------------------------------------


def render_docx(resume: TailoredResume, candidate: dict[str, Any] | None = None) -> bytes:
    cand = candidate or DEMO_CANDIDATE
    condensed = _condense_for_two_pages(resume)

    doc = Document()

    # Standard letter, 1" margins (python-docx defaults).
    section = doc.sections[0]
    usable_width = section.page_width - section.left_margin - section.right_margin
    right_tab_pos = usable_width  # right-edge of text area

    # Base font.
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    # ── Header: name + contact ──────────────────────────────────────────────
    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    name_run = name_p.add_run(cand["name"])
    name_run.bold = True
    name_run.font.size = Pt(20)

    if cand.get("headline"):
        headline_p = doc.add_paragraph()
        headline_run = headline_p.add_run(cand["headline"])
        headline_run.font.size = Pt(12)
        headline_run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    contact_bits: list[str] = []
    for field in ("email", "phone", "location"):
        if cand.get(field):
            contact_bits.append(cand[field])
    if contact_bits:
        contact_p = doc.add_paragraph(" · ".join(contact_bits))
        contact_p.runs[0].font.size = Pt(10)
        contact_p.runs[0].font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    _h_rule(doc)

    # Section order: honour the model's `section_order` when it pinned
    # one, otherwise fall back to the canonical ordering. Sections with
    # no content are skipped so an empty Projects array doesn't print
    # a bare heading. Unknown identifiers in the user's order list are
    # ignored — keeps the renderer robust to a future spelling drift.
    default_order = (
        "summary",
        "skills",
        "experience",
        "projects",
        "education",
        "achievements",
    )
    order = [s for s in (condensed.section_order or []) if s in default_order]
    if not order:
        order = list(default_order)
    # Ensure every supported section gets a chance to render even if
    # the model left a key out of the order list.
    for key in default_order:
        if key not in order:
            order.append(key)

    for section in order:
        if section == "summary" and condensed.summary:
            _section_heading(doc, "Summary")
            doc.add_paragraph(condensed.summary)
        elif section == "skills" and condensed.skills:
            _section_heading(doc, "Skills")
            doc.add_paragraph(", ".join(condensed.skills))
        elif section == "experience" and condensed.experience:
            _section_heading(doc, "Experience")
            for exp in condensed.experience:
                right = " · ".join(part for part in (exp.location, exp.dates) if part)
                _two_column_line(
                    doc,
                    left_text=f"{exp.title}, {exp.company}",
                    right_text=right,
                    right_tab_pos=right_tab_pos,
                    left_bold=True,
                )
                for bullet in exp.bullets:
                    doc.add_paragraph(bullet, style="List Bullet")
        elif section == "projects" and condensed.projects:
            _section_heading(doc, "Projects")
            for proj in condensed.projects:
                right = proj.dates or ""
                _two_column_line(
                    doc,
                    left_text=proj.name,
                    right_text=right,
                    right_tab_pos=right_tab_pos,
                    left_bold=True,
                )
                if proj.description:
                    doc.add_paragraph(proj.description)
                if proj.technologies:
                    p = doc.add_paragraph()
                    tech_run = p.add_run("Tech: " + ", ".join(proj.technologies))
                    tech_run.font.size = Pt(10)
                    tech_run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                if proj.link:
                    link_p = doc.add_paragraph(proj.link)
                    link_p.runs[0].font.size = Pt(10)
                    link_p.runs[0].font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        elif section == "education" and condensed.education:
            _section_heading(doc, "Education")
            for line in condensed.education:
                doc.add_paragraph(line)
        elif section == "achievements" and condensed.achievements:
            _section_heading(doc, "Achievements")
            for ach in condensed.achievements:
                right = ach.date or ""
                _two_column_line(
                    doc,
                    left_text=ach.title,
                    right_text=right,
                    right_tab_pos=right_tab_pos,
                    left_bold=True,
                )
                if ach.description:
                    doc.add_paragraph(ach.description)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --- Lower-level helpers ----------------------------------------------------


def _two_column_line(
    doc: Any,
    *,
    left_text: str,
    right_text: str,
    right_tab_pos: int,
    left_bold: bool = False,
) -> None:
    """Single paragraph with `left_text` on the left and `right_text`
    right-aligned via a right-aligned tab stop. NO tables, NO columns,
    NO text boxes — ATS parsers walk left-to-right and break on those.

    Uses a real `<w:tab w:val="right">` so Word/LibreOffice will visually
    right-align `right_text` to the page margin.
    """
    p = doc.add_paragraph()
    if right_tab_pos:
        p.paragraph_format.tab_stops.add_tab_stop(right_tab_pos, WD_TAB_ALIGNMENT.RIGHT)

    left_run = p.add_run(left_text)
    if left_bold:
        left_run.bold = True

    if right_text:
        p.add_run("\t")
        right_run = p.add_run(right_text)
        right_run.italic = True
        right_run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        right_run.font.size = Pt(10)


def _section_heading(doc: Any, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x22, 0x22, 0x22)


def _h_rule(doc: Any) -> None:
    p = doc.add_paragraph()
    p_pr = p._p.get_or_add_pPr()
    from docx.oxml import OxmlElement  # noqa: PLC0415
    from docx.oxml.ns import qn  # noqa: PLC0415 — needed only here

    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "888888")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


# Re-export for tests so they don't have to dip into _-prefixed names.
__all__ = ["render_docx", "_estimate_line_count", "_condense_for_two_pages"]
