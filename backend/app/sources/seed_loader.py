"""Bulk seed helper — reads `infra/company_seed.tsv` and produces
candidate `(source_type, token, display_name, location)` rows for the
`sources` table.

The TSV is a list of company names + locations gathered by hand; for
each company we don't know which ATS (Greenhouse or Lever) hosts their
public board, so we insert BOTH variants. The next ingest probes each
token; non-resolving boards land at `last_status='error'` and the
per-source auto-disable threshold parks them. Smart-Recruiters is not
candidate-expanded because its tokens are case-sensitive identifiers
(e.g. "Versant3"), not slugs.
"""

from __future__ import annotations

import csv
import pathlib
import re
from dataclasses import dataclass

# Source types we have a working adapter for. Other ATSes
# (jobvite/workday/etc.) are out of scope until an adapter exists.
CANDIDATE_SOURCE_TYPES: tuple[str, ...] = ("greenhouse", "lever")

# Repo-root-relative location of the seed TSV. Resolved lazily so the
# migration + the test fixture can override it via an argument.
_DEFAULT_SEED_PATH = pathlib.Path(__file__).resolve().parents[3] / "infra" / "company_seed.tsv"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase + strip every character that isn't `[a-z0-9]`.

    >>> slugify("23andMe")
    '23andme'
    >>> slugify("Bill.com")
    'billcom'
    >>> slugify("The Climate Corporation")
    'theclimatecorporation'
    >>> slugify("Mark 43")
    'mark43'
    """
    return _SLUG_STRIP.sub("", name.lower())


@dataclass(frozen=True)
class SeedCompany:
    name: str
    location: str | None


def read_seed_file(path: pathlib.Path | None = None) -> list[SeedCompany]:
    """Parse the TSV. Skips the header row + blank/comment lines."""
    p = path if path is not None else _DEFAULT_SEED_PATH
    rows: list[SeedCompany] = []
    with p.open(encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for raw in reader:
            if not raw or not raw[0].strip():
                continue
            name = raw[0].strip()
            # Header row uses literal "Company Name" — skip it.
            if name.lower() == "company name":
                continue
            location = raw[1].strip() if len(raw) > 1 and raw[1].strip() else None
            rows.append(SeedCompany(name=name, location=location))
    return rows


def candidate_rows(path: pathlib.Path | None = None) -> list[dict]:
    """Expand seed → DB rows for `sources`. Two per company (one
    `greenhouse`, one `lever`). Deduplicates inside the batch on
    `(source_type, token)` so the multi-row insert doesn't trip the
    Postgres "cannot affect row a second time" rule when two names
    slugify to the same token."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for company in read_seed_file(path):
        token = slugify(company.name)
        if not token:
            continue
        for source_type in CANDIDATE_SOURCE_TYPES:
            key = (source_type, token)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "source_type": source_type,
                    "token": token,
                    "display_name": company.name,
                    "location": company.location,
                }
            )
    return out
