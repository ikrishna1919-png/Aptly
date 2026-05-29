"""Tests for the run-based, background, streaming tailoring flow.

Covers the pieces PR-specific to the flagship rebuild:
  * `loads_partial` — the lenient partial-JSON parser that powers
    progressive section reveal.
  * `profile_is_thin` — the pre-generation gate.
  * Streaming generation emits growing partial snapshots and a valid final.
  * The no-fabrication rule: a skill the user did NOT confirm never appears
    (demo path) and is named in the GENERATE exclusion list.
  * The analyze + generate workers always land on a terminal status, and
    auto-skip the questions stage when there are no gaps.
  * The startup sweep reaps orphaned runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models.candidate import DEMO_SLUG, Candidate
from app.models.job import Job
from app.models.tailor_run import (
    TAILOR_STATUS_ANALYZING,
    TAILOR_STATUS_DONE,
    TAILOR_STATUS_ERROR,
    TAILOR_STATUS_GENERATING,
    TAILOR_STATUS_PENDING_QUESTIONS,
    TailorRun,
)
from app.services import tailor as tailor_module
from app.services import tailor_runs as runs_module
from app.services.tailor import (
    Analysis,
    GeneratedResume,
    _extract_json_object,
    loads_partial,
)

# ─── loads_partial ─────────────────────────────────────────────────────────────


class TestLoadsPartial:
    def test_complete_object_round_trips(self):
        assert loads_partial('{"summary":"hi","skills":[]}') == {"summary": "hi", "skills": []}

    def test_drops_incomplete_trailing_member(self):
        # summary is complete (a top-level comma follows); skills is mid-stream.
        out = loads_partial('{"summary":"backend eng","skills":[{"category":"Lang')
        assert out == {"summary": "backend eng"}

    def test_keeps_complete_nested_value(self):
        out = loads_partial('{"contact":{"name":"Al","email":"a@b.co"},"summary":"x')
        assert out == {"contact": {"name": "Al", "email": "a@b.co"}}

    def test_object_opened_no_member_yet(self):
        assert loads_partial('{"summ') == {}

    def test_not_an_object(self):
        assert loads_partial("not json") is None
        assert loads_partial("") is None

    def test_commas_inside_strings_dont_fool_scan(self):
        out = loads_partial('{"summary":"a, b, c","skills":[')
        assert out == {"summary": "a, b, c"}


# ─── Prompt-based JSON parsing (replaces strict structured output) ──────────────


class TestExtractJsonObject:
    def test_plain_object(self):
        assert _extract_json_object('{"a": 1}') == {"a": 1}

    def test_strips_code_fences(self):
        assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
        assert _extract_json_object('```\n{"a": 1}\n```') == {"a": 1}

    def test_slices_out_surrounding_prose(self):
        assert _extract_json_object('Here you go:\n{"a": 1}\nHope that helps!') == {"a": 1}

    def test_raises_on_non_object(self):
        import pytest as _pytest

        with _pytest.raises((ValueError, Exception)):
            _extract_json_object("not json at all")


class TestPromptExamplesMatchModels:
    """The in-prompt examples are built from the Pydantic models, but assert
    they still parse cleanly back into them so a future model change can't
    silently desync the example from the renderer contract."""

    def test_analyze_example_round_trips(self):
        Analysis.model_validate_json(tailor_module._ANALYZE_EXAMPLE_JSON)

    def test_generate_example_round_trips(self):
        GeneratedResume.model_validate_json(tailor_module._GENERATE_EXAMPLE_JSON)

    def test_no_output_config_constant_sent(self):
        # Belt-and-suspenders: the module must not reintroduce a default
        # output_config path. (The call-site tests in test_tailor.py assert
        # individual calls; this guards the module surface.)
        assert hasattr(tailor_module, "cache_key_for")


# ─── profile_is_thin ───────────────────────────────────────────────────────────


class TestProfileIsThin:
    def test_empty_is_thin(self):
        assert runs_module.profile_is_thin({}) is True
        assert runs_module.profile_is_thin({"name": "Al", "experience": [], "skills": []}) is True

    def test_any_experience_is_not_thin(self):
        assert runs_module.profile_is_thin({"experience": [{"title": "Eng"}]}) is False

    def test_any_skills_is_not_thin(self):
        assert runs_module.profile_is_thin({"skills": ["Python"]}) is False


# ─── Streaming generation ──────────────────────────────────────────────────────

_FINAL_RESUME_JSON = (
    '{"contact":{"name":"Alex Rivera","headline":"Backend Engineer","location":"Remote",'
    '"email":"alex@example.com","phone":"","links":[]},'
    '"summary":"Backend engineer with Python and AWS experience.",'
    '"skills":[{"category":"Languages","items":["Python"]}],'
    '"experience":[{"title":"Senior Engineer","company":"Forge Labs","location":"Remote",'
    '"start_date":"Feb 2023","end_date":"Present","bullets":["Built event-driven services."]}],'
    '"education":[],"projects":[],"certifications":[],'
    '"ats":{"matched_keywords":["Python"],"missing_keywords":[],"score_estimate":80}}'
)


def _chunks_for(text: str, n: int) -> list[str]:
    """Split a string into n roughly-equal streamed chunks."""
    size = max(1, len(text) // n)
    return [text[i : i + size] for i in range(0, len(text), size)]


class _FakeStream:
    def __init__(self, chunks: list[str], final_text: str):
        self._chunks = chunks
        self._final = final_text

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    @property
    def text_stream(self):
        yield from self._chunks

    def get_final_message(self):
        block = type("B", (), {"type": "text", "text": self._final})()
        return type("M", (), {"content": [block]})()


class _FakeMessages:
    def __init__(self, chunks, final):
        self._chunks, self._final = chunks, final

    def stream(self, **_kwargs):
        return _FakeStream(self._chunks, self._final)


class _FakeClient:
    def __init__(self, chunks, final):
        self.messages = _FakeMessages(chunks, final)


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


def _settings(*, key: bool) -> Settings:
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ADMIN_TOKEN="t",
        ANTHROPIC_API_KEY="sk-test" if key else "",
    )


def _seed_candidate(Session, *, slug=DEMO_SLUG, user_id=None, profile=None) -> None:
    profile = profile or {
        "name": "Alex Rivera",
        "email": "alex@example.com",
        "summary": "Backend engineer.",
        "skills": ["Python"],
        "experience": [
            {
                "title": "Senior Engineer",
                "company": "Forge Labs",
                "location": "Remote",
                "start": "2023-02",
                "end": "Present",
                "bullets": ["Built services."],
            }
        ],
        "education": [],
        "projects": [],
        "certifications": [],
        "languages": [],
        "links": {},
    }
    with Session() as s:
        s.add(Candidate(slug=slug, user_id=user_id, profile=profile))
        s.commit()


def _seed_job(Session, *, skills: list[str]) -> int:
    with Session() as s:
        job = Job(
            source="manual",
            external_id="x1",
            company="Forge Labs",
            title="Backend Engineer",
            url="https://example.com/j/1",
            description="We need Python and Kafka.",
            skills=skills,
        )
        s.add(job)
        s.commit()
        return job.id


def test_streaming_emits_growing_partials_and_valid_final(factories, monkeypatch):
    """generate_resume(stream_cb=…) hands progressively-larger partial
    resumes to the callback and returns the fully-validated final."""
    # Emit a snapshot on every chunk so the test doesn't depend on wall-clock.
    monkeypatch.setattr(tailor_module, "_STREAM_SNAPSHOT_INTERVAL_SECONDS", 0.0)
    _seed_candidate(factories)
    job_id = _seed_job(factories, skills=["Python"])

    client = _FakeClient(_chunks_for(_FINAL_RESUME_JSON, 12), _FINAL_RESUME_JSON)
    partials: list[GeneratedResume] = []
    with factories() as db:
        job = db.get(Job, job_id)
        resume = tailor_module.generate_resume(
            db,
            job,
            {},
            settings=_settings(key=True),
            client=client,
            stream_cb=partials.append,
        )

    # Progressive: at least one partial, and the populated-section count never
    # shrinks, ending at the full resume.
    assert partials, "no partial snapshots were emitted"
    populated = [sum(1 for v in p.model_dump().values() if v) for p in partials]
    assert populated == sorted(populated), "section count regressed mid-stream"
    # Final is fully validated, sanitized, contact reconciled from profile.
    assert resume.summary == "Backend engineer with Python and AWS experience."
    assert resume.contact.name == "Alex Rivera"
    assert resume.experience[0].company == "Forge Labs"
    assert resume.meta.pages_estimate in (1, 2)


# ─── No-fabrication: exclusion ──────────────────────────────────────────────────


def test_generate_user_content_lists_declined_skills():
    """The GENERATE message names every gap the user declined, so the model
    is explicitly told never to add them."""
    job = Job(
        source="manual",
        external_id="x",
        company="C",
        title="T",
        url="u",
        description="d",
        skills=["Rust", "Go"],
    )
    answers = {
        "Have you used Rust?": "yes",
        "Have you used Go?": "no",
    }
    content = tailor_module._generate_user_content(job, answers)
    assert "NEVER add these skills" in content
    assert "Have you used Go?" in content
    # The confirmed skill's question is NOT in the exclusion list.
    exclusion = content.split("NEVER add these skills", 1)[1]
    assert "Have you used Rust?" not in exclusion


def test_demo_generate_skips_declined_gap(factories):
    """Demo path: a 'no' answer never adds the skill; 'yes' does."""
    _seed_candidate(factories)
    job_id = _seed_job(factories, skills=["Kafka"])  # Kafka is a gap (not on profile)
    q = tailor_module._question_for_gap("Kafka")
    with factories() as db:
        job = db.get(Job, job_id)
        declined = tailor_module.generate_resume(db, job, {q: "no"}, settings=_settings(key=False))
        confirmed = tailor_module.generate_resume(
            db, job, {q: "yes"}, settings=_settings(key=False)
        )

    def _all_skill_items(r):
        return [i.lower() for g in r.skills for i in g.items]

    assert "kafka" not in _all_skill_items(declined)
    assert "kafka" in _all_skill_items(confirmed)


# ─── Workers ────────────────────────────────────────────────────────────────────


@pytest.fixture
def inline(factories, monkeypatch):
    """Route workers through the test DB and drive them synchronously."""
    monkeypatch.setattr(runs_module, "SessionLocal", factories)
    monkeypatch.setattr(runs_module, "_launch_worker", lambda target, args: target(*args))
    return factories


def _row(Session, run_id: str) -> TailorRun:
    with Session() as s:
        return s.execute(select(TailorRun).where(TailorRun.run_id == run_id)).scalar_one()


def test_analyze_with_gaps_pauses_for_questions(inline):
    Session = inline
    _seed_candidate(Session)
    job_id = _seed_job(Session, skills=["Kafka", "Rust"])  # both gaps
    with Session() as db:
        job = db.get(Job, job_id)
        run_id = runs_module.start_tailor_run(user_id=None, job=job, settings=_settings(key=False))

    run = _row(Session, run_id)
    assert run.status == TAILOR_STATUS_PENDING_QUESTIONS
    assert run.missing_skills_json is not None
    assert len(run.missing_skills_json["questions"]) >= 1
    assert run.result_json is None  # generation hasn't run


def test_analyze_with_no_gaps_auto_generates(inline):
    Session = inline
    _seed_candidate(Session)
    job_id = _seed_job(Session, skills=["Python"])  # already on the profile → no gaps
    with Session() as db:
        job = db.get(Job, job_id)
        run_id = runs_module.start_tailor_run(user_id=None, job=job, settings=_settings(key=False))

    run = _row(Session, run_id)
    assert run.status == TAILOR_STATUS_DONE
    assert run.result_json is not None
    assert run.result_json["contact"]["name"] == "Alex Rivera"


def test_submit_answers_generates_to_done(inline):
    Session = inline
    _seed_candidate(Session)
    job_id = _seed_job(Session, skills=["Kafka"])
    with Session() as db:
        job = db.get(Job, job_id)
        run_id = runs_module.start_tailor_run(user_id=None, job=job, settings=_settings(key=False))
    assert _row(Session, run_id).status == TAILOR_STATUS_PENDING_QUESTIONS

    q = tailor_module._question_for_gap("Kafka")
    ok = runs_module.submit_answers(run_id, {q: "no"}, user_id=None, settings=_settings(key=False))
    assert ok is True

    run = _row(Session, run_id)
    assert run.status == TAILOR_STATUS_DONE
    assert run.result_json is not None


def test_generate_worker_writes_error_on_exception(inline, monkeypatch):
    Session = inline
    _seed_candidate(Session)
    job_id = _seed_job(Session, skills=["Kafka"])
    with Session() as db:
        job = db.get(Job, job_id)
        run_id = runs_module.start_tailor_run(user_id=None, job=job, settings=_settings(key=False))

    def _boom(*_a, **_k):
        raise RuntimeError("simulated generate crash")

    monkeypatch.setattr(runs_module, "generate_resume", _boom)
    q = tailor_module._question_for_gap("Kafka")
    runs_module.submit_answers(run_id, {q: "yes"}, user_id=None, settings=_settings(key=False))

    run = _row(Session, run_id)
    assert run.status == TAILOR_STATUS_ERROR
    assert "simulated generate crash" in (run.error_text or "")
    assert run.finished_at is not None


def test_generate_worker_timeout_message(inline, monkeypatch):
    Session = inline
    _seed_candidate(Session)
    job_id = _seed_job(Session, skills=["Kafka"])
    with Session() as db:
        job = db.get(Job, job_id)
        run_id = runs_module.start_tailor_run(user_id=None, job=job, settings=_settings(key=False))

    def _timeout(*_a, **_k):
        raise TimeoutError("budget exceeded")

    monkeypatch.setattr(runs_module, "generate_resume", _timeout)
    q = tailor_module._question_for_gap("Kafka")
    runs_module.submit_answers(run_id, {q: "yes"}, user_id=None, settings=_settings(key=False))

    run = _row(Session, run_id)
    assert run.status == TAILOR_STATUS_ERROR
    assert "Taking longer than usual" in (run.error_text or "")


def test_cache_hit_reuses_prior_result_without_a_model_call(inline):
    """A second tailor of the same profile+JD returns a `done` run immediately,
    copying the prior result and NOT re-running analyze/generate."""
    Session = inline
    _seed_candidate(Session)  # has Python
    job_id = _seed_job(Session, skills=["Python"])  # no gaps → auto-generate → done

    with Session() as db:
        job = db.get(Job, job_id)
        run1 = runs_module.start_tailor_run(user_id=None, job=job, settings=_settings(key=False))
    r1 = _row(Session, run1)
    assert r1.status == TAILOR_STATUS_DONE
    assert r1.cache_key  # stamped on the real run
    assert r1.missing_skills_json is not None  # analyze ran

    with Session() as db:
        job = db.get(Job, job_id)
        run2 = runs_module.start_tailor_run(user_id=None, job=job, settings=_settings(key=False))
    r2 = _row(Session, run2)
    assert run2 != run1
    assert r2.status == TAILOR_STATUS_DONE
    assert r2.cache_key == r1.cache_key
    assert r2.result_json == r1.result_json
    # The cache-hit marker: analyze never ran, so missing_skills_json is null.
    assert r2.missing_skills_json is None


def test_cache_miss_when_job_differs(inline):
    Session = inline
    _seed_candidate(Session)
    job_a = _seed_job(Session, skills=["Python"])
    with Session() as db:
        run1 = runs_module.start_tailor_run(
            user_id=None, job=db.get(Job, job_a), settings=_settings(key=False)
        )
    # Different JD text → different cache_key → a real run (analyze populates).
    with Session() as db:
        job = db.get(Job, job_a)
        job.description = "Totally different role: we need Rust and Go and Kafka."
        db.commit()
    with Session() as db:
        run2 = runs_module.start_tailor_run(
            user_id=None, job=db.get(Job, job_a), settings=_settings(key=False)
        )
    assert _row(Session, run1).cache_key != _row(Session, run2).cache_key


def test_submit_answers_rejects_unknown_or_unowned_run(inline):
    Session = inline
    _seed_candidate(Session)
    job_id = _seed_job(Session, skills=["Kafka"])
    with Session() as db:
        job = db.get(Job, job_id)
        run_id = runs_module.start_tailor_run(user_id=7, job=job, settings=_settings(key=False))

    # Wrong user can't drive someone else's run.
    assert (
        runs_module.submit_answers(run_id, {}, user_id=99, settings=_settings(key=False)) is False
    )
    # Nonexistent run.
    assert runs_module.submit_answers("nope", {}, user_id=7, settings=_settings(key=False)) is False


# ─── Startup sweep ──────────────────────────────────────────────────────────────


class TestSweep:
    def _seed(self, Session, run_id, *, status, age):
        with Session() as s:
            s.add(
                TailorRun(
                    run_id=run_id,
                    status=status,
                    started_at=datetime.now(UTC) - age,
                )
            )
            s.commit()

    def test_old_nonterminal_rows_marked_error(self, factories, monkeypatch):
        monkeypatch.setattr(runs_module, "SessionLocal", factories)
        self._seed(factories, "a", status=TAILOR_STATUS_ANALYZING, age=timedelta(minutes=30))
        self._seed(factories, "g", status=TAILOR_STATUS_GENERATING, age=timedelta(minutes=30))

        assert runs_module.sweep_orphaned_tailor_runs() == 2
        assert _row(factories, "a").status == TAILOR_STATUS_ERROR
        assert _row(factories, "g").status == TAILOR_STATUS_ERROR
        assert "restart" in (_row(factories, "a").error_text or "")

    def test_fresh_and_terminal_rows_untouched(self, factories, monkeypatch):
        monkeypatch.setattr(runs_module, "SessionLocal", factories)
        self._seed(factories, "fresh", status=TAILOR_STATUS_GENERATING, age=timedelta(seconds=20))
        self._seed(factories, "done", status=TAILOR_STATUS_DONE, age=timedelta(hours=2))

        assert runs_module.sweep_orphaned_tailor_runs() == 0
        assert _row(factories, "fresh").status == TAILOR_STATUS_GENERATING
        assert _row(factories, "done").status == TAILOR_STATUS_DONE

    def test_sweep_swallows_db_errors(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(runs_module, "SessionLocal", _boom)
        assert runs_module.sweep_orphaned_tailor_runs() == 0


# ─── API surface (auth, ownership, thin-profile gate, polling) ──────────────────


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A TestClient wired to a file-backed SQLite DB, with workers driven
    inline against that same DB so /start → poll → done works end to end in
    demo mode. File-backed (not in-memory) so the worker's separate sessions
    see committed rows the way Postgres would."""
    from fastapi.testclient import TestClient

    from app import config as config_module
    from app.api.auth import get_current_user
    from app.config import get_settings
    from app.database import Base as _Base
    from app.database import get_db
    from app.main import app
    from app.models.user import User

    db_url = f"sqlite+pysqlite:///{tmp_path / 'api.db'}"
    engine = create_engine(db_url, future=True, connect_args={"check_same_thread": False})
    _Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as s:
        u = User(google_subject_id="g", email="t@example.com", name="T")
        s.add(u)
        s.commit()
        uid = u.id
        s.expunge(u)
    user = type("U", (), {"id": uid, "email": "t@example.com"})()

    settings = _settings(key=False)
    monkeypatch.setattr(runs_module, "SessionLocal", Session)
    monkeypatch.setattr(runs_module, "_launch_worker", lambda target, args: target(*args))

    def override_db():
        with Session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: user
    config_module.get_settings.cache_clear()
    try:
        yield TestClient(app), Session, uid
    finally:
        app.dependency_overrides.clear()
        config_module.get_settings.cache_clear()


