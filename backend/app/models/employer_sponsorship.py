"""H-1B sponsorship intelligence per employer, sourced from public
DOL LCA disclosure filings.

One row per *normalised* employer name. The normalisation step
(lowercase + strip Inc/LLC/Corp suffixes + collapse whitespace) lets
both sides — the loaded DOL data AND the jobs table's `company`
column — agree on a single key even when the spelling differs
(`Stripe`, `Stripe Inc.`, `STRIPE, INC.` all collapse to `stripe`).

Two signals are computed from these rows, surfaced as badges in the
UI:

  * **Conservative ("Sponsors H-1B")** — `lca_count_12mo` ≥ the
    configured threshold (default 5). High-confidence: an employer
    that filed several LCAs in the last twelve months has an active
    sponsorship pipeline.
  * **Inclusive ("Past H-1B activity")** — at least one LCA in the
    past two-to-three years. Lower-confidence: the employer has
    sponsored at some point, but the activity may be stale.

Companies with no LCA history get NO badge at all — never a negative
one. Stating "does not sponsor" from DOL silence would be misleading,
because the dataset is incomplete and naming mismatches are common.

This file is populated by the `sponsorship-ingest` CLI (one-time
backfill) and refreshed quarterly via the workflow under
`.github/workflows/sponsorship-refresh.yml`.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmployerSponsorship(Base):
    __tablename__ = "employer_sponsorship"

    # `normalized_name` is the join key against the jobs table's
    # normalised `company`. It's the primary key so duplicates are
    # impossible — the CLI's upsert collapses every variant down to a
    # single row.
    normalized_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    # One representative original spelling, picked at load time. Used
    # only for human-readable reports / debugging — the API surfaces
    # signals against the live `Job.company` value, not this column.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Aggregates over the loaded DOL disclosure rows. `12mo` and `3yr`
    # are rolling-window counts computed at load time relative to the
    # `most_recent_filing` date.
    lca_count_12mo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lca_count_3yr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Most recent decision/filing date observed for this employer.
    # Lets the UI surface freshness — a 2018 last-filing is much
    # weaker signal than a 2024 one.
    most_recent_filing: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Approximate diversity of roles sponsored — useful when an
    # employer files a huge volume for a single title vs. spread
    # across many. Counted within the 12-month window.
    distinct_titles_12mo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Which DOL disclosure file(s) the aggregate was built from.
    # Free-form string, e.g. `"FY2024_Q4,FY2024_Q3"`. Helps debug a
    # stale-data complaint without needing to re-derive.
    source_file: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # When the row was last refreshed. Updated on every CLI run.
    last_loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Default threshold for the conservative ("Sponsors H-1B") signal.
# `5` LCAs in the past 12 months is a low-friction signal that the
# employer has an active sponsorship pipeline — high enough to filter
# out one-off filings (which are sometimes for specialised roles that
# never re-open), low enough to catch smaller sponsoring companies.
DEFAULT_CONSERVATIVE_THRESHOLD = 5
