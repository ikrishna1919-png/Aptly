"""`python -m app.cli <subcommand>` entrypoint.

Subcommands:
    ingest               Run ingest + cleanup against the configured DB.
    validate-companies   Probe every seeded board token and print which resolve.
    clean-descriptions   One-off backfill: re-run strip_html() over every Job
                         description that still looks like HTML, in place.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.cli import clean_descriptions as clean_descriptions_cmd
from app.cli import ingest as ingest_cmd
from app.cli import sponsorship_ingest as sponsorship_ingest_cmd
from app.cli import validate as validate_cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ingest", help="Run ingest + cleanup against the configured DB.")
    sub.add_parser(
        "validate-companies",
        help="Probe every seeded board token and print which resolve.",
    )
    clean = sub.add_parser(
        "clean-descriptions",
        help="Backfill: clean HTML from existing Job.description rows in place.",
    )
    clean.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be cleaned without writing.",
    )

    sponsorship = sub.add_parser(
        "sponsorship-ingest",
        help="Load DOL LCA disclosure CSV into the employer_sponsorship table.",
    )
    sponsorship.add_argument("--file", required=True, help="Path to a DOL LCA disclosure CSV.")
    sponsorship.add_argument(
        "--source",
        required=True,
        help="Tag for the source file (e.g. FY2024_Q4). Stored on every row written.",
    )
    sponsorship.add_argument(
        "--report-unmatched",
        action="store_true",
        help="After loading, dump company names from the jobs table with no match.",
    )

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        stats = ingest_cmd.run()
        print(json.dumps(stats, indent=2))
        return 0
    if args.cmd == "validate-companies":
        report = validate_cmd.run()
        print(json.dumps(report, indent=2))
        return 0 if not report["unreachable"] else 1
    if args.cmd == "clean-descriptions":
        report = clean_descriptions_cmd.run(dry_run=args.dry_run)
        print(json.dumps(report, indent=2))
        return 0
    if args.cmd == "sponsorship-ingest":
        from pathlib import Path

        report = sponsorship_ingest_cmd.run(
            Path(args.file),
            args.source,
            report_unmatched=args.report_unmatched,
        )
        print(json.dumps(report, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
