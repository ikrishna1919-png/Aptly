"""Render a `TailoredResume` to a DOCX byte stream with clean ATS formatting.

ATS conventions followed:
  - Single column, left-aligned, no tables or columns
  - Standard font (Calibri), 11pt body / 14pt name
  - Standard section headings (Summary / Skills / Experience / Education)
  - Plain bullets, no fancy unicode for skills
  - No headers/footers, no images
"""

from __future__ import annotations

import io
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from app.services.demo_candidate import DEMO_CANDIDATE
from app.services.tailor import TailoredResume


def render_docx(resume: TailoredResume, candidate: dict[str, Any] | None = None) -> bytes:
    cand = candidate or DEMO_CANDIDATE

    doc = Document()

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

    # ── Summary ─────────────────────────────────────────────────────────────
    _section_heading(doc, "Summary")
    doc.add_paragraph(resume.summary)

    # ── Skills ──────────────────────────────────────────────────────────────
    _section_heading(doc, "Skills")
    doc.add_paragraph(", ".join(resume.skills))

    # ── Experience ──────────────────────────────────────────────────────────
    _section_heading(doc, "Experience")
    for exp in resume.experience:
        header_p = doc.add_paragraph()
        title_run = header_p.add_run(f"{exp.title}, {exp.company}")
        title_run.bold = True
        meta = exp.dates
        if exp.location:
            meta = f"{exp.location}  ·  {meta}"
        meta_run = header_p.add_run(f"   {meta}")
        meta_run.italic = True
        meta_run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        meta_run.font.size = Pt(10)
        for bullet in exp.bullets:
            doc.add_paragraph(bullet, style="List Bullet")

    # ── Education ───────────────────────────────────────────────────────────
    _section_heading(doc, "Education")
    for line in resume.education:
        doc.add_paragraph(line)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


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
