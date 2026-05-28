"""Sponsorship-intelligence: normalisation, signal computation,
CLI ingest, the API-side bulk-lookup helper, and the migration.

The two ends of the pipeline have to agree on the same join key (the
normalised employer name) — that's the centre of gravity for this
test file.
"""

from __future__ import annotations

import csv
import pathlib
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from alembic import command
from app.cli import sponsorship_ingest as cli
from app.database import Base
from app.models.employer_sponsorship import (
    DEFAULT_CONSERVATIVE_THRESHOLD,
    EmployerSponsorship,
)
from app.services.sponsorship import (
    aggregate_rows,
    compute_signals,
    lookup_signals_for_companies,
    normalize_company_name,
    report_unmatched_companies,
    upsert_aggregates,
)


@pytest.fixture
def factories():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session


# ─── Normalisation ─────────────────────────────────────────────────────────


class TestNormalize:
    def test_strips_corporate_suffixes(self):
        assert normalize_company_name("Stripe") == "stripe"
        assert normalize_company_name("Stripe Inc") == "stripe"
        assert normalize_company_name("Stripe, Inc.") == "stripe"
        assert normalize_company_name("STRIPE, INC.") == "stripe"
        assert normalize_company_name("Stripe LLC") == "stripe"
        assert normalize_company_name("Stripe Corporation") == "stripe"

    def test_collapses_separators(self):
        assert normalize_company_name("Bank of America") == "bank of america"
        assert normalize_company_name("Bank-of-America") == "bank of america"
        assert normalize_company_name("Bank   of   America") == "bank of america"
        assert normalize_company_name("Bank_of_America") == "bank of america"

    def test_strips_stacked_suffixes(self):
        """`acme co ltd` is two suffixes back-to-back — both should
        come off so the join key is just `acme`."""
        assert normalize_company_name("Acme Co Ltd") == "acme"
        # Pte Ltd is the Singapore equivalent — also a stacked
        # suffix that should fully strip.
        assert normalize_company_name("Acme Pte Ltd") == "acme"

    def test_ampersand_becomes_and(self):
        """`AT&T` and `AT and T` should normalise to the same key."""
        assert normalize_company_name("AT&T") == normalize_company_name("AT and T")
        assert normalize_company_name("Procter & Gamble") == normalize_company_name(
            "Procter and Gamble"
        )

    def test_empty_and_garbage_inputs(self):
        assert normalize_company_name("") == ""
        assert normalize_company_name("   ") == ""
        # Just a suffix on its own normalises to empty.
        assert normalize_company_name("Inc") == ""
        assert normalize_company_name("LLC") == ""

    def test_equivalence_classes(self):
        """The whole reason for this normaliser: every spelling of the
        same employer must collapse to a single key. If this test
        breaks, the join key has drifted and the badges will start
        missing matches."""
        cohort = [
            "Stripe",
            "Stripe Inc",
            "Stripe, Inc.",
            "STRIPE INC",
            "stripe llc",
            "Stripe Corp",
            "Stripe Corporation",
        ]
        keys = {normalize_company_name(c) for c in cohort}
        assert keys == {"stripe"}, f"normalisation drift: {keys}"


# ─── Signal computation ────────────────────────────────────────────────────


def _row(*, lca_12: int, lca_3y: int, recent: date | None = None) -> EmployerSponsorship:
    return EmployerSponsorship(
        normalized_name="acme",
        display_name="Acme",
        lca_count_12mo=lca_12,
        lca_count_3yr=lca_3y,
        most_recent_filing=recent,
        distinct_titles_12mo=0,
        source_file="test",
    )


