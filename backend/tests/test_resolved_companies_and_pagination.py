"""Migration 0015 + the pagination / host-variant changes that ship
with it.

Two halves:

  Part 1: migration 0015_resolved_companies seeds every (source, token)
          listed in the migration, idempotently.

  Part 2: the Workday adapter accepts both the legacy
          `tenant:dc:site` form AND the new
          `tenant:dc:site:host` form for tenants served from the
          `myworkdaysite.com` variant; both forms produce the right
          URL into the right host. The SmartRecruiters + Workday
          per-company caps are raised so a real enterprise board
          isn't truncated, AND the cap is still defensively
          enforced when reached.
"""

from __future__ import annotations

import pathlib
from typing import Any

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.models.source import Source
from app.sources.workday import (
    _MAX_POSTINGS_PER_COMPANY,
    ALTERNATE_WORKDAY_HOST,
    DEFAULT_WORKDAY_HOST,
    WorkdaySource,
    _parse_token,
)

# ─── Migration 0015 ─────────────────────────────────────────────────────────


def _alembic_config(db_url: str) -> Config:
    backend_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    from app import config as config_module

    db_path = tmp_path / "resolved.sqlite"
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


# Hand-checked subset — every entry MUST be in the seeded table.
# Listing them here pins the spec from the task description; missing
# any of these surfaces as a clear failure rather than a vague
# "row count off by N".
_EXPECTED_ROWS = {
    ("greenhouse", "clear"),
    ("greenhouse", "shift4"),
    ("greenhouse", "robinhood"),
    ("greenhouse", "evolutioncloudservicesevocs"),
    ("greenhouse", "andurilindustries"),
    ("greenhouse", "fglife"),
    ("lever", "zeta"),
    ("lever", "veeva"),
    ("smartrecruiters", "Socotec"),
    ("ashby", "sentilink"),
    ("workday", "mpc:wd1:MPCCareers"),
    ("workday", "copart:wd12:Copart"),
    ("workday", "bcbst:wd1:External"),
    ("workday", "healthcare:wd1:Search"),
    ("workday", "geico:wd1:External"),
    ("workday", "guidehouse:wd1:External"),
    ("workday", "wintrust:wd1:Search"),
    ("workday", "osv-cci:wd1:CCICareers"),
    ("workday", "pennmutual:wd1:_penn-careers"),
    ("workday", "meredith:wd5:EXT"),
    ("workday", "oldrepublic:wd1:oldrepublictitle"),
    ("workday", "rlicorp:wd1:RLI_Corp_Careers:myworkdaysite.com"),
}


def test_migration_seeds_every_resolved_company(migrated_db):
    Session, _ = migrated_db
    with Session() as s:
        rows = {(r.source_type, r.token) for r in s.execute(select(Source)).scalars()}
    missing = _EXPECTED_ROWS - rows
    assert not missing, f"resolved-companies seed missing: {sorted(missing)}"


def test_migration_preserves_smartrecruiters_case(migrated_db):
    """`Socotec` (case-sensitive identifier) must NOT be lowercased
    on the way in — the adapter sends the literal value to
    SmartRecruiters and lowercasing would 404."""
    Session, _ = migrated_db
    with Session() as s:
        present = (
            s.execute(
                select(Source).where(
                    Source.source_type == "smartrecruiters", Source.token == "Socotec"
                )
            )
            .scalars()
            .one_or_none()
        )
    assert present is not None, "Socotec missing — case probably mangled"


def test_migration_seeds_alternate_workday_host_token(migrated_db):
    """The single token in this batch that uses the
    `myworkdaysite.com` host variant — verify it lands verbatim so
    the adapter sees the 4-part form and routes to the right host."""
    Session, _ = migrated_db
    with Session() as s:
        rli = (
            s.execute(
                select(Source).where(
                    Source.source_type == "workday",
                    Source.token == "rlicorp:wd1:RLI_Corp_Careers:myworkdaysite.com",
                )
            )
            .scalars()
            .one_or_none()
        )
    assert rli is not None
    assert rli.display_name == "RLI Corp"


