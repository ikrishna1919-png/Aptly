"""Named resume formats for the /ats hub.

All four pre-built formats still obey the PR #58 rendering contract (single
column, two-line role blocks, right-aligned dates in non-plain modes, ATS-safe
fonts, no em/en dashes, 2-page cap). They differ ONLY in font family, accent
colour, heading treatment, and spacing scale — captured here as a `FormatSpec`
that both `docx_export` and `pdf_export` consume so the DOCX and PDF stay
byte-for-byte consistent in wording.

`resolve_format(name, custom)` is the single source of truth; "custom" layers
three lightweight controls (accent swatch, sans/serif, margin scale) on top of
a chosen base. This is intentionally NOT a full template editor.
"""

from __future__ import annotations

from dataclasses import dataclass

# Brand blue (CLAUDE.md theme colour #1E6FE0) for accented headings.
_PRIMARY = (0x1E, 0x6F, 0xE0)
_INK = (0x22, 0x22, 0x22)

# Custom-format accent swatches (label -> RGB). Five presets, no free-form
# colour picker (keeps it lightweight + on-palette).
ACCENT_SWATCHES: dict[str, tuple[int, int, int]] = {
    "blue": _PRIMARY,
    "slate": (0x33, 0x41, 0x55),
    "teal": (0x0F, 0x76, 0x6E),
    "plum": (0x6D, 0x28, 0xD9),
    "none": _INK,
}
_MARGINS = {"tight": 0.5, "normal": 0.6, "loose": 0.85}
SANS = "Calibri"
SERIF = "Georgia"

VALID_FORMATS = ("modern", "classic", "minimal", "plain", "custom")


@dataclass(frozen=True)
class FormatSpec:
    name: str
    plain: bool  # plain mode: no rules, dates inline via " | "
    font: str  # body/UI font family
    serif: bool  # hint for the PDF font lookup
    accent: tuple[int, int, int]  # heading ink colour
    heading_rule: bool  # hairline rule under section headings
    section_gap: float  # multiplier on inter-section spacing (minimal = 1.5)
    margins: float  # page margins in inches


_PRESETS: dict[str, FormatSpec] = {
    # Sans + brand-blue accent + heading rules (the polished default).
    "modern": FormatSpec("modern", False, SANS, False, _PRIMARY, True, 1.0, 0.6),
    # Traditional serif, no colour, heading rules.
    "classic": FormatSpec("classic", False, SERIF, True, _INK, True, 1.0, 0.7),
    # Airy sans, no colour, no rules, extra whitespace.
    "minimal": FormatSpec("minimal", False, SANS, False, _INK, False, 1.5, 0.85),
    # Max ATS compatibility — the PR #58 plain mode.
    "plain": FormatSpec("plain", True, SANS, False, _INK, False, 1.0, 0.6),
}


def resolve_format(name: str | None, custom: dict | None = None) -> FormatSpec:
    """Map a format selection (+ optional custom overrides) to a FormatSpec.
    Unknown names fall back to 'modern'."""
    key = (name or "modern").lower()
    if key == "custom":
        c = custom or {}
        base = _PRESETS.get(str(c.get("base", "modern")).lower(), _PRESETS["modern"])
        accent = ACCENT_SWATCHES.get(str(c.get("accent_color", "blue")).lower(), base.accent)
        serif = str(c.get("font_family", "")).lower() == "serif"
        font = SERIF if serif else SANS
        margins = _MARGINS.get(str(c.get("margins", "normal")).lower(), base.margins)
        return FormatSpec(
            name="custom",
            plain=base.plain,
            font=font,
            serif=serif,
            accent=accent,
            heading_rule=base.heading_rule,
            section_gap=base.section_gap,
            margins=margins,
        )
    return _PRESETS.get(key, _PRESETS["modern"])