class TestComputeSignals:
    def test_no_row_returns_empty_signals(self):
        s = compute_signals(None)
        assert s.sponsors_h1b is False
        assert s.past_h1b_activity is False
        assert s.lca_count_12mo == 0
        assert s.lca_count_3yr == 0
        assert s.most_recent_filing is None

    def test_conservative_signal_above_threshold(self):
        s = compute_signals(_row(lca_12=10, lca_3y=30, recent=date(2024, 6, 1)))
        assert s.sponsors_h1b is True
        assert s.past_h1b_activity is True

    def test_inclusive_signal_only_when_past_activity(self):
        """One LCA filed two years ago: too sparse for the
        conservative signal, enough for the inclusive one. The two
        signals must come back distinct so the UI can render only the
        weaker badge."""
        s = compute_signals(_row(lca_12=0, lca_3y=1))
        assert s.sponsors_h1b is False
        assert s.past_h1b_activity is True

    def test_conservative_threshold_is_configurable(self):
        """Default threshold is 5; raising it to 20 should turn the
        conservative signal back off for the same row."""
        row = _row(lca_12=10, lca_3y=20)
        default = compute_signals(row)
        assert default.sponsors_h1b is True
        strict = compute_signals(row, conservative_threshold=20)
        assert strict.sponsors_h1b is False
        # Inclusive signal is unaffected.
        assert strict.past_h1b_activity is True

    def test_never_returns_negative_label_for_unknown_company(self):
        """The whole point: a missing row reads as 'no badge', not as
        'does not sponsor'. The signals helper enforces that by
        returning falsy values; the UI side honours it by not
        rendering a negative badge."""
        s = compute_signals(None)
        assert s.sponsors_h1b is False
        assert s.past_h1b_activity is False


# ─── Aggregation (the heart of the CLI ingest) ──────────────────────────────


class TestAggregateRows:
    def test_groups_by_normalised_employer(self):
        """Different spellings of the same employer all roll up into
        a single aggregate."""
        rows = [
            ("Stripe Inc", date(2024, 8, 1), "Engineer"),
            ("Stripe, Inc.", date(2024, 9, 1), "Engineer"),
            ("STRIPE", date(2024, 10, 1), "Manager"),
        ]
        agg = aggregate_rows(rows, reference_date=date(2024, 12, 31))
        assert set(agg.keys()) == {"stripe"}
        stripe = agg["stripe"]
        assert stripe.lca_count_12mo == 3
        assert stripe.lca_count_3yr == 3
        # Two distinct titles, deduped.
        assert stripe.titles_12mo == {"engineer", "manager"}
        assert stripe.most_recent_filing == date(2024, 10, 1)

    def test_12mo_and_3yr_windows_are_distinct(self):
        ref = date(2024, 12, 31)
        rows = [
            # In 12mo + 3yr
            ("Acme", date(2024, 6, 1), "Eng"),
            # In 3yr but NOT in 12mo (>365 days ago)
            ("Acme", date(2022, 6, 1), "Eng"),
            # Outside both windows (>3yr ago)
            ("Acme", date(2018, 1, 1), "Eng"),
        ]
        agg = aggregate_rows(rows, reference_date=ref)["acme"]
        assert agg.lca_count_12mo == 1
        assert agg.lca_count_3yr == 2

    def test_missing_decision_date_doesnt_count(self):
        """Filings with no parseable date are kept for display but
        contribute to neither window. They'd otherwise inflate counts
        unpredictably."""
        rows = [
            ("Acme", None, "Eng"),
            ("Acme", date(2024, 6, 1), "Eng"),
        ]
        agg = aggregate_rows(rows, reference_date=date(2024, 12, 31))["acme"]
        assert agg.lca_count_12mo == 1
        assert agg.lca_count_3yr == 1

    def test_empty_employer_is_dropped_silently(self):
        rows = [
            ("", date(2024, 1, 1), "Eng"),
            ("   ", date(2024, 1, 1), "Eng"),
            ("Acme", date(2024, 1, 1), "Eng"),
        ]
        agg = aggregate_rows(rows, reference_date=date(2024, 12, 31))
        assert list(agg.keys()) == ["acme"]


# ─── Upsert + DB round-trip ─────────────────────────────────────────────────


class TestUpsert:
    def test_inserts_new_rows(self, factories):
        rows = [("Acme Inc", date(2024, 6, 1), "Eng"), ("Stripe", date(2024, 8, 1), "Eng")]
        agg = aggregate_rows(rows, reference_date=date(2024, 12, 31))
        with factories() as db:
            stats = upsert_aggregates(db, agg, source_file="FY2024_Q4")
            assert stats == {"inserted": 2, "updated": 0, "total": 2}
            stored = db.query(EmployerSponsorship).all()
            assert {r.normalized_name for r in stored} == {"acme", "stripe"}

    def test_updates_existing_rows_in_place(self, factories):
        first_pass = aggregate_rows(
            [("Acme", date(2024, 6, 1), "Eng")], reference_date=date(2024, 12, 31)
        )
        with factories() as db:
            upsert_aggregates(db, first_pass, source_file="FY2024_Q3")
        # Now a second pass with newer data — same employer, different counts.
        second_pass = aggregate_rows(
            [
                ("Acme", date(2024, 6, 1), "Eng"),
                ("Acme", date(2024, 11, 1), "Manager"),
            ],
            reference_date=date(2024, 12, 31),
        )
        with factories() as db:
            stats = upsert_aggregates(db, second_pass, source_file="FY2024_Q4")
            assert stats == {"inserted": 0, "updated": 1, "total": 1}
            row = db.query(EmployerSponsorship).one()
            assert row.lca_count_12mo == 2
            assert row.distinct_titles_12mo == 2
            assert row.source_file == "FY2024_Q4"