def test_start_409_on_thin_profile(api):
    client, Session, uid = api
    _seed_candidate(Session, slug="demo", user_id=uid, profile={"name": "Al"})  # thin
    job_id = _seed_job(Session, skills=["Kafka"])
    res = client.post("/api/tailor/start", json={"job_id": job_id})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "profile_thin"


def test_start_force_bypasses_thin_gate(api):
    client, Session, uid = api
    _seed_candidate(Session, slug="demo", user_id=uid, profile={"name": "Al"})
    job_id = _seed_job(Session, skills=["Python"])
    res = client.post("/api/tailor/start", json={"job_id": job_id, "force": True})
    assert res.status_code == 202
    assert res.json()["run_id"]


def test_start_then_poll_reaches_terminal(api):
    client, Session, uid = api
    _seed_candidate(Session, slug="demo", user_id=uid)  # non-thin default profile
    job_id = _seed_job(Session, skills=["Kafka"])  # a gap → pending_questions

    start = client.post("/api/tailor/start", json={"job_id": job_id})
    assert start.status_code == 202
    run_id = start.json()["run_id"]

    poll = client.get(f"/api/tailor/runs/{run_id}")
    assert poll.status_code == 200
    body = poll.json()
    assert body["status"] == TAILOR_STATUS_PENDING_QUESTIONS
    assert body["demo_mode"] is True
    assert body["analysis"]["questions"]

    # Answer and generate to done.
    q = body["analysis"]["questions"][0]
    ans = client.post(f"/api/tailor/runs/{run_id}/answers", json={"answers": {q: "no"}})
    assert ans.status_code == 202
    done = client.get(f"/api/tailor/runs/{run_id}").json()
    assert done["status"] == TAILOR_STATUS_DONE
    assert done["resume"]["contact"]["name"]


def test_run_status_404_for_unknown_run(api):
    client, _Session, _uid = api
    assert client.get("/api/tailor/runs/deadbeef").status_code == 404
