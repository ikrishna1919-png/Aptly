"""Background orchestration for /ats runs (reuses the `tailor_runs` table).

Two worker flavours:
  * generate  — jd_paste / upload_pdf_fallback: stream a `TailoredResume`
    (ats.generate_ats), persisting partial snapshots, then `done`.
  * docx_inject — upload_docx: compute minimal keyword swaps and store the
    diff; the edited file is produced at download time from the original blob
    + the user's accepted edits.

Same terminal-status contract as the tailor workers (try/except/finally).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models.candidate import Candidate
from app.models.tailor_run import (
    TAILOR_STATUS_DONE,
    TAILOR_STATUS_ERROR,
    TAILOR_STATUS_GENERATING,
    TailorRun,
)
from app.services import ats
from app.services.tailor import (
    _GENERATE_HARD_TIMEOUT_SECONDS,
    GeneratedResume,
    ResumeMeta,
    TailoredResume,
)

log = logging.getLogger(__name__)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _launch(target: Any, args: tuple) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _finish(run_id: str, *, status: str, result_json=None, error_text=None) -> None:
    with SessionLocal() as db:
        run = db.execute(select(TailorRun).where(TailorRun.run_id == run_id)).scalar_one_or_none()
        if run is None:
            return
        run.status = status
        if result_json is not None:
            run.result_json = result_json
        if error_text is not None:
            run.error_text = error_text
        if status in (TAILOR_STATUS_DONE, TAILOR_STATUS_ERROR):
            run.finished_at = datetime.now(UTC)
        db.commit()


# ─── Upload row (Option 2 DOCX) ──────────────────────────────────────────────


def create_docx_upload(*, user_id: int | None, filename: str, docx_blob: bytes) -> str:
    """Persist an uploaded DOCX as a tailor_runs row (status=generating is set
    only once /generate fires; here it parks as 'analyzing'). Returns run_id."""
    run_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(
            TailorRun(
                run_id=run_id,
                user_id=user_id,
                option_type="upload_docx",
                uploaded_filename=filename,
                uploaded_docx_blob=docx_blob,
                status="analyzing",
            )
        )
        db.commit()
    return run_id


# ─── Entry points ─────────────────────────────────────────────────────────────


def start_docx_inject_from_active_resume(
    *,
    user_id: int | None,
    jd_text: str,
    customization: dict[str, Any] | None,
    settings: Settings | None = None,
) -> str | None:
    """Run the EXISTING in-place keyword-inject path against the user's SAVED
    active_resume DOCX — the "match my resume format" (source 'b') route, shared
    by /ats/generate AND the Jobs tailor flow. Seeds a fresh upload_docx run
    from the saved blob, then flips it into docx_inject. Returns the run_id, or
    None when no DOCX is saved (caller surfaces 'upload a resume first'). NO
    from-scratch generation; formatting/sections/colours are untouched."""
    settings = settings or get_settings()
    with SessionLocal() as db:
        cand = db.execute(
            select(Candidate).where(Candidate.user_id == user_id)
        ).scalar_one_or_none()
        blob = cand.active_resume_blob if cand else None
        ctype = cand.active_resume_content_type if cand else None
        fname = (cand.active_resume_filename if cand else None) or "resume.docx"
    if not blob or ctype != _DOCX_MIME:
        return None
    run_id = create_docx_upload(user_id=user_id, filename=fname, docx_blob=blob)
    ok = start_docx_inject(
        run_id=run_id,
        user_id=user_id,
        jd_text=jd_text,
        customization=customization,
        settings=settings,
    )
    return run_id if ok else None


def start_generate(
    *,
    user_id: int | None,
    option_type: str,
    jd_text: str,
    customization: dict[str, Any] | None,
    fmt: str,
    custom_options: dict[str, Any] | None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    run_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(
            TailorRun(
                run_id=run_id,
                user_id=user_id,
                option_type=option_type,
                jd_text=jd_text[:8000],
                questions_answers_json=customization,
                format_selection=fmt,
                custom_options_json=custom_options,
                status=TAILOR_STATUS_GENERATING,
            )
        )
        db.commit()
    _launch(_execute_generate, (run_id, settings))
    return run_id


def start_docx_inject(
    *,
    run_id: str,
    user_id: int | None,
    jd_text: str,
    customization: dict[str, Any] | None,
    settings: Settings | None = None,
) -> bool:
    """Flip an existing upload row into the keyword-injection flow."""
    settings = settings or get_settings()
    with SessionLocal() as db:
        run = db.execute(
            select(TailorRun).where(TailorRun.run_id == run_id, TailorRun.user_id == user_id)
        ).scalar_one_or_none()
        if run is None or run.option_type != "upload_docx" or run.uploaded_docx_blob is None:
            return False
        run.jd_text = jd_text[:8000]
        run.questions_answers_json = customization
        run.status = TAILOR_STATUS_GENERATING
        run.error_text = None
        db.commit()
    _launch(_execute_docx_inject, (run_id, settings))
    return True


# ─── Workers ──────────────────────────────────────────────────────────────────


def _execute_generate(run_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    tag = f"ats_run={run_id}"
    deadline = time.monotonic() + _GENERATE_HARD_TIMEOUT_SECONDS
    written = False
    try:
        with SessionLocal() as db:
            run = db.execute(
                select(TailorRun).where(TailorRun.run_id == run_id)
            ).scalar_one_or_none()
            if run is None:
                return
            user_id, jd, custom = run.user_id, run.jd_text or "", run.questions_answers_json

            def _on_partial(gen: GeneratedResume) -> None:
                _write_partial(run_id, gen)

            resume = ats.generate_ats(
                db,
                user_id=user_id,
                jd_text=jd,
                customization=custom,
                settings=settings,
                stream_cb=_on_partial,
                deadline=deadline,
            )
        _finish(run_id, status=TAILOR_STATUS_DONE, result_json=resume.model_dump(mode="json"))
        written = True
    except TimeoutError:
        _finish(
            run_id,
            status=TAILOR_STATUS_ERROR,
            error_text="Taking longer than usual — try again, or shorten the job description.",
        )
        written = True
    except Exception as e:  # noqa: BLE001
        log.exception("%s: generate failed", tag)
        _finish(run_id, status=TAILOR_STATUS_ERROR, error_text=f"Generation failed — {e}")
        written = True
    finally:
        if not written:
            _finish(
                run_id, status=TAILOR_STATUS_ERROR, error_text="Worker exited without a result."
            )


def _execute_docx_inject(run_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    tag = f"ats_run={run_id}"
    written = False
    try:
        with SessionLocal() as db:
            run = db.execute(
                select(TailorRun).where(TailorRun.run_id == run_id)
            ).scalar_one_or_none()
            if run is None or run.uploaded_docx_blob is None:
                _finish(run_id, status=TAILOR_STATUS_ERROR, error_text="Upload not found.")
                return
            blob, jd = run.uploaded_docx_blob, run.jd_text or ""
            answers = run.questions_answers_json
            resume_text = ats.extract_docx_text(blob)
        try:
            edits = ats.compute_keyword_edits(resume_text, jd, answers=answers, settings=settings)
        except ats.KeywordInjectionError as e:
            # Clean, user-facing message (the LLM returned unparseable JSON
            # twice) — never a raw traceback.
            _finish(run_id, status=TAILOR_STATUS_ERROR, error_text=str(e))
            written = True
            return
        # Validate which edits actually land in a single run (the rest are
        # reported as skipped so the diff is honest).
        _, applied, skipped = ats.apply_docx_edits(blob, edits)
        _finish(
            run_id,
            status=TAILOR_STATUS_DONE,
            result_json={
                "kind": "docx_keyword_inject",
                "applied": applied,
                "skipped": skipped,
            },
        )
        written = True
    except Exception as e:  # noqa: BLE001
        log.exception("%s: docx inject failed", tag)
        _finish(run_id, status=TAILOR_STATUS_ERROR, error_text=f"Keyword injection failed — {e}")
        written = True
    finally:
        if not written:
            _finish(
                run_id, status=TAILOR_STATUS_ERROR, error_text="Worker exited without a result."
            )


def _write_partial(run_id: str, gen: GeneratedResume) -> None:
    snapshot = TailoredResume(**gen.model_dump(), meta=ResumeMeta(mode="visual"))
    with SessionLocal() as db:
        run = db.execute(select(TailorRun).where(TailorRun.run_id == run_id)).scalar_one_or_none()
        if run is None or run.status != TAILOR_STATUS_GENERATING:
            return
        run.result_json = snapshot.model_dump(mode="json")
        db.commit()
