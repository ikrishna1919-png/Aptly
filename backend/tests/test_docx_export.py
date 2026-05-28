"""DOCX renderer tests — covers the ATS rules the layout MUST follow:

- Right-aligned dates/location via a real right-aligned TAB STOP, NOT a
  table, column, or text box.
- No tables, no columns, no text boxes anywhere in the document.
- Standard headings.
- 2-page max — verified via the line-count estimate.
"""

from __future__ import annotations

import io
import re
import zipfile

from docx import Document

from app.services.docx_export import (
    _condense_for_two_pages,
    _estimate_line_count,
    render_docx,
)
from app.services.tailor import ExperienceBullet, TailoredResume


def _resume(experience: list[ExperienceBullet], **overrides) -> TailoredResume:
    defaults: dict = {
        "summary": "Senior backend engineer with strong Python + AWS background.",
        "skills": ["Python", "AWS", "Kafka", "PostgreSQL"],
        "experience": experience,
        "education": ["B.S. CS, CMU (2018)"],
        "ats_notes": "Tailored for ATS — kept relevant skills only.",
    }
    defaults.update(overrides)
    return TailoredResume(**defaults)


def _basic_experience() -> list[ExperienceBullet]:
    return [
        ExperienceBullet(
            company="Forge Labs",
            title="Senior Software Engineer",
            location="San Francisco, CA",
            dates="2023 – Present",
            bullets=[
                "Led migration of billing service to event-driven Kafka; p95 480ms→110ms.",
                "Designed feature-flag platform (FastAPI + Postgres) used by 6 teams.",
            ],
        ),
        ExperienceBullet(
            company="Northwind Analytics",
            title="Software Engineer",
            location="Remote",
            dates="2020 – 2023",
            bullets=["Built data ingestion pipeline (Airflow, Snowflake) — 4B events/day."],
        ),
    ]


# ── Tab-stop / no-tables/columns/text-boxes ────────────────────────────────


def _xml_blob(buf: bytes) -> str:
    """Pull document.xml out of the .docx (which is a zip) so we can grep
    the actual word-processing XML."""
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def test_no_tables_columns_or_text_boxes():
    buf = render_docx(_resume(_basic_experience()))
    xml = _xml_blob(buf)
    # Tables — `<w:tbl>` is the table element name.
    assert "<w:tbl>" not in xml and "<w:tbl " not in xml
    # Columns — `<w:cols w:num="2"/>` or higher.
    cols_match = re.search(r'<w:cols[^>]*w:num="(\d+)"', xml)
    if cols_match:
        assert int(cols_match.group(1)) == 1, "document must be single-column"
    # Text boxes — `<w:txbxContent>` is the text-box-content element.
    assert "txbxContent" not in xml
    # No images either.
    assert "<w:drawing" not in xml and "<pic:pic" not in xml


def test_experience_uses_right_tab_stop_for_dates_and_location():
    buf = render_docx(_resume(_basic_experience()))
    xml = _xml_blob(buf)

    # A right-aligned tab stop is `<w:tab>` (inside `<w:tabs>`) carrying
    # `w:val="right"` AND a numeric `w:pos`. Attribute order isn't fixed in
    # python-docx's output, so match either ordering.
    right_tab_stops = [
        m
        for m in re.findall(r"<w:tab\b[^>]*/>", xml)
        if 'w:val="right"' in m and re.search(r'w:pos="\d+"', m)
    ]
    assert (
        len(right_tab_stops) >= 2
    ), f"expected ≥1 right-aligned tab stop per experience entry; got {len(right_tab_stops)}"

    # The `\t` between left and right runs becomes a `<w:tab/>` element
    # (no `w:val`/`w:pos`). At least one per experience entry header.
    inline_tabs = re.findall(r"<w:tab\s*/>", xml)
    assert len(inline_tabs) >= 2

    # Verify both the title/company text and the date string appear in the
    # rendered DOCX — confirms left+right halves are present.
    doc = Document(io.BytesIO(buf))
    text_blob = "\n".join(p.text for p in doc.paragraphs)
    assert "Senior Software Engineer, Forge Labs" in text_blob
    assert "San Francisco, CA · 2023 – Present" in text_blob
    # And: title appears BEFORE the dates on the same paragraph (left/right
    # split, not separate paragraphs).
    for p in doc.paragraphs:
        if "Forge Labs" in p.text:
            assert p.text.index("Forge Labs") < p.text.index("2023 – Present")
            break