def test_migration_is_idempotent(migrated_db):
    """Downgrade-then-upgrade must not change row counts."""
    Session, cfg = migrated_db
    with Session() as s:
        first = s.execute(select(func.count()).select_from(Source)).scalar_one()

    command.downgrade(cfg, "0014_bulk_load_ats_tokens")
    command.upgrade(cfg, "head")

    with Session() as s:
        second = s.execute(select(func.count()).select_from(Source)).scalar_one()
    assert second == first


def test_migration_downgrade_removes_only_resolved_rows(migrated_db):
    """0015 downgrade should drop all 22 resolved rows. Other migrations'
    rows remain untouched."""
    Session, cfg = migrated_db
    with Session() as s:
        before = s.execute(select(func.count()).select_from(Source)).scalar_one()
    command.downgrade(cfg, "0014_bulk_load_ats_tokens")
    with Session() as s:
        after = s.execute(select(func.count()).select_from(Source)).scalar_one()
        for source_type, token in _EXPECTED_ROWS:
            row = s.execute(
                select(Source).where(Source.source_type == source_type, Source.token == token)
            ).scalar_one_or_none()
            # Some tokens may overlap with earlier seeds (e.g. greenhouse
            # `robinhood` was already in the baseline). Those keep their
            # row in the table after downgrade — `oldrepublic` and the
            # rest of the workday/ashby/smartrecruiters/lever rows are
            # unique to this migration and must be gone.
            if (source_type, token) in {
                ("greenhouse", "robinhood"),  # in companies.py baseline
            }:
                continue
            assert row is None, f"{source_type}:{token} still present after downgrade"
    # The downgrade dropped at least the unique-to-0015 entries.
    assert before - after >= len(_EXPECTED_ROWS) - 1


# ─── Workday alternate host ─────────────────────────────────────────────────


class TestWorkdayHostVariant:
    def test_parse_token_defaults_host_to_myworkdayjobs(self):
        tenant, dc, site, host = _parse_token("generalmotors:wd5:Careers_GM")
        assert (tenant, dc, site) == ("generalmotors", "wd5", "Careers_GM")
        assert host == DEFAULT_WORKDAY_HOST

    def test_parse_token_accepts_four_part_alternate_host(self):
        tenant, dc, site, host = _parse_token("rlicorp:wd1:RLI_Corp_Careers:myworkdaysite.com")
        assert (tenant, dc, site) == ("rlicorp", "wd1", "RLI_Corp_Careers")
        assert host == ALTERNATE_WORKDAY_HOST

    def test_parse_token_rejects_unknown_host(self):
        from app.sources.base import SourceUnavailable

        with pytest.raises(SourceUnavailable, match="unknown host"):
            _parse_token("acme:wd1:Site:bogus.example.com")

    def test_parse_token_rejects_malformed(self):
        from app.sources.base import SourceUnavailable

        with pytest.raises(SourceUnavailable, match="malformed token"):
            _parse_token("only:two")
        with pytest.raises(SourceUnavailable, match="malformed token"):
            _parse_token(":empty:components:")

    def test_fetch_uses_alternate_host_in_urls(self):
        """End-to-end check: a 4-part token routes both LIST and
        DETAIL requests at `myworkdaysite.com`, not the default
        host. Without this the adapter would 404 forever on every
        ingest pass for an RLI-style tenant."""
        list_payload = {
            "total": 1,
            "jobPostings": [
                {
                    "title": "Underwriter",
                    "externalPath": "/job/underwriter",
                    "postedOn": "Posted Today",
                }
            ],
        }
        detail_payload = {
            "jobPostingInfo": {
                "id": "JR-1",
                "title": "Underwriter",
                "jobDescription": "<p>Insurance role.</p>",
                "startDate": "2026-05-25",
                "timeType": "Full time",
            }
        }
        seen_hosts: set[str] = set()

        def handler(request: httpx.Request) -> httpx.Response:
            seen_hosts.add(request.url.host)
            if request.method == "POST":
                return httpx.Response(200, json=list_payload)
            return httpx.Response(200, json=detail_payload)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        source = WorkdaySource(client=client)
        jobs = list(source.fetch("rlicorp:wd1:RLI_Corp_Careers:myworkdaysite.com"))
        assert len(jobs) == 1
        # Both the list POST and the detail GET landed on the
        # alternate host.
        assert seen_hosts == {f"rlicorp.wd1.{ALTERNATE_WORKDAY_HOST}"}


