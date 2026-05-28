"""Render a `TailoredResume` to a PDF byte stream with ReportLab.

Built from the SAME shared `resume_layout` block list as the DOCX renderer,
so the two outputs carry identical text — only the styling differs (the
rendering-contract requirement).

ATS conventions (mirrors `docx_export`):
  - Single column. No tables, columns, text boxes, images, or headers/footers.
  - ATS-safe font (Helvetica, a ReportLab base-14 font). Body ~10.5pt,
    name ~20pt. 0.6" margins.
  - Closed-list section headings.
  - Two-line entry blocks: bold line 1, light line 2.
  - Visual mode: a hairline rule under each heading; dates flush-right via a
    right-aligned draw on the same baseline (NOT a table). Plain mode: no
    rules, headings as plain bold text, dates inline after the
    company/institution separated by " | ".

`count_pages()` builds the document and returns the exact page count — used
by `tailor.generate_resume` to enforce the 2-page hard cap.
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib.colors import Color
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
)

from app.services.resume_layout import (
    SEP,
    Bullet,
    Entry,
    Header,
    Heading,
    Para,
    build_blocks,
)
from app.services.tailor import TailoredResume

_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_FONT_OBLIQUE = "Helvetica-Oblique"
_BODY_PT = 10.5
_NAME_PT = 20.0
_HEADLINE_PT = 12.0
_SMALL_PT = 9.5
_MARGIN = 0.6 * inch

_INK = Color(0.13, 0.13, 0.13)
_LIGHT = Color(0.33, 0.33, 0.33)
_RULE = Color(0.6, 0.6, 0.6)


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "body",
        fontName=_FONT,
        fontSize=_BODY_PT,
        leading=_BODY_PT * 1.25,
        alignment=TA_LEFT,
        spaceAfter=2,
        textColor=_INK,
    )
    return {
        "body": base,
        "name": ParagraphStyle(
            "name",
            parent=base,
            fontName=_FONT_BOLD,
            fontSize=_NAME_PT,
            leading=_NAME_PT * 1.1,
            spaceAfter=2,
        ),
        "headline": ParagraphStyle(
            "headline",
            parent=base,
            fontSize=_HEADLINE_PT,
            leading=_HEADLINE_PT * 1.2,
            textColor=_LIGHT,
            spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "contact",
            parent=base,
            fontSize=_SMALL_PT,
            leading=_SMALL_PT * 1.25,
            textColor=_LIGHT,
            spaceAfter=1,
        ),
        "heading": ParagraphStyle(
            "heading",
            parent=base,
            fontName=_FONT_BOLD,
            fontSize=11,
            leading=13,
            spaceBefore=10,
            spaceAfter=3,
            textColor=_INK,
        ),
        "entry1": ParagraphStyle(
            "entry1",
            parent=base,
            fontName=_FONT_BOLD,
            spaceAfter=0,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base,
            leftIndent=12,
            bulletIndent=0,
            spaceAfter=2,
        ),
    }


class HRule(Flowable):
    """A thin horizontal rule under a section heading (visual mode only)."""

    def __init__(self, width: float = 0) -> None:
        super().__init__()
        self.width = width
        self.height = 3

    def wrap(self, avail_w: float, avail_h: float):  # noqa: ANN201
        self.width = avail_w
        return avail_w, self.height

    def draw(self) -> None:
        self.canv.setStrokeColor(_RULE)
        self.canv.setLineWidth(0.5)
        self.canv.line(0, 1, self.width, 1)


class TwoColLine(Flowable):
    """A single line with `left` flush-left and `right` flush-right on the
    SAME baseline — the visual-mode date column. Uses two draw calls on one
    line (no table), so ATS parsers read it linearly as "left  right"."""

    def __init__(self, left: str, right: str) -> None:
        super().__init__()
        self.left = left
        self.right = right
        self.width = 0
        self.height = _SMALL_PT * 1.3

    def wrap(self, avail_w: float, avail_h: float):  # noqa: ANN201
        self.width = avail_w
        return avail_w, self.height

    def draw(self) -> None:
        self.canv.setFillColor(_LIGHT)
        if self.left:
            self.canv.setFont(_FONT, _SMALL_PT)
            self.canv.drawString(0, 2, self.left)
        if self.right:
            self.canv.setFont(_FONT_OBLIQUE, _SMALL_PT)
            self.canv.drawRightString(self.width, 2, self.right)


def _flowables(resume: TailoredResume, *, plain: bool) -> list:
    styles = _styles()
    out: list = []
    for block in build_blocks(resume):
        if isinstance(block, Header):
            out.append(Paragraph(escape(block.name), styles["name"]))
            if block.headline:
                out.append(Paragraph(escape(block.headline), styles["headline"]))
            for line in (block.contact_line, block.links_line):
                if line:
                    out.append(Paragraph(escape(line), styles["contact"]))
        elif isinstance(block, Heading):
            out.append(Paragraph(escape(block.text), styles["heading"]))
            if not plain:
                out.append(HRule())
                out.append(Spacer(1, 3))
        elif isinstance(block, Para):
            out.append(Paragraph(escape(block.text), styles["body"]))
        elif isinstance(block, Entry):
            if block.line1:
                out.append(Paragraph(escape(block.line1), styles["entry1"]))
            if block.left or block.right:
                if plain:
                    text = SEP.join(part for part in (block.left, block.right) if part)
                    out.append(Paragraph(escape(text), styles["contact"]))
                else:
                    out.append(TwoColLine(block.left, block.right))
            out.append(Spacer(1, 3))
        elif isinstance(block, Bullet):
            out.append(Paragraph(f"- {escape(block.text)}", styles["bullet"]))
    return out


def _build(resume: TailoredResume, *, mode: str | None) -> tuple[bytes, int]:
    """Build the PDF and return (bytes, page_count)."""
    chosen = (mode or resume.meta.mode or "visual").lower()
    plain = chosen == "plain"

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title="Tailored resume",
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        id="body",
    )
    doc.addPageTemplates([PageTemplate(id="single", frames=[frame])])
    doc.build(_flowables(resume, plain=plain))
    # BaseDocTemplate.page holds the last page number after a build.
    return buf.getvalue(), max(1, doc.page)


def render_pdf(resume: TailoredResume, *, mode: str | None = None) -> bytes:
    """Render `resume` to PDF bytes. `mode` is "visual" (default) or
    "plain"; falls back to the resume's own `meta.mode`."""
    return _build(resume, mode=mode)[0]


def count_pages(resume: TailoredResume, *, mode: str | None = None) -> int:
    """Return the exact rendered page count for `resume` in `mode`."""
    return _build(resume, mode=mode)[1]


__all__ = ["render_pdf", "count_pages"]