# ─── Bulk lookup (the API-side path) ────────────────────────────────────────


class TestLookupForCompanies:
    def test_matches_via_normalised_name(self, factories):
        with factories() as db:
            db.add(
                EmployerSponsorship(
                    normalized_name="stripe",
                    display_name="Stripe",
                    lca_count_12mo=42,
                    lca_count_3yr=120,
                    most_recent_filing=date(2024, 10, 1),
                    distinct_titles_12mo=15,
                    source_file="FY2024_Q4",
                )
            )
            db.commit()
            # Same company across three spellings — all should match.
            res = lookup_signals_for_companies(db, ["Stripe Inc", "stripe", "STRIPE, INC."])
        for name in ["Stripe Inc", "stripe", "STRIPE, INC."]:
            sig = res[name]
            assert sig.sponsors_h1b is True
            assert sig.past_h1b_activity is True
            assert sig.lca_count_12mo == 42

    def test_missing_company_returns_empty_signals(self, factories):
        with factories() as db:
            res = lookup_signals_for_companies(db, ["Some Unknown LLC"])
        sig = res["Some Unknown LLC"]
        assert sig.sponsors_h1b is False
        assert sig.past_h1b_activity is False
        assert sig.lca_count_12mo == 0

    def test_below_threshold_company_only_lights_inclusive(self, factories):
        # `Tiny Solutions` normalises to `tiny solutions` — no
        # corporate-suffix collision so the equivalence-class test is
        # clean.
        with factories() as db:
            db.add(
                EmployerSponsorship(
                    normalized_name="tiny solutions",
                    display_name="Tiny Solutions",
                    lca_count_12mo=1,  # below default threshold of 5
                    lca_count_3yr=2,
                    most_recent_filing=date(2024, 1, 1),
                    distinct_titles_12mo=1,
                    source_file="FY2024_Q4",
                )
            )
            db.commit()
            sig = lookup_signals_for_companies(db, ["Tiny Solutions"])["Tiny Solutions"]
        assert sig.sponsors_h1b is False  # conservative — below threshold
        assert sig.past_h1b_activity is True  # inclusive — fires

    def test_empty_input_returns_empty_dict(self, factories):
        with factories() as db:
            assert lookup_signals_for_companies(db, []) == {}


# ─── CLI ingest ─────────────────────────────────────────────────────────────


def _write_csv(tmp_path: Path, headers: list[str], rows: list[list[str]]) -> Path:
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    p = tmp_path / "lca.csv"
    p.write_text(buf.getvalue(), encoding="utf-8")
    return p