# ─── Cap was raised + cap-hit warning ───────────────────────────────────────


def test_workday_cap_is_well_above_legacy_default():
    """The cap was 200 before; the spec asked us to pull EVERY job
    per company. 1500 is the new floor — enough for any real public
    Workday board. Pin a >=1000 floor so any future tightening shows
    up in review."""
    assert _MAX_POSTINGS_PER_COMPANY >= 1000


def test_smartrecruiters_cap_is_well_above_legacy_default():
    from app.sources.smartrecruiters import _MAX_POSTINGS_PER_COMPANY as sr_cap

    assert sr_cap >= 1000


def test_workday_logs_when_cap_is_hit(monkeypatch):
    """When the API returns more postings than the configured cap,
    the adapter emits a single WARNING so the operator can decide
    whether to raise the cap further. Use a small explicit cap to
    keep the test cheap. Spying on the module logger directly is
    more robust than `caplog` here because `caplog`'s root-logger
    plumbing is fragile across test modules."""
    # 25 postings on the first list page; cap configured to 5.
    list_payload = {
        "total": 25,
        "jobPostings": [
            {"title": f"R{i}", "externalPath": f"/job/{i}", "postedOn": "Posted Today"}
            for i in range(25)
        ],
    }
    detail_payload = {
        "jobPostingInfo": {
            "id": "X",
            "title": "X",
            "jobDescription": "x",
            "startDate": "2026-05-25",
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=list_payload)
        return httpx.Response(200, json=detail_payload)

    import app.sources.workday as workday_module

    seen: list[str] = []
    monkeypatch.setattr(
        workday_module.log,
        "warning",
        lambda msg, *args, **kw: seen.append(msg % args if args else msg),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = WorkdaySource(client=client, max_postings_per_company=5)
    jobs = list(source.fetch("acme:wd1:Careers"))
    assert len(jobs) == 5
    hits = [s for s in seen if "cap reached" in s]
    assert len(hits) == 1, f"expected one cap-hit warning, got: {seen!r}"


# ─── Ingest "fetched X, saved Y (within window)" log line ───────────────────


def test_ingest_log_line_says_saved_within_window(monkeypatch):
    """A regression pin on the operator-facing log format. Future
    changes can add fields, but the `fetched N, saved N` headline
    is what an operator greps for to tell a pagination problem from
    a window-filtering one. Spy directly on the module logger —
    more robust than `caplog` across the test suite."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.services.ingest as ingest_mod
    from app.config import Settings
    from app.database import Base
    from app.models.source import Source as SourceModel
    from app.services.ingest import run_ingest
    from app.sources.base import JobSource, NormalizedJob

    class _Fake(JobSource):
        name = "fake"

        def fetch(self, token: str) -> Any:
            now = datetime.now(UTC)
            return [
                NormalizedJob(
                    source="fake",
                    external_id="in",
                    company="acme",
                    title="In-window",
                    url="https://example.com/in",
                    source_updated_at=now - timedelta(hours=1),
                ),
                NormalizedJob(
                    source="fake",
                    external_id="out",
                    company="acme",
                    title="Out-of-window",
                    url="https://example.com/out",
                    source_updated_at=now - timedelta(days=10),
                ),
            ]

    seen: list[str] = []
    monkeypatch.setattr(
        ingest_mod.log,
        "info",
        lambda msg, *args, **kw: seen.append(msg % args if args else msg),
    )

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        src = SourceModel(source_type="fake", token="acme", enabled=True)
        s.add(src)
        s.commit()
        s.refresh(src)
        run_ingest(
            s,
            Settings(
                DATABASE_URL="sqlite+pysqlite:///:memory:",
                ADMIN_TOKEN="t",
                HOURS_WINDOW=48,
            ),
            sources=[src],
            source_factories={"fake": _Fake},
        )

    matching = [ln for ln in seen if "fetched 2" in ln and "saved 1" in ln]
    assert matching, f"missing 'fetched/saved' headline in log; got: {seen!r}"
    assert (
        "within window" in matching[0]
    ), f"log line lost the 'within window' qualifier: {matching[0]!r}"
