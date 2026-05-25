"""`python -m app.cli <subcommand>` entrypoint.

Subcommands:
    ingest              Run ingest + cleanup against the configured DB.
    validate-companies  Probe every seeded board token and print which resolve.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.cli import ingest as ingest_cmd
from app.cli import validate as validate_cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ingest", help="Run ingest + cleanup against the configured DB.")
    sub.add_parser(
        "validate-companies",
        help="Probe every seeded board token and print which resolve.",
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
    return 1


if __name__ == "__main__":
    sys.exit(main())
