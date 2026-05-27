"""Tests for the bulk-seed helper used by Alembic migration 0008.

Pins:
  * The slugify rule matches what the user spec'd, with the four
    canonical examples and the obvious edge cases.
  * Reading the TSV produces non-empty rows with location preserved.
  * `candidate_rows` produces exactly TWO rows per company (greenhouse +
    lever), deduplicated on (source_type, token).
"""

from __future__ import annotations

import pathlib

from app.sources.seed_loader import (
    CANDIDATE_SOURCE_TYPES,
    SeedCompany,
    candidate_rows,
    read_seed_file,
    slugify,
)

# Path to the committed seed TSV so the tests verify the real file.
_SEED_PATH = pathlib.Path(__file__).resolve().parents[2] / "infra" / "company_seed.tsv"


def test_slugify_examples_from_spec():
    """Exactly the four examples from the task spec — pin them so a
    careless regex tweak can't break the contract."""
    assert slugify("23andMe") == "23andme"
    assert slugify("Bill.com") == "billcom"
    assert slugify("The Climate Corporation") == "theclimatecorporation"
    assert slugify("Mark 43") == "mark43"


def test_slugify_strips_assorted_punctuation():
    assert slugify("frame.ai") == "frameai"
    assert slugify("Taco Bell, Corporate") == "tacobellcorporate"
    assert slugify("Hart, Inc.") == "hartinc"
    assert slugify("Redis-Labs") == "redislabs"
    assert slugify("Dot & Bo") == "dotbo"
    assert slugify("Booking.com") == "bookingcom"


def test_slugify_lowercases():
    assert slugify("ROBLOX") == "roblox"
    assert slugify("MongoDB") == "mongodb"


def test_slugify_drops_non_ascii():
    """Accented characters aren't in `[a-z0-9]` — they get stripped just
    like punctuation. No company in the seed has these, but we want the
    behaviour pinned in case a future name does."""
    assert slugify("São Paulo") == "sopaulo"


def test_read_seed_file_skips_header_and_parses_locations():
    rows = read_seed_file(_SEED_PATH)
    assert len(rows) > 100, "seed should have hundreds of rows"
    assert all(isinstance(r, SeedCompany) for r in rows)
    by_name = {r.name: r for r in rows}
    # Spot-check a few entries from the original TSV.
    assert by_name["23andMe"].location == "Mountain View, CA"
    assert "Booking.com" in by_name
    # Multi-city locations preserved as-is.
    assert "Toronto" in (by_name["Mark 43"].location or "")


def test_candidate_rows_produces_two_per_company_with_metadata():
    seed = read_seed_file(_SEED_PATH)
    rows = candidate_rows(_SEED_PATH)

    # Two rows (greenhouse + lever) per unique slug.
    distinct_slugs = {slugify(c.name) for c in seed if slugify(c.name)}
    assert len(rows) == 2 * len(distinct_slugs)

    # Every row carries display_name + location forwarded from the seed,
    # and only `greenhouse` / `lever` source types.
    types = {r["source_type"] for r in rows}
    assert types == set(CANDIDATE_SOURCE_TYPES)

    for r in rows:
        assert r["token"] == slugify(r["display_name"])
        assert "location" in r  # may be None for some rows


def test_candidate_rows_dedupes_in_batch():
    """If two names slugify identically, the helper drops the second so
    the multi-row insert doesn't try to write the same `(source_type,
    token)` twice — Postgres' `ON CONFLICT DO NOTHING` rejects that."""
    rows = candidate_rows(_SEED_PATH)
    pairs = [(r["source_type"], r["token"]) for r in rows]
    assert len(pairs) == len(set(pairs))