class TestCliIngest:
    def test_iter_disclosure_rows_parses_modern_headers(self, tmp_path):
        path = _write_csv(
            tmp_path,
            ["CASE_STATUS", "DECISION_DATE", "EMPLOYER_NAME", "JOB_TITLE"],
            [
                ["Certified", "2024-06-01", "Stripe Inc", "Software Engineer"],
                ["Certified-Withdrawn", "2024-07-15", "Acme Co", "Manager"],
                ["Denied", "2024-08-01", "Acme Co", "Manager"],  # filtered out
            ],
        )
        rows = list(cli.iter_disclosure_rows(path))
        # Two rows kept (Certified + Certified-Withdrawn); Denied dropped.
        assert len(rows) == 2
        assert rows[0][0] == "Stripe Inc"
        assert rows[0][1] == date(2024, 6, 1)
        assert rows[0][2] == "Software Engineer"

    def test_iter_disclosure_rows_handles_alternative_headers(self, tmp_path):
        """An older DOL release uses `LCA_CASE_EMPLOYER_NAME` /
        `LCA_CASE_SUBMIT` / `LCA_CASE_JOB_TITLE`. The loader must
        tolerate either."""
        path = _write_csv(
            tmp_path,
            ["STATUS", "LCA_CASE_SUBMIT", "LCA_CASE_EMPLOYER_NAME", "LCA_CASE_JOB_TITLE"],
            [["CERTIFIED", "06/01/2024", "Old Inc", "Engineer"]],
        )
        rows = list(cli.iter_disclosure_rows(path))
        assert rows == [("Old Inc", date(2024, 6, 1), "Engineer")]

    def test_iter_disclosure_rows_errors_when_employer_column_missing(self, tmp_path):
        path = _write_csv(
            tmp_path,
            ["DECISION_DATE", "JOB_TITLE"],
            [["2024-06-01", "Engineer"]],
        )
        with pytest.raises(RuntimeError, match="employer column"):
            list(cli.iter_disclosure_rows(path))

    def test_parse_date_tolerates_multiple_formats(self):
        assert cli._parse_date("2024-06-01") == date(2024, 6, 1)
        assert cli._parse_date("06/01/2024") == date(2024, 6, 1)
        # The CSV occasionally carries `YYYY-MM-DD HH:MM:SS`.
        assert cli._parse_date("2024-06-01 12:34:56") == date(2024, 6, 1)
        assert cli._parse_date("") is None
        assert cli._parse_date(None) is None
        assert cli._parse_date("not a date") is None

    def test_end_to_end_run_writes_aggregates(self, tmp_path, factories, monkeypatch):
        path = _write_csv(
            tmp_path,
            ["CASE_STATUS", "DECISION_DATE", "EMPLOYER_NAME", "JOB_TITLE"],
            [
                ["Certified", "2024-06-01", "Stripe Inc", "Software Engineer"],
                ["Certified", "2024-07-01", "Stripe Inc", "Backend Engineer"],
                ["Certified", "2024-08-01", "Stripe, Inc.", "Engineer"],  # normalises same
                ["Certified", "2024-09-01", "Acme Co", "Manager"],
                ["Denied", "2024-09-01", "Acme Co", "Manager"],  # filtered
            ],
        )
        # Route the CLI's `SessionLocal` at the test DB.
        monkeypatch.setattr(cli, "SessionLocal", factories)
        report = cli.run(path, source="FY2024_Q4", reference_date=date(2024, 12, 31))
        assert report["employers_aggregated"] == 2
        assert report["inserted"] == 2
        with factories() as db:
            rows = {r.normalized_name: r for r in db.query(EmployerSponsorship).all()}
        assert rows["stripe"].lca_count_12mo == 3
        # Distinct titles count is bounded by what we keep — the
        # three Stripe rows had three distinct titles.
        assert rows["stripe"].distinct_titles_12mo == 3
        assert rows["acme"].lca_count_12mo == 1

    def test_run_is_idempotent(self, tmp_path, factories, monkeypatch):
        path = _write_csv(
            tmp_path,
            ["CASE_STATUS", "DECISION_DATE", "EMPLOYER_NAME", "JOB_TITLE"],
            [["Certified", "2024-06-01", "Stripe Inc", "Eng"]],
        )
        monkeypatch.setattr(cli, "SessionLocal", factories)
        first = cli.run(path, source="FY2024_Q4", reference_date=date(2024, 12, 31))
        assert first["inserted"] == 1
        second = cli.run(path, source="FY2024_Q4", reference_date=date(2024, 12, 31))
        # Same row, in-place update.
        assert second["inserted"] == 0
        assert second["updated"] == 1


# ─── Unmatched-companies report ─────────────────────────────────────────────


class TestUnmatchedReport:
    def test_reports_companies_with_no_sponsorship_row(self, factories):
        with factories() as db:
            db.add(
                EmployerSponsorship(
                    normalized_name="stripe",
                    display_name="Stripe",
                    lca_count_12mo=5,
                    lca_count_3yr=20,
                    source_file="test",
                )
            )
            db.commit()

            unmatched = report_unmatched_companies(db, ["Stripe Inc", "Acme Corp", "Some Unknown"])
        # Stripe is matched; the other two aren't.
        assert sorted(u["normalized"] for u in unmatched) == sorted(["acme", "some unknown"])


