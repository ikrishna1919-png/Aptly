"""extend tailor_runs for the ATS resume hub

Revision ID: 0020_ats_runs_columns
Revises: 0019_tailor_cache_key
Create Date: 2026-05-30

The /ats resume-generation hub reuses the tailor background-run machinery, so
rather than a parallel table we extend `tailor_runs` with the ATS-specific
inputs. ALL new columns are nullable so existing rows + the existing tailor
flow are untouched (additive only).

  option_type            'jd_paste' | 'upload_docx' | 'upload_pdf_fallback'
  uploaded_filename      original upload name (display/triage)
  uploaded_docx_blob     original DOCX bytes, kept for the keyword-injection
                         path (we edit this file in place, preserving format)
  format_selection       'modern' | 'classic' | 'minimal' | 'plain' | 'custom'
  custom_options_json    {base, accent_color, font_family, margins}
  questions_answers_json the 6 customization answers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_ats_runs_columns"
down_revision: str | None = "0019_tailor_cache_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tailor_runs", sa.Column("option_type", sa.String(length=32), nullable=True))
    op.add_column(
        "tailor_runs", sa.Column("uploaded_filename", sa.String(length=256), nullable=True)
    )
    op.add_column("tailor_runs", sa.Column("uploaded_docx_blob", sa.LargeBinary(), nullable=True))
    op.add_column("tailor_runs", sa.Column("format_selection", sa.String(length=32), nullable=True))
    op.add_column("tailor_runs", sa.Column("custom_options_json", sa.JSON(), nullable=True))
    op.add_column("tailor_runs", sa.Column("questions_answers_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tailor_runs", "questions_answers_json")
    op.drop_column("tailor_runs", "custom_options_json")
    op.drop_column("tailor_runs", "format_selection")
    op.drop_column("tailor_runs", "uploaded_docx_blob")
    op.drop_column("tailor_runs", "uploaded_filename")
    op.drop_column("tailor_runs", "option_type")
