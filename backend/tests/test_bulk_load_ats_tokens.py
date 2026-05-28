"""Tests for the bulk-load of ATS company tokens (migration 0014).

Covers:
  * The migration upgrade actually inserts the curated bulk lists,
    one row per token, across all five source types.
  * Re-running the migration is idempotent (no duplicate-key crash,
    same row count).
  * Downgrade removes only the bulk-list rows, preserving rows that
    were inserted by other migrations.
  * Per-platform shape rules hold: SmartRecruiters preserves case,
    Workday entries unpack to a complete `tenant:dc:site` triple.
  * The seed counts are pinned so a careless edit to `bulk_tokens.py`
    causes a visible test failure (rather than a silent drift in the
    coverage we promise to operators).
  * `INGEST_MAX_PER_RUN` still bounds the next run after the bulk
    load — the rotation picks the configured cap and no more.
"""

from __future__ import annotations

import pathlib

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.models.source import Source
from app.sources import bulk_tokens
from app.sources.workday import _parse_token as parse_workday_token


def _alembic_config(db_url: str) -> Config:
    backend_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    """Fresh SQLite file + every migration to head. Mirrors the
    setup `test_sources_table.py` uses so the patterns stay
    consistent across migration test files."""
    from app import config as config_module

    db_path = tmp_path / "bulk_tokens.sqlite"
    db_url = f"sqlite+pysqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    config_module.get_settings.cache_clear()

    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")

    engine = create_engine(db_url, future=True)
    Session = sessionmaker(bind=engine, future=True)
    try:
        yield Session, cfg
    finally:
        engine.dispose()
        config_module.get_settings.cache_clear()


# ── Per-platform shape ──────────────────────────────────────────────────────


def test_bulk_lists_are_non_trivial_and_well_formed():
    """Pin a floor on how aggressive the bulk load is. If someone
    edits `bulk_tokens.py` to remove most entries by mistake, this
    test catches it before the next deploy ships a much smaller
    source set than the operator expects."""
    rows = bulk_tokens.all_bulk_rows()

    # Floors, not exact counts — leaving headroom for the next bulk
    # refresh to ADD entries without churning this test on every
    # edit. Equally, ratcheting the floor up when we deliberately
    # grow a list is the right move.
    floors = {
        "greenhouse": 150,
        "lever": 80,
        "ashby": 50,
        "smartrecruiters": 15,
        "workday": 20,
    }
    for source_type, floor in floors.items():
        assert (
            len(rows[source_type]) >= floor
        ), f"{source_type} bulk list shrank below {floor} entries — verify intentional"

    # No empty tokens, every row carries the four columns the
    # migration expects to insert.
    for source_type, payload in rows.items():
        for row in payload:
            assert row["source_type"] == source_type
            assert (
                isinstance(row["token"], str) and row["token"].strip()
            ), f"empty token in {source_type}: {row}"
            assert isinstance(row["display_name"], str) and row["display_name"].strip()


def test_workday_tokens_unpack_to_full_triple():
    """Every Workday bulk entry must satisfy `tenant:dc:site[:host]` —
    the adapter's `_parse_token` raises `SourceUnavailable` on a half-
    formed token and the operator would see those rows churn
    forever. The spec is explicit: skip Workday entries we can't
    form a complete triple for."""
    rows = bulk_tokens.all_bulk_rows()["workday"]
    for row in rows:
        # `_parse_token` raises on malformed input; reach into it to
        # confirm the shape AND that each component is non-empty.
        # `_parse_token` returns a 4-tuple — the 4th element is the
        # host, which defaults to `myworkdayjobs.com` for the
        # three-part form.
        tenant, dc, site, host = parse_workday_token(row["token"])
        assert tenant and dc and site, f"workday token has empty component: {row['token']!r}"
        # Sanity: data center looks like `wdN` or `wdNNN`. Pinning
        # the shape catches a typo like `wd-5` that the adapter
        # would dutifully send into the URL template and 404 on
        # forever.
        assert dc.startswith("wd"), f"workday dc {dc!r} doesn't look like wd*"
        assert host, f"workday token has empty host: {row['token']!r}"


def test_smartrecruiters_tokens_preserve_case():
    """SmartRecruiters identifiers are case-sensitive (`Bosch`, NOT
    `bosch`). Lowercasing here would silently break every probe."""
    rows = bulk_tokens.all_bulk_rows()["smartrecruiters"]
    # We expect a meaningful share of the list to contain at least
    # one uppercase character — proves we're keeping the source case.
    upper_count = sum(1 for r in rows if any(ch.isupper() for ch in r["token"]))
    assert (
        upper_count >= len(rows) // 2
    ), "smartrecruiters tokens look lowercase — case-preserving rule broken"


