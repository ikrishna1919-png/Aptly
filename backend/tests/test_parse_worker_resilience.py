"""Regression tests for the parse-worker bulletproofing.

Symptom we're protecting against: a parse run reaches the worker but
never transitions to `success` or `failed` — leaves the row at
`running` forever and the frontend times out. The fixes under test:

  * `_execute_parse_run` writes a terminal status on every code
    path, with `try/except/finally` so a corruption mid-write still
    triggers a defensive write.
  * The Anthropic call carries a hard wall-clock ceiling
    (`_LLM_HARD_TIMEOUT_SECONDS`) enforced by
    `concurrent.futures.Future.result(timeout=…)`. A hung LLM call
    falls back to the regex result instead of blocking the worker.
  * Real exception messages are surfaced on `ParseRun.error` so the
    frontend can show them instead of a generic "timed out".
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models.parse_run import (
    PARSE_STATUS_FAILED,
    PARSE_STATUS_RUNNING,
    PARSE_STATUS_SUCCESS,
    ParseRun,
)
from app.services import profile_parser as parser_module


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
def inline_worker(factories, monkeypatch):
    """Route the worker through the test DB + drive it inline so we
    can assert the terminal row state in the same request."""
    monkeypatch.setattr(parser_module, "SessionLocal", factories)
    monkeypatch.setattr(parser_module, "_launch_worker", lambda target, args: target(*args))

    def _no_anthropic(*_a, **_k):
        raise RuntimeError("test stub: no Anthropic client wired")

    monkeypatch.setattr(parser_module, "_build_client", _no_anthropic)
    return factories


def _seed_run(Session, run_id: str = "run-1") -> None:
    with Session() as s:
        s.add(ParseRun(run_id=run_id, status=PARSE_STATUS_RUNNING, user_id=None))
        s.commit()


def _row(Session, run_id: str) -> ParseRun:
    with Session() as s:
        return s.execute(select(ParseRun).where(ParseRun.run_id == run_id)).scalar_one()


class TestWorkerAlwaysWritesTerminalStatus:
    def test_happy_path_writes_success(self, inline_worker):
        Session = inline_worker
        _seed_run(Session, "ok")
        parser_module._execute_parse_run("ok", "Alex Rivera\nalex@example.com\n")
        run = _row(Session, "ok")
        assert run.status == PARSE_STATUS_SUCCESS
        assert run.error is None
        assert run.finished_at is not None
        assert run.profile is not None
        # The text "Alex Rivera" is the regex parser's view (LLM
        # stubbed to raise → falls back to regex).
        assert run.profile["email"] == "alex@example.com"

    def test_exception_in_parse_writes_failed_with_real_message(self, inline_worker, monkeypatch):
        """If `parse_resume` itself raises (e.g. a defensive bug), the
        worker must catch it and write `failed` with the real
        exception message — never sit at `running`."""
        Session = inline_worker
        _seed_run(Session, "boom")

        def _boom(*_a, **_k):
            raise RuntimeError("simulated parse_resume crash")

        monkeypatch.setattr(parser_module, "parse_resume", _boom)
        parser_module._execute_parse_run("boom", "any text")

        run = _row(Session, "boom")
        assert run.status == PARSE_STATUS_FAILED
        assert "simulated parse_resume crash" in (run.error or "")
        assert run.finished_at is not None

    def test_defensive_finally_writes_failed_when_success_write_fails(
        self, inline_worker, monkeypatch
    ):
        """If the success-path `_finish_parse` raises AND the
        error-path catch re-raises, the `finally` block's last-ditch
        write must still leave the row at `failed` so the polling
        client doesn't sit on `running`.

        The pattern: monkeypatch `_finish_parse` so the FIRST call
        (the success write) raises, but the SECOND (the error-path
        write) succeeds — which is the realistic crash-recover-then-
        write order. We then assert the row is `failed`."""
        Session = inline_worker
        _seed_run(Session, "flaky")
        real_finish = parser_module._finish_parse
        calls: list[int] = []

        def _flaky_finish(run_id, *, status, profile, error):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("DB blip on success write")
            return real_finish(run_id, status=status, profile=profile, error=error)

        monkeypatch.setattr(parser_module, "_finish_parse", _flaky_finish)
        parser_module._execute_parse_run("flaky", "Alex Rivera\n")

        run = _row(Session, "flaky")
        assert (
            run.status == PARSE_STATUS_FAILED
        ), "row left at running — worker failed to write a terminal status"
        # Two attempts: success path (raised) + error path (succeeded).
        assert len(calls) == 2

    def test_total_defensive_only_runs_if_both_branches_failed(self, inline_worker, monkeypatch):
        """The defensive `finally` write should ONLY trigger when
        neither the success nor the error branch wrote a terminal
        status. On the happy path it's a no-op."""
        Session = inline_worker
        _seed_run(Session, "happy")
        finish_calls = 0
        real_finish = parser_module._finish_parse

        def _counted(run_id, *, status, profile, error):
            nonlocal finish_calls
            finish_calls += 1
            return real_finish(run_id, status=status, profile=profile, error=error)

        monkeypatch.setattr(parser_module, "_finish_parse", _counted)
        parser_module._execute_parse_run("happy", "Alex Rivera\n")

        # Exactly one finish call (the success write).
        assert finish_calls == 1
        assert _row(Session, "happy").status == PARSE_STATUS_SUCCESS