# ─── Process-wide one-shot unmatched logging ────────────────────────────────


def test_unmatched_companies_are_logged_once(factories, monkeypatch):
    """The lookup helper logs every unmatched company once (process-
    wide) so the operator can grep `sponsorship unmatched` for naming
    gaps. The dedupe keeps the noise floor sane."""
    import app.services.sponsorship as svc

    # Reset the module-global dedupe set so this test starts fresh.
    monkeypatch.setattr(svc, "_UNMATCHED_LOGGED", set())
    # Spy on the module's log.info — more robust than `caplog`, which
    # is sensitive to root-logger config bleed from other test modules.
    seen: list[tuple] = []
    monkeypatch.setattr(svc.log, "info", lambda msg, *args, **kw: seen.append((msg, args)))

    with factories() as db:
        lookup_signals_for_companies(db, ["NoMatchCo"])
        lookup_signals_for_companies(db, ["NoMatchCo"])  # second time — no new log
    matching = [s for s in seen if "sponsorship unmatched" in s[0]]
    assert len(matching) == 1
    # The args carry (original_name, normalised_name).
    assert "NoMatchCo" in matching[0][1]


# Light sanity check on the constant — pinning to default 5 so the
# "Sponsors H-1B" badge doesn't quietly turn into a much-looser
# signal across refactors.
def test_default_threshold_is_5():
    assert DEFAULT_CONSERVATIVE_THRESHOLD == 5


# Pin the `reference_date` math: a row dated 366 days before the
# reference is OUTSIDE the 12-month window. Guards against an off-
# by-one that would cause the conservative badge to fire on stale
# data.
def test_aggregate_window_is_exactly_365_days():
    ref = date(2024, 12, 31)
    edge = ref - timedelta(days=365)
    just_outside = ref - timedelta(days=366)
    agg = aggregate_rows(
        [("Acme", edge, "Eng"), ("Acme", just_outside, "Eng")],
        reference_date=ref,
    )["acme"]
    assert agg.lca_count_12mo == 1


def test_model_default_columns_round_trip(factories):
    """A row inserted with only the required columns should pick up
    the SQLAlchemy defaults — exercises the column-default plumbing
    end-to-end."""
    with factories() as db:
        db.add(
            EmployerSponsorship(
                normalized_name="default-test",
                display_name="Default Test",
            )
        )
        db.commit()
        row = db.query(EmployerSponsorship).filter_by(normalized_name="default-test").one()
        assert row.lca_count_12mo == 0
        assert row.lca_count_3yr == 0
        assert row.distinct_titles_12mo == 0
        assert isinstance(row.last_loaded_at, datetime)


# A regression pin: the bulk lookup must NEVER touch the database
# more than once. The function is the API-side hot path; an N+1 here
# would tank job-listing latency the moment the sponsorship table has
# real volume.
def test_lookup_uses_a_single_query(factories, monkeypatch):
    import sqlalchemy.orm

    with factories() as db:
        db.add(
            EmployerSponsorship(
                normalized_name="stripe",
                display_name="Stripe",
                lca_count_12mo=10,
                lca_count_3yr=30,
                source_file="test",
            )
        )
        db.commit()

        query_count = 0
        original_execute = sqlalchemy.orm.Session.execute

        def counting_execute(self, *args, **kwargs):
            nonlocal query_count
            query_count += 1
            return original_execute(self, *args, **kwargs)

        monkeypatch.setattr(sqlalchemy.orm.Session, "execute", counting_execute)
        lookup_signals_for_companies(db, ["Stripe Inc", "Acme Corp", "Stripe LLC", "Some Other Co"])
        # One SELECT for the four companies, regardless of count.
        assert query_count == 1, f"expected 1 query, got {query_count} (N+1 regression)"


# Cross-check that the JobOut payload honours the same not-sponsored
# default: a company with no row reads as `sponsors_h1b=False` rather
# than `None`. The default lives on the Pydantic model itself.
def test_joboutput_default_signals_are_false(factories):
    """End-to-end sanity at the schema level. Failure here means the
    JobOut model started defaulting `sponsors_h1b` to None (or
    similar) — the UI would either crash on the null or, worse, treat
    null as 'unknown' and render a 'no badge' state the right way
    accidentally."""
    from app.api.jobs import JobOut

    minimal = JobOut(
        id=1,
        source="manual",
        external_id="x",
        company="Acme",
        title="Engineer",
        location=None,
        remote=None,
        employment_type=None,
        salary=None,
        skills=[],
        sponsors_visa=None,
        url="https://example.com",
        description=None,
        posted_at=None,
        source_updated_at=None,
    )
    assert minimal.sponsors_h1b is False
    assert minimal.past_h1b_activity is False
    assert minimal.lca_count_12mo == 0


