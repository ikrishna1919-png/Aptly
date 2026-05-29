"""Background orchestration for the run-based resume-tailoring flow.

This is the tailoring twin of `profile_parser`'s run management: it owns the
`tailor_runs` row lifecycle and the daemon-thread workers, while the actual
prompts / schema / rendering stay in `tailor.py` (the PR #58 contract) and
are reused untouched.

Lifecycle (mirrors the PRD):

    POST /api/tailor/start
        → row(status=analyzing) + spawn ANALYZE worker → 202 {run_id}
    ANALYZE worker:
        analyze_job() → write missing_skills_json
        questions?  yes → status=pending_questions (wait for answers)
                    no  → auto-skip straight into the GENERATE worker
    POST /api/tailor/runs/{run_id}/answers
        → store answers, status=generating + spawn GENERATE worker → 202
    GENERATE worker (streaming):
        generate_resume(stream_cb=…) → throttled partial snapshots into
        result_json → final validated resume → status=done

Worker contract — identical to the parser's: every code path MUST write a
terminal status (`done`/`error`) before returning, guarded by
try/except/finally, so a crash can never strand a row at
`analyzing`/`generating`. The startup sweep reaps anything a *process*
death left behind.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models.job import Job
from app.models.tailor_run import (
    TAILOR_STATUS_ANALYZING,
    TAILOR_STATUS_DONE,
    TAILOR_STATUS_ERROR,
    TAILOR_STATUS_GENERATING,
    TAILOR_STATUS_PENDING_QUESTIONS,
    TailorRun,
)
from app.services.demo_candidate import get_candidate
from app.services.tailor import (
    _GENERATE_HARD_TIMEOUT_SECONDS,
    Analysis,
    GeneratedResume,
    ResumeMeta,
    TailoredResume,
    analyze_job,
    cache_key_for,
    generate_resume,
)
from app.sources._text import strip_html

log = logging.getLogger(__name__)

# A tailor run older than this still sitting at a non-terminal status was
# orphaned by a process death (the worker's finally can't run if the process
# is killed). The startup sweep reaps these. Generation is hard-capped at 90s,
# so 10 minutes is comfortably past any legitimate in-flight run.
_ORPHAN_AFTER = timedelta(minutes=10)

# How long a cached tailor result stays fresh. A repeat of the same JD with
# the same profile inside this window is served from the prior run — no model
# call — so it returns in well under a second.
_CACHE_TTL = timedelta(days=7)


def _launch_worker(target: Any, args: tuple) -> None:
    """Indirection so tests can monkey-patch to run the worker inline.

    Production: daemon thread. Tests: replace with `lambda t, a: t(*a)` to
    drive the worker synchronously and assert the terminal row state."""
    threading.Thread(target=target, args=args, daemon=True).start()


def profile_is_thin(candidate: dict[str, Any]) -> bool:
    """A profile is "thin" when tailoring has almost nothing real to work
    from: no experience, no education, no projects, and no skills. We gate
    on this BEFORE spending an Anthropic call so the user gets an honest
    "fill in your profile first" nudge instead of a hollow resume."""
    has_experience = bool(candidate.get("experience"))
    has_education = bool(candidate.get("education"))
    has_projects = bool(candidate.get("projects"))
    skills = candidate.get("skills") or []
    has_skills = bool(skills)
    return not (has_experience or has_education or has_projects or has_skills)


# ─── Row lifecycle helpers ────────────────────────────────────────────────────


def _finish(
    run_id: str,
    *,
    status: str,
    error_text: str | None = None,
    result_json: dict[str, Any] | None = None,
    missing_skills_json: dict[str, Any] | None = None,
    user_answers_json: dict[str, Any] | None = None,
) -> None:
    """Write a status transition onto the TailorRun row. Only fields passed
    explicitly are touched; `finished_at` is stamped on terminal states."""
    with SessionLocal() as db:
        run = db.execute(select(TailorRun).where(TailorRun.run_id == run_id)).scalar_one_or_none()
        if run is None:
            log.warning("tailor_run=%s: row not found at finish — was it deleted?", run_id)
            return
        run.status = status
        if error_text is not None:
            run.error_text = error_text
        if result_json is not None:
            run.result_json = result_json
        if missing_skills_json is not None:
            run.missing_skills_json = missing_skills_json
        if user_answers_json is not None:
            run.user_answers_json = user_answers_json
        if status in (TAILOR_STATUS_DONE, TAILOR_STATUS_ERROR):
            run.finished_at = datetime.now(UTC)
        db.commit()


def _write_partial(run_id: str, gen: GeneratedResume) -> None:
    """Persist a best-effort partial resume snapshot while generating, so the
    poller can reveal sections as they stream in. Wrapped as a full
    `TailoredResume` (with default meta) so `result_json` always validates as
    one shape, partial or final."""
    snapshot = TailoredResume(**gen.model_dump(), meta=ResumeMeta(mode="visual"))
    with SessionLocal() as db:
        run = db.execute(select(TailorRun).where(TailorRun.run_id == run_id)).scalar_one_or_none()
        # Only overwrite while still generating — never clobber a terminal row
        # (a slow snapshot could otherwise land after done/error).
        if run is None or run.status != TAILOR_STATUS_GENERATING:
            return
        run.result_json = snapshot.model_dump(mode="json")
        db.commit()


# ─── Public entry points (called by the API layer) ────────────────────────────


def start_tailor_run(
    *,
    user_id: int | None,
    job: Job,
    settings: Settings | None = None,
) -> str:
    """Create a TailorRun row (status=analyzing) and spawn the analyze worker.
    Returns the run_id. Fast — no Anthropic call on this path."""
    settings = settings or get_settings()
    run_id = uuid.uuid4().hex
    jd_text = strip_html(job.description or "")[:6000] or None
    t0 = time.perf_counter()
    cached_result: dict[str, Any] | None = None
    with SessionLocal() as db:
        candidate = get_candidate(db, user_id=user_id)
        cache_key = cache_key_for(candidate, job)
        # 7-day per-user result cache: identical profile + JD + prompt version
        # reuses a prior `done` run's resume with no Anthropic call.
        cutoff = datetime.now(UTC) - _CACHE_TTL
        prior = (
            db.execute(
                select(TailorRun)
                .where(
                    TailorRun.user_id == user_id,
                    TailorRun.cache_key == cache_key,
                    TailorRun.status == TAILOR_STATUS_DONE,
                    TailorRun.result_json.isnot(None),
                    TailorRun.started_at >= cutoff,
                )
                .order_by(TailorRun.started_at.desc())
            )
            .scalars()
            .first()
        )
        cached_result = prior.result_json if prior is not None else None
        db.add(
            TailorRun(
                run_id=run_id,
                user_id=user_id,
                job_id=job.id,
                jd_text=jd_text,
                cache_key=cache_key,
                status=TAILOR_STATUS_DONE if cached_result is not None else TAILOR_STATUS_ANALYZING,
                result_json=cached_result,
                finished_at=datetime.now(UTC) if cached_result is not None else None,
            )
        )
        db.commit()
    if cached_result is not None:
        log.info(
            "tailor_run=%s: tailor.cache_hit %.0fms — returning prior result, no model call",
            run_id,
            (time.perf_counter() - t0) * 1000,
        )
        return run_id
    log.info(
        "tailor_run=%s: tailor.db_write (create row) %.0fms — spawning analyze worker",
        run_id,
        (time.perf_counter() - t0) * 1000,
    )
    _launch_worker(_execute_analyze, (run_id, settings))
    return run_id


def submit_answers(
    run_id: str,
    answers: dict[str, str],
    *,
    user_id: int | None,
    settings: Settings | None = None,
) -> bool:
    """Store the user's gap-question answers and spawn the generate worker.

    Returns False when the run doesn't exist / isn't owned by `user_id`, or
    isn't in a state that accepts answers (must be `pending_questions`, or
    `error` for a retry). True when the generate worker was launched."""
    settings = settings or get_settings()
    with SessionLocal() as db:
        run = db.execute(
            select(TailorRun).where(TailorRun.run_id == run_id, TailorRun.user_id == user_id)
        ).scalar_one_or_none()
        if run is None:
            return False
        if run.status not in (TAILOR_STATUS_PENDING_QUESTIONS, TAILOR_STATUS_ERROR):
            return False
        run.user_answers_json = answers
        run.status = TAILOR_STATUS_GENERATING
        run.error_text = None
        run.finished_at = None
        db.commit()
    _launch_worker(_execute_generate, (run_id, settings))
    return True


# ─── Workers ──────────────────────────────────────────────────────────────────


def _execute_analyze(run_id: str, settings: Settings | None = None) -> None:
    """Analyze worker: run the JD-vs-profile analysis, persist the questions,
    and either pause for answers or auto-skip straight into generation."""
    settings = settings or get_settings()
    tag = f"tailor_run={run_id}"
    t_start = time.perf_counter()
    log.info("%s: tailor.received step=analyze t=0", tag)
    terminal_or_handed_off = False
    try:
        with SessionLocal() as db:
            run = db.execute(
                select(TailorRun).where(TailorRun.run_id == run_id)
            ).scalar_one_or_none()
            if run is None:
                log.warning("%s: row vanished before analyze", tag)
                return
            user_id = run.user_id
            job = db.get(Job, run.job_id) if run.job_id is not None else None
            if job is None:
                _finish(run_id, status=TAILOR_STATUS_ERROR, error_text="Job not found.")
                terminal_or_handed_off = True
                return
            t_an = time.perf_counter()
            analysis = analyze_job(db, job, user_id=user_id, settings=settings)
            log.info(
                "%s: tailor.analyze_done %.0fms (worker_elapsed=%.0fms)",
                tag,
                (time.perf_counter() - t_an) * 1000,
                (time.perf_counter() - t_start) * 1000,
            )

        missing = analysis.model_dump(mode="json")
        if analysis.questions:
            _finish(
                run_id,
                status=TAILOR_STATUS_PENDING_QUESTIONS,
                missing_skills_json=missing,
            )
            terminal_or_handed_off = True
            log.info("%s: %d gap question(s) — awaiting answers", tag, len(analysis.questions))
        else:
            # Nothing meaningful to ask — skip the questions stage and go
            # straight to generation in this same worker thread.
            _finish(
                run_id,
                status=TAILOR_STATUS_GENERATING,
                missing_skills_json=missing,
                user_answers_json={},
            )
            terminal_or_handed_off = True
            log.info("%s: no gap questions — auto-generating", tag)
            _execute_generate(run_id, settings)
    except Exception as e:  # noqa: BLE001
        log.exception("%s: analyze worker failed", tag)
        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        try:
            _finish(run_id, status=TAILOR_STATUS_ERROR, error_text=f"Analysis failed — {msg}")
            terminal_or_handed_off = True
        except Exception:  # noqa: BLE001
            log.exception("%s: failed to record analyze error", tag)
    finally:
        if not terminal_or_handed_off:
            try:
                _finish(
                    run_id,
                    status=TAILOR_STATUS_ERROR,
                    error_text="Analysis worker exited without a result. See backend logs.",
                )
            except Exception:  # noqa: BLE001
                log.exception("%s: defensive analyze-error write failed", tag)


def _execute_generate(run_id: str, settings: Settings | None = None) -> None:
    """Generate worker: stream the tailored resume, persisting partial
    snapshots, and write the final validated resume. Always lands on a
    terminal status."""
    settings = settings or get_settings()
    tag = f"tailor_run={run_id}"
    t_start = time.perf_counter()
    log.info("%s: tailor.received step=generate t=0", tag)
    deadline = time.monotonic() + _GENERATE_HARD_TIMEOUT_SECONDS
    terminal_written = False
    try:
        with SessionLocal() as db:
            run = db.execute(
                select(TailorRun).where(TailorRun.run_id == run_id)
            ).scalar_one_or_none()
            if run is None:
                log.warning("%s: row vanished before generate", tag)
                return
            user_id = run.user_id
            answers = dict(run.user_answers_json or {})
            job = db.get(Job, run.job_id) if run.job_id is not None else None
            if job is None:
                _finish(run_id, status=TAILOR_STATUS_ERROR, error_text="Job not found.")
                terminal_written = True
                return

            def _on_partial(gen: GeneratedResume) -> None:
                _write_partial(run_id, gen)

            t_gen = time.perf_counter()
            resume = generate_resume(
                db,
                job,
                answers,
                user_id=user_id,
                settings=settings,
                stream_cb=_on_partial,
                deadline=deadline,
            )
            log.info(
                "%s: tailor.generate_done %.0fms (worker_elapsed=%.0fms)",
                tag,
                (time.perf_counter() - t_gen) * 1000,
                (time.perf_counter() - t_start) * 1000,
            )
        t_w = time.perf_counter()
        _finish(
            run_id,
            status=TAILOR_STATUS_DONE,
            result_json=resume.model_dump(mode="json"),
        )
        terminal_written = True
        log.info(
            "%s: tailor.db_write_end status=done %.0fms (worker_total=%.0fms)",
            tag,
            (time.perf_counter() - t_w) * 1000,
            (time.perf_counter() - t_start) * 1000,
        )
    except TimeoutError:
        log.warning("%s: generation exceeded the %.0fs cap", tag, _GENERATE_HARD_TIMEOUT_SECONDS)
        try:
            _finish(
                run_id,
                status=TAILOR_STATUS_ERROR,
                error_text=(
                    "Taking longer than usual — try again, or generate with a shorter "
                    "job description."
                ),
            )
            terminal_written = True
        except Exception:  # noqa: BLE001
            log.exception("%s: failed to record timeout error", tag)
    except Exception as e:  # noqa: BLE001
        log.exception("%s: generate worker failed", tag)
        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        try:
            _finish(run_id, status=TAILOR_STATUS_ERROR, error_text=f"Generation failed — {msg}")
            terminal_written = True
        except Exception:  # noqa: BLE001
            log.exception("%s: failed to record generate error", tag)
    finally:
        if not terminal_written:
            try:
                _finish(
                    run_id,
                    status=TAILOR_STATUS_ERROR,
                    error_text="Generation worker exited without a result. See backend logs.",
                )
            except Exception:  # noqa: BLE001
                log.exception("%s: defensive generate-error write failed", tag)


# ─── Startup sweep ─────────────────────────────────────────────────────────────


def sweep_orphaned_tailor_runs() -> int:
    """Mark long-stuck `analyzing`/`generating` rows as error. Returns the
    count. Best-effort — a DB hiccup here is logged and swallowed so it can't
    block app boot. Covers the gap the worker's finally can't: a process
    killed mid-run."""
    cutoff = datetime.now(UTC) - _ORPHAN_AFTER
    try:
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(TailorRun).where(
                        TailorRun.status.in_((TAILOR_STATUS_ANALYZING, TAILOR_STATUS_GENERATING)),
                        TailorRun.started_at < cutoff,
                    )
                )
                .scalars()
                .all()
            )
            for run in rows:
                run.status = TAILOR_STATUS_ERROR
                run.error_text = (
                    "Tailoring was interrupted by a server restart before it could "
                    "finish. Please try again."
                )
                run.finished_at = datetime.now(UTC)
            if rows:
                db.commit()
                log.warning("startup sweep: marked %d orphaned tailor_run(s) as error", len(rows))
            return len(rows)
    except Exception:  # noqa: BLE001
        log.exception("startup sweep: failed to reap orphaned tailor_runs")
        return 0


# Re-exported so the API layer can validate/serialize without reaching into
# tailor.py internals.
__all__ = [
    "Analysis",
    "TailoredResume",
    "profile_is_thin",
    "start_tailor_run",
    "submit_answers",
    "sweep_orphaned_tailor_runs",
]