class TestWallClockTimeout:
    def test_llm_hang_falls_back_to_regex(self, factories, monkeypatch):
        """Simulate an Anthropic client whose `messages.create` blocks
        forever. The wall-clock ceiling must fire, the LLM exception
        catch in `parse_resume` must take it, and the worker must
        write `success` with the REGEX result (partial > empty)."""

        class _HangingMessages:
            def create(self, **_kwargs):
                # Block past any reasonable test wall — the wall-
                # clock ceiling fires long before this.
                time.sleep(60)
                raise AssertionError("should not be reached")

        class _HangingClient:
            messages = _HangingMessages()

        # Tighten the hard ceiling so the test runs fast.
        monkeypatch.setattr(parser_module, "_LLM_HARD_TIMEOUT_SECONDS", 0.5)
        monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _HangingClient())
        monkeypatch.setattr(parser_module, "SessionLocal", factories)
        monkeypatch.setattr(parser_module, "_launch_worker", lambda t, a: t(*a))

        _seed_run(factories, "hang")
        settings = Settings(
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            ADMIN_TOKEN="t",
            ANTHROPIC_API_KEY="sk-test",
        )
        parser_module._execute_parse_run("hang", "Alex Rivera\nalex@example.com\n", settings)

        run = _row(factories, "hang")
        # The worker terminates — never stays at running.
        assert run.status == PARSE_STATUS_SUCCESS
        # Regex fallback was used.
        assert run.profile is not None
        assert run.profile["email"] == "alex@example.com"

    def test_llm_explicit_exception_falls_back_to_regex_with_log(self, factories, monkeypatch):
        """When the SDK raises (not hangs), the worker still falls
        back to regex and writes `success` — the exception is logged
        but the user gets the regex result, not a failure."""

        class _RaisingMessages:
            def create(self, **_kwargs):
                raise RuntimeError("anthropic 500")

        class _RaisingClient:
            messages = _RaisingMessages()

        monkeypatch.setattr(parser_module, "_build_client", lambda s, c: _RaisingClient())
        monkeypatch.setattr(parser_module, "SessionLocal", factories)
        monkeypatch.setattr(parser_module, "_launch_worker", lambda t, a: t(*a))

        _seed_run(factories, "raise")
        settings = Settings(
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            ADMIN_TOKEN="t",
            ANTHROPIC_API_KEY="sk-test",
        )
        parser_module._execute_parse_run("raise", "Alex Rivera\nalex@example.com\n", settings)

        run = _row(factories, "raise")
        assert run.status == PARSE_STATUS_SUCCESS
        assert run.profile["email"] == "alex@example.com"


class TestErrorMessageFidelity:
    def test_failed_runs_carry_the_real_exception_message(self, inline_worker, monkeypatch):
        """When the worker DOES write `failed`, the error column
        carries the underlying exception's `str(e)` — not a generic
        'parse failed' string. The frontend reads this verbatim."""
        Session = inline_worker
        _seed_run(Session, "deet")

        def _boom(*_a, **_k):
            raise ValueError("could not read file: bad magic bytes")

        monkeypatch.setattr(parser_module, "parse_resume", _boom)
        parser_module._execute_parse_run("deet", "x")

        run = _row(Session, "deet")
        assert run.status == PARSE_STATUS_FAILED
        # The verbatim original message survives.
        assert "could not read file: bad magic bytes" in (run.error or "")


def test_log_lines_emit_a_run_id_tag(inline_worker, monkeypatch, caplog):
    """Pin the operator-grep contract: every step the worker takes
    emits a log line keyed on `parse_run=<id>`, so a single run's
    lifecycle is one grep away in Render's logs.

    Spy on the module logger directly — `caplog` config drifts
    across test modules in this codebase, so the spy is more
    reliable. We capture log messages by replacing `log.info` for
    the duration of the run."""
    Session = inline_worker
    _seed_run(Session, "logme")
    seen: list[str] = []

    real_info = parser_module.log.info

    def _capture_info(msg, *args, **kw):
        seen.append(msg % args if args else msg)
        real_info(msg, *args, **kw)

    monkeypatch.setattr(parser_module.log, "info", _capture_info)
    parser_module._execute_parse_run("logme", "Alex Rivera\nalex@example.com\n")

    joined = "\n".join(seen)
    assert "parse_run=logme: worker started" in joined
    assert "parse_run=logme: regex extract complete" in joined
    assert "parse_run=logme: terminal status=success written" in joined