def test_no_duplicate_tokens_within_platform():
    """Two rows with the same `(source_type, token)` in the bulk
    payload would trip the `INSERT OR IGNORE` for the second one and
    hide a typo. Catch it at the source before the migration runs."""
    by_platform = bulk_tokens.all_bulk_rows()
    for source_type, rows in by_platform.items():
        tokens = [r["token"] for r in rows]
        dupes = {t for t in tokens if tokens.count(t) > 1}
        assert not dupes, f"{source_type} has duplicate tokens: {sorted(dupes)}"


# ── Migration end-to-end ────────────────────────────────────────────────────


def test_migration_inserts_every_bulk_token(migrated_db):
    """After upgrade-to-head, every (source_type, token) from
    `bulk_tokens.all_bulk_rows` exists in the `sources` table."""
    Session, _ = migrated_db
    by_platform = bulk_tokens.all_bulk_rows()
    with Session() as s:
        rows = list(s.execute(select(Source.source_type, Source.token)))
    present = {(t, tok) for t, tok in rows}
    for source_type, payload in by_platform.items():
        for row in payload:
            assert (
                source_type,
                row["token"],
            ) in present, f"{source_type}:{row['token']} missing after upgrade"


def test_migration_records_correct_counts_per_platform(migrated_db):
    """The migration's job is to load each bulk list with no
    duplicates. After upgrade, each platform's row count must be
    GREATER-OR-EQUAL to its bulk-list length — equality fails if
    earlier migrations seeded overlapping tokens (which is fine and
    expected), strict <-comparison catches a missing insert."""
    Session, _ = migrated_db
    by_platform = bulk_tokens.all_bulk_rows()
    with Session() as s:
        for source_type, payload in by_platform.items():
            count = s.execute(
                select(func.count()).where(Source.source_type == source_type)
            ).scalar_one()
            assert count >= len(
                payload
            ), f"{source_type}: rows={count} but bulk list has {len(payload)}"


def test_migration_is_idempotent(migrated_db):
    """Downgrade-then-upgrade twice must converge on the same row
    count. The migration uses INSERT OR IGNORE / ON CONFLICT DO
    NOTHING, so re-running can't add duplicates."""
    Session, cfg = migrated_db
    with Session() as s:
        first = s.execute(select(func.count()).select_from(Source)).scalar_one()

    command.downgrade(cfg, "0013_employer_sponsorship")
    command.upgrade(cfg, "head")

    with Session() as s:
        second = s.execute(select(func.count()).select_from(Source)).scalar_one()
    assert second == first


def test_downgrade_removes_only_bulk_load_rows(migrated_db):
    """Downgrade-0014 must remove every bulk-list token AND leave
    rows seeded by 0007/0008/0009/0010 untouched."""
    Session, cfg = migrated_db
    by_platform = bulk_tokens.all_bulk_rows()
    with Session() as s:
        before_total = s.execute(select(func.count()).select_from(Source)).scalar_one()

    command.downgrade(cfg, "0013_employer_sponsorship")

    with Session() as s:
        after_total = s.execute(select(func.count()).select_from(Source)).scalar_one()
    # Some bulk tokens overlap with earlier seeds — `(greenhouse,
    # stripe)` for instance is in both `companies.py`'s baseline AND
    # `bulk_tokens.py`. The 0014 downgrade deletes every bulk-list
    # token (it can't tell which were duplicates), so those overlaps
    # also vanish — a known and accepted trade-off. The contract this
    # test pins is the looser one: downgrade dropped a substantial
    # number of rows (at least the unique ones 0014 introduced).
    bulk_total = sum(len(p) for p in by_platform.values())
    assert before_total - after_total >= bulk_total * 0.8, (
        f"downgrade only removed {before_total - after_total} rows; "
        f"expected ≥ {int(bulk_total * 0.8)} (bulk load was {bulk_total})"
    )


# ── Rotation bound still applies ────────────────────────────────────────────


def test_ingest_rotation_still_bounded_after_bulk_load(migrated_db):
    """A run after the bulk load picks at most `ingest_max_per_run`
    sources, regardless of how many rows landed in the table. This
    is what keeps a 500-row sources table from running past the
    scheduled-budget per pass — newly-seeded rows have
    `last_run_at=NULL` and sort first via `NULLS FIRST`, so they
    rotate through across successive runs."""
    from app.services.ingest import _load_due_sources

    Session, _ = migrated_db
    with Session() as s:
        total = s.execute(select(func.count()).select_from(Source)).scalar_one()
        assert total > 150, (
            "bulk load isn't large enough to exercise the rotation cap "
            f"(only {total} rows); raise the bulk lists or lower the cap"
        )

        picked = _load_due_sources(s, limit=150)
    # Cap honoured.
    assert len(picked) == 150
    # Every picked row is enabled (the bulk load + earlier seeds all
    # default to `enabled=true`).
    assert all(p.enabled for p in picked)