# Smoke test for the date helper edge case the DOL CSV will hit.
def test_parse_date_excel_export_form():
    """Excel-exported CSVs sometimes emit `MM/DD/YY` (two-digit year)
    when the column is shorter than a date. The loader should
    tolerate that — `06/01/24` is unambiguously 2024."""
    parsed = cli._parse_date("06/01/24")
    assert parsed == date(2024, 6, 1)


# The `most_recent_filing` field on the aggregate row is the max of
# all observed filings — not the first, not the last in iteration
# order. Pin that contract.
def test_most_recent_filing_is_max_not_last():
    rows = [
        ("Acme", date(2024, 6, 1), "Eng"),
        ("Acme", date(2024, 1, 1), "Eng"),  # earlier than the first row
        ("Acme", date(2024, 11, 1), "Eng"),
    ]
    agg = aggregate_rows(rows, reference_date=date(2024, 12, 31))["acme"]
    assert agg.most_recent_filing == date(2024, 11, 1)


# The CLI's `--report-unmatched` flag needs a Jobs table to scan.
def test_run_with_report_unmatched_dumps_jobs_table_gaps(tmp_path, factories, monkeypatch):
    from app.models.job import Job

    path = _write_csv(
        tmp_path,
        ["CASE_STATUS", "DECISION_DATE", "EMPLOYER_NAME", "JOB_TITLE"],
        [["Certified", "2024-06-01", "Stripe Inc", "Engineer"]],
    )
    monkeypatch.setattr(cli, "SessionLocal", factories)

    # Seed the jobs table with two companies: one matches (Stripe),
    # one doesn't (Acme).
    now = datetime.now(UTC)
    with factories() as db:
        db.add_all(
            [
                Job(
                    source="greenhouse",
                    external_id="stripe-1",
                    company="Stripe",
                    title="Eng",
                    skills=[],
                    url="https://stripe.example",
                    source_updated_at=now,
                ),
                Job(
                    source="greenhouse",
                    external_id="acme-1",
                    company="Acme Corp",
                    title="Eng",
                    skills=[],
                    url="https://acme.example",
                    source_updated_at=now,
                ),
            ]
        )
        db.commit()

    report = cli.run(
        path,
        source="FY2024_Q4",
        reference_date=date(2024, 12, 31),
        report_unmatched=True,
    )
    unmatched_norms = [u["normalized"] for u in report["unmatched"]]
    assert "acme" in unmatched_norms
    assert "stripe" not in unmatched_norms


# ─── Migration 0013_employer_sponsorship ────────────────────────────────────


def _alembic_config(db_url: str) -> Config:
    backend_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_creates_employer_sponsorship_table(tmp_path, monkeypatch):
    """Alembic upgrade to head creates the `employer_sponsorship`
    table with the expected columns + the primary-key index. The
    downgrade path drops it cleanly."""
    from app import config as config_module

    db_path = tmp_path / "sponsorship.sqlite"
    db_url = f"sqlite+pysqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    config_module.get_settings.cache_clear()
    try:
        cfg = _alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        insp = inspect(engine)
        assert "employer_sponsorship" in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("employer_sponsorship")}
        assert {
            "normalized_name",
            "display_name",
            "lca_count_12mo",
            "lca_count_3yr",
            "most_recent_filing",
            "distinct_titles_12mo",
            "source_file",
            "last_loaded_at",
        }.issubset(cols)

        # The downgrade path also works — required for safe rollback.
        command.downgrade(cfg, "0012_users_table")
        insp = inspect(create_engine(db_url, future=True))
        assert "employer_sponsorship" not in insp.get_table_names()

        # And re-upgrading is fine (no leftover state).
        command.upgrade(cfg, "head")
        insp = inspect(create_engine(db_url, future=True))
        assert "employer_sponsorship" in insp.get_table_names()
    finally:
        config_module.get_settings.cache_clear()
