"""`python -m app.cli sponsorship-ingest --file <path.csv>` —
load DOL LCA disclosure data into `employer_sponsorship`.

The DOL publishes quarterly LCA disclosure files on the
foreign-labor performance page. Download the CSV (or the Excel,
exported to CSV), then point this CLI at it:

    python -m app.cli sponsorship-ingest \\
        --file ./LCA_Disclosure_Data_FY2024_Q4.csv \\
        --source FY2024_Q4

For a backfill, run the CLI once per file across the most recent two
fiscal years (eight quarterly files). The CLI is idempotent — running
the same file twice just rewrites the same aggregate rows.

CSV column mapping is permissive: the DOL has renamed columns
across years (`EMPLOYER_NAME` → `LCA_CASE_EMPLOYER_NAME` and back).
We try a list of common spellings and error out clearly if none
match, rather than silently producing empty aggregates.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models.job import Job
from app.services.sponsorship import (
    aggregate_rows,
    report_unmatched_companies,
    upsert_aggregates,
)

log = logging.getLogger(__name__)

# Column-name aliases the DOL has used across fiscal years. The
# loader tries each in order; the first that matches a header in the
# CSV wins. Adding a new alias is the right move when a future DOL
# release renames a column — the rest of the loader stays untouched.
_EMPLOYER_HEADERS = (
    "EMPLOYER_NAME",
    "LCA_CASE_EMPLOYER_NAME",
    "EMPLOYER_BUSINESS_NAME",
    "Employer (Petitioner) Name",
)
_DECISION_DATE_HEADERS = (
    "DECISION_DATE",
    "LCA_CASE_SUBMIT",
    "Case Decision Date",
    "DATE_OF_DECISION",
)
_TITLE_HEADERS = (
    "JOB_TITLE",
    "LCA_CASE_JOB_TITLE",
    "Job Title",
    "SOC_TITLE",
)
_STATUS_HEADERS = (
    "CASE_STATUS",
    "STATUS",
)

# Only "Certified" / "Certified-Withdrawn" filings count as evidence
# of sponsorship activity. Denied / withdrawn filings are noise —
# they don't reflect an active sponsorship pipeline.
_VALID_STATUSES = {
    "certified",
    "certified-withdrawn",
    "certified - withdrawn",
}


def _pick_column(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    """Case-insensitive lookup of the first matching column name."""
    lower = {h.lower(): h for h in headers}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return None


def _parse_date(value: str | None) -> date | None:
    """DOL dates come in a couple of shapes across years. Try the
    common ones; return None on unparseable input rather than
    crashing the whole import."""
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    # `MM/DD/YYYY HH:MM:SS` and `YYYY-MM-DD HH:MM:SS` show up too.
    for sep in (" ", "T"):
        if sep in v:
            head = v.split(sep, 1)[0]
            for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                try:
                    return datetime.strptime(head, fmt).date()
                except ValueError:
                    continue
    return None


def iter_disclosure_rows(
    csv_path: Path,
) -> Iterable[tuple[str, date | None, str | None]]:
    """Stream `(employer, decision_date, job_title)` triples out of a
    DOL disclosure CSV. Rows with an unrecognised `CASE_STATUS` are
    dropped — only certified filings count as sponsorship evidence.

    Raises `RuntimeError` when the file's headers don't include any
    recognisable employer / decision-date column. That's a load-time
    failure with a clear actionable message rather than a silent
    zero-rows import.
    """
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        employer_col = _pick_column(headers, _EMPLOYER_HEADERS)
        date_col = _pick_column(headers, _DECISION_DATE_HEADERS)
        title_col = _pick_column(headers, _TITLE_HEADERS)
        status_col = _pick_column(headers, _STATUS_HEADERS)
        if employer_col is None:
            raise RuntimeError(
                f"DOL CSV {csv_path} has no recognised employer column. "
                f"Tried: {_EMPLOYER_HEADERS}; got: {headers}"
            )
        if date_col is None:
            raise RuntimeError(
                f"DOL CSV {csv_path} has no recognised decision-date column. "
                f"Tried: {_DECISION_DATE_HEADERS}; got: {headers}"
            )

        for row in reader:
            if status_col is not None:
                status = (row.get(status_col) or "").strip().lower()
                if status and status not in _VALID_STATUSES:
                    continue
            employer = (row.get(employer_col) or "").strip()
            if not employer:
                continue
            decision_date = _parse_date(row.get(date_col))
            title = (row.get(title_col) or "").strip() if title_col else None
            yield employer, decision_date, title


def run(
    csv_path: Path,
    source: str,
    *,
    reference_date: date | None = None,
    report_unmatched: bool = False,
) -> dict:
    """Backfill / refresh entrypoint. Reads the CSV, computes
    aggregates, upserts, returns a stats dict for the CLI's stdout
    summary."""
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    log.info("loading DOL disclosure file %s (source=%s)", csv_path, source)
    rows = iter_disclosure_rows(csv_path)
    aggregates = aggregate_rows(rows, reference_date=reference_date)
    log.info("aggregated %d distinct employers", len(aggregates))

    with SessionLocal() as db:
        stats = upsert_aggregates(db, aggregates, source_file=source)

        unmatched: list[dict[str, str]] = []
        if report_unmatched:
            companies = [c for (c,) in db.execute(select(Job.company).distinct()).all() if c]
            unmatched = report_unmatched_companies(db, companies)
            log.info(
                "unmatched companies in jobs table: %d / %d",
                len(unmatched),
                len(companies),
            )

    return {
        "source_file": source,
        "employers_aggregated": len(aggregates),
        **stats,
        "unmatched": unmatched,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        prog="python -m app.cli sponsorship-ingest",
        description="Load DOL LCA disclosure CSV into employer_sponsorship.",
    )
    parser.add_argument(
        "--file", required=True, type=Path, help="Path to a DOL LCA disclosure CSV."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Tag identifying the source file (e.g. FY2024_Q4) — stored on every "
        "row written so a stale-data complaint can be traced back.",
    )
    parser.add_argument(
        "--report-unmatched",
        action="store_true",
        help="After loading, dump the list of company names in the jobs "
        "table that don't match any sponsorship row.",
    )
    args = parser.parse_args(argv)

    try:
        report = run(args.file, args.source, report_unmatched=args.report_unmatched)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    import json

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
