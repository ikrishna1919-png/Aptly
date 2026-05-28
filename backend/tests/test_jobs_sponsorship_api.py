"""API-side sponsorship surfaces: badges on `JobOut` and the
`sponsors_h1b` / `past_h1b_activity` filters on `/api/jobs`.

The sponsorship data and the jobs feed are joined at serve time;
these tests verify the join is correct, the badges follow the
not-misleading semantics from the spec (a missing employer row is
NOT a "doesn't sponsor" claim), and the filters never collapse the
two distinct signals.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models.employer_sponsorship import EmployerSponsorship
from app.models.job import Job


@pytest.fixture
def factories():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


@pytest.fixture
def client(factories):
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        HOURS_WINDOW=48,
    )

    def override_db():
        with factories() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app), factories
    finally:
        app.dependency_overrides.clear()


def _seed_world(Session):
    """Three jobs at three different companies, each with a different
    sponsorship profile:

      * Stripe — heavy sponsor (conservative + inclusive badges).
      * Tiny Solutions — small sponsor, below the conservative
        threshold but above zero in 3yr (inclusive only).
      * Unknown Co — absent from `employer_sponsorship` (no badges).
    """
    import app.services.sponsorship as svc

    # Reset the unmatched-logged dedupe set so per-test logs don't
    # leak — keeps test isolation intact for assertions that depend
    # on the lookup having "first-seen" semantics.
    svc._UNMATCHED_LOGGED.clear()

    now = datetime.now(UTC)
    with Session() as s:
        s.add_all(
            [
                Job(
                    source="greenhouse",
                    external_id="stripe-1",
                    company="Stripe, Inc.",
                    title="Backend Engineer",
                    url="https://stripe.example",
                    source_updated_at=now - timedelta(hours=1),
                    skills=["Python"],
                ),
                Job(
                    source="greenhouse",
                    external_id="tiny-1",
                    company="Tiny Solutions",
                    title="Engineer",
                    url="https://tiny.example",
                    source_updated_at=now - timedelta(hours=1),
                    skills=["Go"],
                ),
                Job(
                    source="greenhouse",
                    external_id="unknown-1",
                    company="Unknown Co",
                    title="Engineer",
                    url="https://unknown.example",
                    source_updated_at=now - timedelta(hours=1),
                    skills=[],
                ),
            ]
        )
        s.add_all(
            [
                EmployerSponsorship(
                    normalized_name="stripe",
                    display_name="Stripe",
                    lca_count_12mo=40,
                    lca_count_3yr=150,
                    most_recent_filing=date(2024, 10, 1),
                    distinct_titles_12mo=12,
                    source_file="FY2024_Q4",
                ),
                EmployerSponsorship(
                    normalized_name="tiny solutions",
                    display_name="Tiny Solutions",
                    lca_count_12mo=1,
                    lca_count_3yr=2,
                    most_recent_filing=date(2023, 4, 1),
                    distinct_titles_12mo=1,
                    source_file="FY2024_Q4",
                ),
            ]
        )
        s.commit()


def _job_by_company(jobs: list[dict], company_substr: str) -> dict:
    for job in jobs:
        if company_substr.lower() in job["company"].lower():
            return job
    raise AssertionError(f"no job with company containing {company_substr!r} found")


def test_list_jobs_attaches_signals_by_normalized_name(client):
    test_client, Session = client
    _seed_world(Session)
    res = test_client.get("/api/jobs")
    assert res.status_code == 200
    jobs = res.json()["jobs"]
    assert len(jobs) == 3

    stripe = _job_by_company(jobs, "stripe")
    assert stripe["sponsors_h1b"] is True
    assert stripe["past_h1b_activity"] is True
    assert stripe["lca_count_12mo"] == 40
    assert stripe["lca_count_3yr"] == 150
    assert stripe["most_recent_lca_filing"] == "2024-10-01"

    tiny = _job_by_company(jobs, "tiny")
    # Below the conservative threshold — only the inclusive badge.
    assert tiny["sponsors_h1b"] is False
    assert tiny["past_h1b_activity"] is True

    unknown = _job_by_company(jobs, "unknown")
    # No row → no badge. NEVER a "does not sponsor" badge.
    assert unknown["sponsors_h1b"] is False
    assert unknown["past_h1b_activity"] is False
    assert unknown["lca_count_12mo"] == 0


def test_get_job_single_endpoint_carries_signals(client):
    test_client, Session = client
    _seed_world(Session)
    jobs = test_client.get("/api/jobs").json()["jobs"]
    stripe_id = _job_by_company(jobs, "stripe")["id"]
    detail = test_client.get(f"/api/jobs/{stripe_id}").json()
    assert detail["sponsors_h1b"] is True
    assert detail["past_h1b_activity"] is True


def test_sponsors_h1b_filter_returns_only_conservative_signal(client):
    test_client, Session = client
    _seed_world(Session)
    res = test_client.get("/api/jobs?sponsors_h1b=true").json()
    # Only Stripe — Tiny Solutions has only inclusive activity.
    assert {j["company"] for j in res["jobs"]} == {"Stripe, Inc."}
    assert res["total"] == 1


def test_past_h1b_filter_includes_below_threshold_sponsors(client):
    test_client, Session = client
    _seed_world(Session)
    res = test_client.get("/api/jobs?past_h1b_activity=true").json()
    # Both Stripe AND Tiny Solutions — distinct from the conservative filter.
    companies = {j["company"] for j in res["jobs"]}
    assert "Stripe, Inc." in companies
    assert "Tiny Solutions" in companies
    assert "Unknown Co" not in companies


def test_both_filters_can_combine(client):
    """A user who wants BOTH badges (a sponsor with recent + sustained
    activity) sends both. AND-semantics — only Stripe."""
    test_client, Session = client
    _seed_world(Session)
    res = test_client.get("/api/jobs?sponsors_h1b=true&past_h1b_activity=true").json()
    assert {j["company"] for j in res["jobs"]} == {"Stripe, Inc."}


def test_sponsors_h1b_false_is_not_a_negative_filter(client):
    """`sponsors_h1b=false` must NOT be honoured as 'show jobs at
    companies that don't sponsor'. The DOL data is incomplete and
    naming mismatches are common — silence isn't evidence. False is
    treated as 'no filter'."""
    test_client, Session = client
    _seed_world(Session)
    res = test_client.get("/api/jobs?sponsors_h1b=false").json()
    # All three jobs come back, same as no filter.
    assert {j["company"] for j in res["jobs"]} == {
        "Stripe, Inc.",
        "Tiny Solutions",
        "Unknown Co",
    }


def test_filters_compose_with_text_search(client):
    test_client, Session = client
    _seed_world(Session)
    # Search for "engineer" AND require conservative sponsorship — only Stripe.
    res = test_client.get("/api/jobs?q=engineer&sponsors_h1b=true").json()
    assert {j["company"] for j in res["jobs"]} == {"Stripe, Inc."}


def test_company_with_no_jobs_does_not_break_lookup(client):
    """If a sponsor row exists for a company that ISN'T in the jobs
    table this pass, the endpoint must not 500. The bulk-lookup
    helper takes the live job companies as input — it doesn't try to
    join the other direction."""
    test_client, Session = client
    with Session() as s:
        s.add(
            EmployerSponsorship(
                normalized_name="ghost",
                display_name="Ghost Co",
                lca_count_12mo=20,
                lca_count_3yr=60,
                most_recent_filing=date(2024, 8, 1),
                source_file="FY2024_Q4",
            )
        )
        s.commit()
    res = test_client.get("/api/jobs")
    assert res.status_code == 200
