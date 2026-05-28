"""Sponsorship-intelligence lookup + company-name normalisation.

Two consumers:

  * **CLI ingest** (`app.cli.sponsorship_ingest`): reads a DOL LCA
    disclosure CSV, normalises employer names, computes the rolling-
    window aggregates, and upserts rows into `employer_sponsorship`.
  * **Jobs API** (`app.api.jobs.list_jobs`): takes the live set of
    `Job.company` values, normalises each one the same way, and
    looks them up in bulk so every JobOut can carry the two signals
    without an N+1 round trip.

The normalisation must be *exactly the same* on both sides — a single
mismatched suffix-strip rule would mean Stripe's filings live under
`stripe` while the job ad reads `stripe-inc`, and the badges would
never light up. The set of suffixes / punctuation / whitespace rules
is kept in one helper, `normalize_company_name`, with a test that
pins the equivalence classes.

Companies in the jobs table that *don't* match any sponsorship row
are logged (deduped) so the operator can investigate naming gaps.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employer_sponsorship import (
    DEFAULT_CONSERVATIVE_THRESHOLD,
    EmployerSponsorship,
)

log = logging.getLogger(__name__)


# ─── Normalisation ──────────────────────────────────────────────────────────

# Common corporate-form suffixes stripped from the right edge before
# the join key is built. The list isn't exhaustive — it's the set
# that actually causes false-negatives in practice on the DOL data.
# Each entry is a whole-word match, case-insensitive, and may carry a
# trailing period.
_COMPANY_SUFFIXES: tuple[str, ...] = (
    "inc",
    "incorporated",
    "llc",
    "l.l.c",
    "l l c",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "co",
    "company",
    "plc",
    "lp",
    "llp",
    "gmbh",
    "ag",
    "ab",
    "sa",
    "nv",
    "bv",
    "pte",
    "pte ltd",
    "pty",
    "pty ltd",
)

# Strip punctuation that doesn't carry identity information.
# Apostrophes are kept (e.g. McDonald's) — they're meaningful and
# rarely the cause of false negatives.
_PUNCT_RE = re.compile(r"[.,;:!?()\[\]{}/\\\"]+")
# `&` and `+` collapse to ` and ` so "AT&T" and "AT and T" agree.
_AMPERSAND_RE = re.compile(r"\s*[&+]\s*")
# Collapse runs of whitespace + hyphens into single spaces. We
# treat hyphens as separators here so `Bank-of-America` matches
# `Bank of America`.
_SEPARATOR_RE = re.compile(r"[\s\-_]+")


def normalize_company_name(name: str) -> str:
    """Return a stable join-key for `name`. Empty / falsy input → ``""``.

    Rules (each applied in order):
      1. Lowercase + strip leading/trailing whitespace.
      2. Replace `&`/`+` with ` and `.
      3. Drop punctuation (commas, periods, parens, slashes…). Keep
         apostrophes — they're meaningful and rarely the source of
         false negatives.
      4. Collapse runs of whitespace/hyphens/underscores into single
         spaces.
      5. Iteratively strip recognised corporate suffixes from the
         right edge (e.g. `stripe inc` → `stripe`, `acme co ltd` →
         `acme`). The iteration handles stacked suffixes like
         `... pte ltd`.
      6. Strip again to remove the trailing space the suffix-strip
         leaves behind.

    The function is deterministic and pure — same input, same output,
    forever — which is what lets the DOL load and the jobs API agree
    on a single key.
    """
    if not name:
        return ""
    s = name.lower().strip()
    s = _AMPERSAND_RE.sub(" and ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _SEPARATOR_RE.sub(" ", s).strip()
    # Repeated suffix-strip handles stacked forms ("co ltd", "pte ltd").
    # Bounded loop so a pathological input can't spin.
    for _ in range(4):
        stripped = _strip_one_suffix(s)
        if stripped == s:
            break
        s = stripped
    return s.strip()


def _strip_one_suffix(s: str) -> str:
    for suffix in _COMPANY_SUFFIXES:
        suffix_lc = suffix.lower()
        # Whole-word match at the right edge. We tolerate one trailing
        # period (`Inc.` → `inc.`) but already stripped that in
        # `_PUNCT_RE` so the suffix here is bare.
        if s == suffix_lc:
            return ""
        if s.endswith(" " + suffix_lc):
            return s[: -(len(suffix_lc) + 1)].rstrip()
    return s


# ─── Signal computation ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SponsorshipSignals:
    """Compact view of one company's sponsorship state, computed from
    an `EmployerSponsorship` row.

    `sponsors_h1b` is the conservative signal — high confidence;
    `past_h1b_activity` is the inclusive one — looser. Both can be
    True; only `past_h1b_activity` can be True alone; both False
    means the company has no LCA history and gets no badge."""

    sponsors_h1b: bool
    past_h1b_activity: bool
    lca_count_12mo: int
    lca_count_3yr: int
    most_recent_filing: date | None


_NO_SIGNALS = SponsorshipSignals(
    sponsors_h1b=False,
    past_h1b_activity=False,
    lca_count_12mo=0,
    lca_count_3yr=0,
    most_recent_filing=None,
)


def compute_signals(
    row: EmployerSponsorship | None,
    *,
    conservative_threshold: int = DEFAULT_CONSERVATIVE_THRESHOLD,
) -> SponsorshipSignals:
    """Map a single `EmployerSponsorship` row to its two signals. A
    missing row (the company has no DOL filings on file) returns the
    empty-signals tuple — no badge will render."""
    if row is None:
        return _NO_SIGNALS
    sponsors_h1b = row.lca_count_12mo >= conservative_threshold
    past_h1b_activity = row.lca_count_3yr >= 1
    return SponsorshipSignals(
        sponsors_h1b=sponsors_h1b,
        past_h1b_activity=past_h1b_activity,
        lca_count_12mo=row.lca_count_12mo,
        lca_count_3yr=row.lca_count_3yr,
        most_recent_filing=row.most_recent_filing,
    )


# ─── Bulk lookup (used by /api/jobs to avoid N+1) ──────────────────────────


# Process-wide cache of company names already logged as unmatched, so a
# single user page-load doesn't spam the logs with the same misses.
# Reset on process restart — these are debugging hints, not state.
_UNMATCHED_LOGGED: set[str] = set()


def lookup_signals_for_companies(
    db: Session,
    company_names: Iterable[str],
    *,
    conservative_threshold: int = DEFAULT_CONSERVATIVE_THRESHOLD,
) -> dict[str, SponsorshipSignals]:
    """Normalise each company name + look up its sponsorship row in a
    single query. Returns a dict keyed by the ORIGINAL company name so
    callers can attach the signals without re-normalising.

    Companies with no match get the empty-signals tuple AND a one-shot
    INFO log entry — the operator can grep `sponsorship unmatched` to
    surface naming gaps for investigation.
    """
    names = list(company_names)
    if not names:
        return {}

    # Build the original-name → normalised-name map first. We keep
    # both because we want a single signals dict keyed on the
    # original name (the caller has a `Job.company` to match), AND we
    # need the normalised names for the SQL `IN (...)` query.
    original_to_norm: dict[str, str] = {}
    for original in names:
        original_to_norm[original] = normalize_company_name(original)
    norm_keys = {n for n in original_to_norm.values() if n}

    rows_by_norm: dict[str, EmployerSponsorship] = {}
    if norm_keys:
        result = db.execute(
            select(EmployerSponsorship).where(EmployerSponsorship.normalized_name.in_(norm_keys))
        ).scalars()
        for row in result:
            rows_by_norm[row.normalized_name] = row

    signals: dict[str, SponsorshipSignals] = {}
    for original, norm in original_to_norm.items():
        row = rows_by_norm.get(norm)
        if row is None and norm and norm not in _UNMATCHED_LOGGED:
            _UNMATCHED_LOGGED.add(norm)
            log.info(
                "sponsorship unmatched: company=%r normalized=%r",
                original,
                norm,
            )
        signals[original] = compute_signals(row, conservative_threshold=conservative_threshold)
    return signals


# ─── Aggregate computation (used by the CLI ingest) ────────────────────────


@dataclass
class _EmployerAggregate:
    """Mutable accumulator the ingest builds up as it walks the DOL
    CSV. Converted to an `EmployerSponsorship` row at the end."""

    display_name: str
    lca_count_12mo: int = 0
    lca_count_3yr: int = 0
    titles_12mo: set[str] | None = None
    most_recent_filing: date | None = None

    def __post_init__(self) -> None:
        if self.titles_12mo is None:
            self.titles_12mo = set()


def aggregate_rows(
    rows: Iterable[tuple[str, date | None, str | None]],
    *,
    reference_date: date | None = None,
) -> dict[str, _EmployerAggregate]:
    """Aggregate `(employer, decision_date, job_title)` triples into
    per-normalised-employer counts. `reference_date` anchors the 12mo
    / 3yr windows — pass it explicitly in tests to keep results
    stable; in production the CLI passes `today` so windows roll
    forward with each run.

    Rows with a missing or unparseable employer name are dropped
    silently — they're noise the DOL exports occasionally contain.
    """
    ref = reference_date or datetime.now(UTC).date()
    cutoff_12mo = ref - timedelta(days=365)
    cutoff_3yr = ref - timedelta(days=365 * 3)

    aggregates: dict[str, _EmployerAggregate] = {}
    for employer, decision_date, title in rows:
        norm = normalize_company_name(employer or "")
        if not norm:
            continue
        bucket = aggregates.get(norm)
        if bucket is None:
            bucket = _EmployerAggregate(display_name=(employer or "").strip())
            aggregates[norm] = bucket
        if decision_date is None:
            # Without a date we can't slot the filing into either
            # window — count it nowhere but keep the row so the
            # display_name lookup still works downstream.
            continue
        if decision_date >= cutoff_3yr:
            bucket.lca_count_3yr += 1
        if decision_date >= cutoff_12mo:
            bucket.lca_count_12mo += 1
            if title:
                assert bucket.titles_12mo is not None
                bucket.titles_12mo.add(title.strip().lower())
        if bucket.most_recent_filing is None or decision_date > bucket.most_recent_filing:
            bucket.most_recent_filing = decision_date
    return aggregates


def upsert_aggregates(
    db: Session,
    aggregates: dict[str, _EmployerAggregate],
    *,
    source_file: str,
) -> dict[str, int]:
    """Write the aggregates into `employer_sponsorship`, in place.
    Returns counters for the CLI's stdout summary."""
    now = datetime.now(UTC)
    inserted = 0
    updated = 0
    for norm, agg in aggregates.items():
        existing = db.execute(
            select(EmployerSponsorship).where(EmployerSponsorship.normalized_name == norm)
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                EmployerSponsorship(
                    normalized_name=norm,
                    display_name=agg.display_name or norm,
                    lca_count_12mo=agg.lca_count_12mo,
                    lca_count_3yr=agg.lca_count_3yr,
                    most_recent_filing=agg.most_recent_filing,
                    distinct_titles_12mo=len(agg.titles_12mo or set()),
                    source_file=source_file,
                    last_loaded_at=now,
                )
            )
            inserted += 1
        else:
            existing.display_name = agg.display_name or existing.display_name
            existing.lca_count_12mo = agg.lca_count_12mo
            existing.lca_count_3yr = agg.lca_count_3yr
            existing.most_recent_filing = agg.most_recent_filing
            existing.distinct_titles_12mo = len(agg.titles_12mo or set())
            # `source_file` may carry a CSV-of-filenames over time —
            # the CLI passes the latest as a single token; collapsing
            # the history isn't worth the complexity.
            existing.source_file = source_file
            existing.last_loaded_at = now
            updated += 1
    db.commit()
    return {"inserted": inserted, "updated": updated, "total": inserted + updated}


def report_unmatched_companies(
    db: Session,
    job_companies: Iterable[str],
) -> list[dict[str, str]]:
    """Return the list of `(original, normalized)` company names from
    `job_companies` that don't have a row in `employer_sponsorship`.
    Used by the CLI's `--report-unmatched` flag to surface naming
    gaps for the operator to investigate."""
    by_norm: dict[str, str] = {}
    for name in job_companies:
        norm = normalize_company_name(name)
        if not norm:
            continue
        # Keep the first-seen display form per normalised key — good
        # enough for an investigation report.
        by_norm.setdefault(norm, name)
    if not by_norm:
        return []
    rows = db.execute(
        select(EmployerSponsorship.normalized_name).where(
            EmployerSponsorship.normalized_name.in_(by_norm.keys())
        )
    ).scalars()
    matched = set(rows)
    return [
        {"company": original, "normalized": norm}
        for norm, original in sorted(by_norm.items())
        if norm not in matched
    ]