def test_standard_ats_section_headings_present():
    buf = render_docx(_resume(_basic_experience()))
    doc = Document(io.BytesIO(buf))
    text = "\n".join(p.text for p in doc.paragraphs)
    for heading in ("SUMMARY", "SKILLS", "EXPERIENCE", "EDUCATION"):
        assert heading in text


# ── 2-page max ─────────────────────────────────────────────────────────────


def test_estimate_under_two_pages_for_typical_resume():
    resume = _resume(_basic_experience())
    assert _estimate_line_count(resume) <= 100  # 2 pages * 50 lines


def test_estimate_flags_overstuffed_resume():
    """A resume with 6 roles × 8 long bullets each is obviously over."""
    overstuffed = _resume(
        [
            ExperienceBullet(
                company=f"Company {i}",
                title="Senior Software Engineer",
                location="San Francisco, CA",
                dates=f"{2010 + i} – {2010 + i + 2}",
                bullets=[
                    "Long bullet text that goes on for a while describing a "
                    "complex achievement with multiple metrics and outcomes that "
                    f"should wrap to two lines in the final document (#{j})."
                    for j in range(8)
                ],
            )
            for i in range(6)
        ],
        skills=[f"Skill{n}" for n in range(40)],
    )
    assert _estimate_line_count(overstuffed) > 100


def test_condense_brings_overstuffed_within_budget():
    """`_condense_for_two_pages` should drop until estimate ≤ budget,
    starting with extra bullets, then oldest roles."""
    overstuffed = _resume(
        [
            ExperienceBullet(
                company=f"Company {i}",
                title="Senior Software Engineer",
                location="San Francisco, CA",
                dates=f"{2010 + i} – {2010 + i + 2}",
                bullets=[f"Bullet {j} describing a meaningful achievement." for j in range(8)],
            )
            for i in range(6)
        ],
    )

    condensed = _condense_for_two_pages(overstuffed)
    assert _estimate_line_count(condensed) <= 100
    # Must not have invented new content.
    seen_companies = {e.company for e in condensed.experience}
    assert seen_companies.issubset({e.company for e in overstuffed.experience})


def test_condense_preserves_most_recent_role():
    """Recency is signal — drop oldest first, never the most recent."""
    overstuffed = _resume(
        [
            ExperienceBullet(
                company=f"Company {i}",
                title="Engineer",
                dates=f"{2010 + i}",
                bullets=[f"b{j}" for j in range(6)],
            )
            for i in range(6)
        ],
    )
    condensed = _condense_for_two_pages(overstuffed)
    # The first (most recent) entry must survive.
    assert condensed.experience[0].company == "Company 0"


def test_render_docx_honours_two_page_max_under_pressure():
    """End-to-end through render_docx — an overstuffed resume should
    produce a small-enough DOCX. Use realistic-length bullets so the
    line estimate reflects a real-world page count, not a degenerate
    "all bullets are 20 chars long" toy case."""
    long_bullet = (
        "Led a complex multi-quarter platform migration that reduced infrastructure "
        "cost by 30% while improving latency and reliability across services."
    )
    overstuffed = _resume(
        [
            ExperienceBullet(
                company=f"Company {i}",
                title="Senior Engineer",
                location="SF",
                dates=f"{2010 + i} – {2012 + i}",
                bullets=[f"{long_bullet} (#{j})" for j in range(8)],
            )
            for i in range(5)
        ],
        skills=[f"Skill{n}" for n in range(40)],
    )
    # The fixture is well over the budget pre-condensation.
    assert _estimate_line_count(overstuffed) > 100
    buf = render_docx(overstuffed)
    # After render_docx → condense → re-render, the estimate of the
    # surviving (truncated) content must fit.
    doc = Document(io.BytesIO(buf))
    non_empty = sum(1 for p in doc.paragraphs if p.text.strip())
    assert non_empty <= 45, f"expected ≤45 non-empty paragraphs, got {non_empty}"
