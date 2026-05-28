"""Raw LLM output capture + defensive bullet split.

The PDF / DOCX parsing regression has surfaced two recurring
failures the operator needs visibility into:

  1. The model occasionally returns concatenated bullets in a single
     string instead of a list. The parser now defensively splits
     them (`_normalise_bullets`) before mapping into the Profile so
     the UI renders one bullet per line instead of a run-on blob.
  2. When a parse looks "wrong" it's hard to tell whether the LLM
     returned bad data or the mapping / display lost something.
     The worker now PERSISTS the raw structured-output JSON on
     `ParseRun.raw_llm_output` so the operator can grep one row and
     see exactly what the model returned — extraction vs. mapping
     vs. display becomes a one-query distinction.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models.parse_run import PARSE_STATUS_SUCCESS, ParseRun
from app.services import profile_parser as parser_module
from app.services.profile_parser import _normalise_bullets

# ─── Defensive bullet split ────────────────────────────────────────────────


class TestNormaliseBullets:
    def test_clean_list_passes_through(self):
        out = _normalise_bullets(["Did X", "Did Y", "Did Z"])
        assert out == ["Did X", "Did Y", "Did Z"]

    def test_empty_and_whitespace_are_dropped(self):
        assert _normalise_bullets(["", "   ", "\n", "Real bullet"]) == ["Real bullet"]

    def test_run_on_blob_with_embedded_glyphs_is_split(self):
        """The failure mode: the model returns one string with
        newline-glyph-newline-glyph instead of a proper list. We
        split on those boundaries so each bullet ends up as its
        own list item."""
        joined = (
            "Cut p95 latency 480ms to 110ms via Redis cache\n"
            "• Mentored 4 junior engineers\n"
            "• Owned the on-call rotation for the platform team"
        )
        out = _normalise_bullets([joined])
        assert out == [
            "Cut p95 latency 480ms to 110ms via Redis cache",
            "Mentored 4 junior engineers",
            "Owned the on-call rotation for the platform team",
        ]

    def test_dash_glyph_split(self):
        joined = "Built billing pipeline\n- Stripe + Postgres\n- $30M ARR"
        out = _normalise_bullets([joined])
        assert out == ["Built billing pipeline", "Stripe + Postgres", "$30M ARR"]

    def test_numbered_list_split(self):
        joined = "Migrated to event-driven Kafka\n1. 12 services shipped\n2. 0 downtime"
        out = _normalise_bullets([joined])
        assert out == [
            "Migrated to event-driven Kafka",
            "12 services shipped",
            "0 downtime",
        ]

    def test_blank_line_split(self):
        """A blank line between two bullets is also a split signal —
        some models emit this shape instead of explicit glyphs."""
        joined = "Led the migration\n\nMentored four juniors"
        out = _normalise_bullets([joined])
        assert out == ["Led the migration", "Mentored four juniors"]

    def test_single_multiline_bullet_without_markers_stays_intact(self):
        """A long bullet that happens to wrap should NOT be split.
        Only embedded bullet markers / blank lines trigger the
        split."""
        wrapped = (
            "Led the migration of the legacy monolith to event-driven Kafka with zero downtime"
        )
        out = _normalise_bullets([wrapped])
        assert out == [wrapped]


# ─── Worker persists raw LLM output ─────────────────────────────────────────


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


def _seed_run(Session, run_id: str = "raw") -> None:
    from app.models.parse_run import PARSE_STATUS_RUNNING

    with Session() as s:
        s.add(ParseRun(run_id=run_id, status=PARSE_STATUS_RUNNING, user_id=None))
        s.commit()


def _row(Session, run_id: str) -> ParseRun:
    with Session() as s:
        return s.execute(select(ParseRun).where(ParseRun.run_id == run_id)).scalar_one()


class _CapturingClient:
    def __init__(self, payload: dict) -> None:
        self.calls: list[dict] = []
        self.payload = payload
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        import json

        text_block = SimpleNamespace(type="text", text=json.dumps(self.payload))
        return SimpleNamespace(content=[text_block])


def test_text_worker_persists_raw_llm_output(factories, monkeypatch):
    """`_execute_parse_run` writes the verbatim model JSON into
    `ParseRun.raw_llm_output`. The shape on disk is the SAME shape
    Anthropic returned — no remapping, no truncation, no
    re-serialisation through the Profile model. The operator can
    look at this row and see exactly what the model produced."""
    payload = {
        "name": "Jordan Singh",
        "experience": [
            {
                "company": "Acme",
                "title": "Senior Engineer",
                "location": "Remote",
                "start_date": "Jan 2022",
                "end_date": "Present",
                "description_bullets": ["Built X.", "Shipped Y."],
            }
        ],
        "education": [],
        "skills": ["Python"],
        "projects": [],
        "achievements": [],
        "certifications": [],
        "section_order": ["experience", "skills"],
    }
    mock = _CapturingClient(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda t, a: t(*a))

    _seed_run(factories, "raw-text")
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        ANTHROPIC_API_KEY="sk-test",
    )
    parser_module._execute_parse_run("raw-text", "Jordan Singh\n", settings)

    run = _row(factories, "raw-text")
    assert run.status == PARSE_STATUS_SUCCESS
    # The raw LLM payload is preserved verbatim.
    assert run.raw_llm_output is not None
    assert run.raw_llm_output["name"] == "Jordan Singh"
    assert (
        run.raw_llm_output["experience"][0]["company"] == "Acme"
    ), "raw output's company field MUST be preserved (no swap)"
    assert run.raw_llm_output["experience"][0]["title"] == "Senior Engineer"
    # And the parsed profile carries the same — sanity check that
    # the mapping doesn't introduce a swap.
    assert run.profile["experience"][0]["company"] == "Acme"
    assert run.profile["experience"][0]["title"] == "Senior Engineer"


def test_pdf_worker_persists_raw_llm_output(factories, monkeypatch):
    """Same contract for the PDF path."""
    payload = {
        "name": "Dana Sponsor",
        "experience": [],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [],
        "certifications": [
            {
                "name": "AWS Certified Solutions Architect",
                "issuer": "Amazon Web Services",
                "date": "2024",
                "credential_id": "ABC-1",
            }
        ],
        "section_order": [],
    }
    mock = _CapturingClient(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda t, a: t(*a))

    _seed_run(factories, "raw-pdf")
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        ANTHROPIC_API_KEY="sk-test",
    )
    parser_module._execute_parse_run_pdf("raw-pdf", b"%PDF-1.4\nfake", settings)

    run = _row(factories, "raw-pdf")
    assert run.status == PARSE_STATUS_SUCCESS
    assert run.raw_llm_output is not None
    assert run.raw_llm_output["certifications"][0]["name"].startswith("AWS Certified")


def test_no_key_worker_leaves_raw_llm_output_null(factories, monkeypatch):
    """When `ANTHROPIC_API_KEY` is empty the parser stays on the
    regex path — there's no LLM JSON to capture. The column should
    remain NULL so the operator can tell "we never called the LLM"
    apart from "the LLM call returned junk"."""
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda t, a: t(*a))

    _seed_run(factories, "raw-nokey")
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:", ADMIN_TOKEN="t")
    parser_module._execute_parse_run("raw-nokey", "Jordan Singh\nalex@example.com\n", settings)

    run = _row(factories, "raw-nokey")
    assert run.status == PARSE_STATUS_SUCCESS
    assert run.raw_llm_output is None


def test_concatenated_bullets_in_llm_output_are_split_in_profile(factories, monkeypatch):
    """End-to-end backstop: even if the model returns a single
    string with embedded bullets, the saved profile has them as
    separate list items so the UI renders one bullet per line."""
    payload = {
        "name": "Jordan Singh",
        "experience": [
            {
                "company": "Acme",
                "title": "Senior Engineer",
                "location": None,
                "start_date": "2022",
                "end_date": "Present",
                # The failure mode: bullets glued into one string.
                "description_bullets": [
                    "Cut p95 latency 480ms→110ms\n• Mentored 4 juniors\n• Ran weekly arch review"
                ],
            }
        ],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [],
        "certifications": [],
        "section_order": [],
    }
    mock = _CapturingClient(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda t, a: t(*a))

    _seed_run(factories, "blob")
    settings = Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        ANTHROPIC_API_KEY="sk-test",
    )
    parser_module._execute_parse_run("blob", "Jordan Singh\n", settings)

    run = _row(factories, "blob")
    bullets = run.profile["experience"][0]["bullets"]
    assert len(bullets) == 3, f"expected 3 split bullets, got: {bullets!r}"
    assert bullets[0].startswith("Cut p95 latency")
    assert bullets[1].startswith("Mentored 4 juniors")
    assert bullets[2].startswith("Ran weekly arch review")
    # The RAW row still shows the original concatenated form — that
    # asymmetry is what tells an operator "the LLM glued these
    # together; the parser split them on the way to disk." Without
    # storing both forms the failure is invisible.
    assert (
        "\n•" in run.raw_llm_output["experience"][0]["description_bullets"][0]
    ), "raw output should preserve the model's verbatim shape"


def test_finish_parse_writes_raw_output_column(factories, monkeypatch):
    """Direct contract test on `_finish_parse`: when
    `raw_llm_output` is passed, the column gets set; when it's
    omitted, the column is None (default)."""
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    _seed_run(factories, "direct")
    parser_module._finish_parse(
        "direct",
        status=PARSE_STATUS_SUCCESS,
        profile={"name": "X"},
        error=None,
        raw_llm_output={"verbatim": True},
    )
    row = _row(factories, "direct")
    assert row.raw_llm_output == {"verbatim": True}

    # Now an update without raw_llm_output clears it back to None.
    _seed_run(factories, "no-raw")
    parser_module._finish_parse(
        "no-raw",
        status=PARSE_STATUS_SUCCESS,
        profile={"name": "Y"},
        error=None,
    )
    assert _row(factories, "no-raw").raw_llm_output is None


def test_run_llm_extract_appends_to_raw_sink(monkeypatch):
    """Unit test on the LLM helper itself: when given a sink, it
    appends the verbatim text response BEFORE parsing."""
    payload = {
        "name": "Test",
        "experience": [],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [],
        "certifications": [],
        "section_order": [],
    }
    mock = _CapturingClient(payload)
    monkeypatch.setattr(parser_module, "_build_client", lambda s, c: mock)
    sink: list[str] = []
    settings = Settings(DATABASE_URL="x", ADMIN_TOKEN="t", ANTHROPIC_API_KEY="sk-test")
    parser_module._llm_extract_structural(
        "Test resume", settings=settings, run_id="t", raw_sink=sink
    )
    assert len(sink) == 1
    import json

    assert json.loads(sink[0])["name"] == "Test"
