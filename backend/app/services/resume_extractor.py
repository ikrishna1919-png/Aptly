"""Resume-upload text extractor.

Given a file's bytes + reported filename, extract plain text and
hand it to the existing hybrid parser (`parse_resume`). The parser
itself is unchanged — this module just lifts text out of PDF/DOCX
containers so the same downstream pipeline (regex contact fields +
LLM structural extract) runs on uploads the same way it runs on
pastes.

Public API:
  * `extract_text(filename, data) -> str`  — best-effort extract.
                                             Raises `UnsupportedResumeFile`
                                             for unknown extensions and
                                             `EmptyExtractionError` when
                                             the file parsed cleanly but
                                             produced no text (e.g. a
                                             scanned, image-only PDF
                                             with no OCR layer).
  * `UnsupportedResumeFile`                 — the API surfaces this as
                                             400 with a clear message.
  * `EmptyExtractionError`                  — surfaced as 422 with the
                                             "paste your text instead"
                                             hint.

A corrupt or unreadable PDF/DOCX file raises a generic exception
that the caller catches and reports as a clear "couldn't read this
file" error — better than letting the underlying pdfminer/docx
exception bubble.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import PurePosixPath

log = logging.getLogger(__name__)


# Allowed input file types. Cheap extension check + content-sniff
# happens inside each branch — we trust the extension as a routing
# hint but never as proof.
SUPPORTED_SUFFIXES: tuple[str, ...] = (".pdf", ".docx")

# Hard cap on accepted upload size — 10 MB is well above any real
# resume. Beyond this we 413 before opening the file so a malicious
# upload can't grind the parser.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class UnsupportedResumeFile(ValueError):
    """Raised when the uploaded file's extension isn't `.pdf` or
    `.docx`. The API turns this into a 400 with the allow-list."""


class EmptyExtractionError(ValueError):
    """Raised when extraction completed without raising but produced
    no usable text. Almost always means a scanned / image-only PDF
    or a DOCX with no text-runs. The API surfaces this as 422 with
    a clear "paste your text instead" hint."""


def _suffix(filename: str) -> str:
    """Lowercased extension of `filename`. `PurePosixPath` keeps
    things deterministic even on weird inputs like `resume.PDF` or
    `resume.tar.gz`."""
    return PurePosixPath(filename or "").suffix.lower()


def extract_text(filename: str, data: bytes) -> str:
    """Best-effort plain-text extraction. Output is whitespace-
    normalised but never fabricated — empty input → raises
    `EmptyExtractionError` instead of returning the empty string,
    so the API path can short-circuit to its actionable error
    message rather than handing empty text to the LLM."""
    if not data:
        raise EmptyExtractionError("Uploaded file is empty.")

    suffix = _suffix(filename)
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedResumeFile(
            f"Unsupported file type {suffix!r}. Allowed: " f"{', '.join(SUPPORTED_SUFFIXES)}."
        )

    if suffix == ".pdf":
        text = _extract_pdf(data)
    else:  # ".docx"
        text = _extract_docx(data)

    text = _normalise(text)
    if not text.strip():
        # Most common cause: scanned-image PDF with no text layer.
        # The frontend turns this into a user-facing message
        # asking the user to paste the text directly.
        raise EmptyExtractionError(
            "No text could be extracted from the file. If your resume is a "
            "scanned image or image-only PDF, please paste the text instead."
        )
    return text


# ─── PDF ────────────────────────────────────────────────────────────────────


def _extract_pdf(data: bytes) -> str:
    """`pdfplumber` walks every page + extracts the text layer. A
    page with no text layer (scanned image) returns the empty
    string — collected across all pages we detect that case
    upstream as `EmptyExtractionError`.

    Imported lazily to keep cold start fast and to avoid pulling
    pdfminer into the import graph of anything that doesn't need
    it (notably tests for the parser itself)."""
    import pdfplumber  # noqa: PLC0415

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = []
            for page in pdf.pages:
                try:
                    text = page.extract_text() or ""
                except Exception as e:  # noqa: BLE001
                    # A single bad page shouldn't fail the whole
                    # extract — log + skip, then trust the empty-text
                    # detection upstream.
                    log.warning("pdfplumber: skipping page — %s", e)
                    text = ""
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
    except EmptyExtractionError:
        raise
    except Exception as e:  # noqa: BLE001
        # Wrap pdfminer's various failure modes (PDFSyntaxError,
        # PSEOF, etc.) in a single ValueError the API knows about.
        raise ValueError(f"Couldn't read PDF: {e}") from e


# ─── DOCX ───────────────────────────────────────────────────────────────────


def _extract_docx(data: bytes) -> str:
    """`python-docx` opens the docx, walks paragraphs + table cells.
    Tables show up in some resume templates (date right-aligned in a
    second column) — we extract their text too so the parser sees
    the same content the recruiter would."""
    from docx import Document  # noqa: PLC0415

    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Couldn't read DOCX: {e}") from e

    parts: list[str] = []
    for para in doc.paragraphs:
        if para.text and para.text.strip():
            parts.append(para.text)
    # Tables — preserve cell order, joined with tabs so the parser
    # can still see role/date pairs that some templates render in
    # two columns.
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
            if cells:
                parts.append("\t".join(cells))
    return "\n".join(parts)


# ─── Normalisation ─────────────────────────────────────────────────────────


# Collapse 3+ consecutive newlines into 2 so the section segmenter
# (which uses blank lines to split entries) sees a consistent shape
# across PDF/DOCX/paste inputs. Trailing whitespace per line is
# stripped because PDF extracts often carry hard-wrap padding.
_NEWLINE_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def _normalise(text: str) -> str:
    if not text:
        return ""
    text = _TRAILING_WS_RE.sub("", text)
    text = _NEWLINE_RUN_RE.sub("\n\n", text)
    return text.strip()
