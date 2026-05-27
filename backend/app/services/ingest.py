"""Ingest orchestrator: fetch → dedupe → upsert → expire.

The rolling-window guarantee: after every run, the `jobs` table contains
exactly the postings whose `source_updated_at` falls within the last
`HOURS_WINDOW` hours. Anything older is deleted.

Sources to pull from come from the `sources` table (one row per
`(source_type, token)` pair, `enabled=True`). `companies.py` is just the
first-deploy seed; at runtime the DB is the source of truth. Each
source's row gets per-token telemetry written after every pass
(`last_run_at`, `last_status` = success/error/skipped, `last_error`,
`jobs_found_last_run`) so the operator can spot a board that's been
silently broken for days.

Per-board failures (timeout, 404, malformed JSON) are caught + logged +
counted but never abort the run — one slow board can't stall the rest.
HTTP-level timeouts live on the source adapters' `httpx.Client`
(`timeout=20.0` for all three).

Long ingests don't fit inside a typical HTTP request budget (Render's
free tier kills connections at 100s), so `POST /api/admin/ingest` no
longer awaits `run_ingest()` directly. It calls
`start_background_ingest()`, which writes an `IngestRun` row and spawns
a daemon thread to do the work, and returns the new `run_id` immediately
so the caller can poll `GET /api/admin/ingest/{run_id}` for completion.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import SessionLocal
from app.models.ingest_run import (
    INGEST_STATUS_FAILED,
    INGEST_STATUS_SUCCESS,
    IngestRun,
)
from app.models.job import Job
from app.models.source import (
    STATUS_ERROR,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    Source,
)
from app.sources import SOURCES, JobSource, NormalizedJob
from app.sources.base import SourceUnavailable

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    """What an ingest run did. Returned to the CLI / admin endpoint."""

    window_hours: int
    boards_attempted: int = 0
    boards_failed: int = 0
    boards_failures: list[dict] = field(default_factory=list)  # [{board, error}]
    boards_auto_disabled: list[str] = field(default_factory=list)  # ["greenhouse:foo", …]
    fetched: int = 0  # raw postings returned by sources
    skipped_outside_window: int = 0
    skipped_duplicates: int = 0
    inserted: int = 0
    updated: int = 0
    deleted_expired: int = 0
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _content_hash(nj: NormalizedJob) -> str:
    """Stable hash of the content that drives "did anything change?"."""
    h = hashlib.sha256()
    for field_value in (
        nj.title,
        nj.location or "",
        nj.url,
        nj.description or "",
        str(nj.remote),
        nj.employment_type or "",
        ",".join(nj.skills),
        str(nj.sponsors_visa),
    ):
        h.update(field_value.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _load_enabled_sources(db: Session) -> list[Source]:
    """Read enabled rows in a stable order so logs are diffable across runs."""
    return list(
        db.execute(
            select(Source)
            .where(Source.enabled.is_(True))
            .order_by(Source.source_type, Source.token)
        ).scalars()
    )


def run_ingest(
    db: Session,
    settings: Settings,
    sources: list[Source] | None = None,
    source_factories: dict[str, type[JobSource]] | None = None,
) -> IngestStats:
    """Run a full ingest+cleanup pass. Commits after each source so a
    crash mid-pass leaves the already-pulled rows + their telemetry on
    disk; commits at the end for the expiry pass."""

    source_rows = sources if sources is not None else _load_enabled_sources(db)
    source_factories = source_factories if source_factories is not None else SOURCES

    stats = IngestStats(window_hours=settings.hours_window, started_at=_utcnow().isoformat())
    window_start = _utcnow() - timedelta(hours=settings.hours_window)
    seen: set[tuple[str, str]] = set()

    # Cache adapter instances so we don't open one client per board.
    source_instances: dict[str, JobSource] = {}

    threshold = max(1, int(getattr(settings, "source_failure_threshold", 3)))

    try:
        for src in source_rows:
            source_name = src.source_type
            token = src.token
            cls = source_factories.get(source_name)
            if cls is None:
                msg = f"unknown source type {source_name!r}"
                log.warning("%s (%s): skipped — %s", token, source_name, msg)
                _record_source_result(
                    db,
                    src,
                    status=STATUS_SKIPPED,
                    error=msg,
                    jobs_found=None,
                    failure_threshold=threshold,
                    stats=stats,
                )
                continue

            adapter = source_instances.get(source_name)
            if adapter is None:
                adapter = cls()
                source_instances[source_name] = adapter

            stats.boards_attempted += 1
            try:
                postings = list(adapter.fetch(token))
            except SourceUnavailable as e:
                stats.boards_failed += 1
                stats.boards_failures.append({"board": f"{source_name}:{token}", "error": str(e)})
                log.warning("%s (%s): error — %s", token, source_name, e)
                _record_source_result(
                    db,
                    src,
                    status=STATUS_ERROR,
                    error=str(e),
                    jobs_found=0,
                    failure_threshold=threshold,
                    stats=stats,
                )
                continue
            except Exception as e:  # noqa: BLE001
                stats.boards_failed += 1
                stats.boards_failures.append(
                    {"board": f"{source_name}:{token}", "error": f"unexpected: {e}"}
                )
                log.exception("%s (%s): unexpected failure", token, source_name)
                _record_source_result(
                    db,
                    src,
                    status=STATUS_ERROR,
                    error=f"unexpected: {e}",
                    jobs_found=0,
                    failure_threshold=threshold,
                    stats=stats,
                )
                continue

            fetched_n = len(postings)
            added = 0
            updated = 0
            outside = 0
            duplicates = 0
            unchanged = 0

            for nj in postings:
                stats.fetched += 1
                source_updated = _ensure_aware(nj.source_updated_at)
                if source_updated is None or source_updated < window_start:
                    stats.skipped_outside_window += 1
                    outside += 1
                    continue
                key = (nj.source, nj.external_id)
                if key in seen:
                    stats.skipped_duplicates += 1
                    duplicates += 1
                    continue
                seen.add(key)
                inserted_row, updated_row = _upsert(db, nj, source_updated)
                if inserted_row:
                    stats.inserted += 1
                    added += 1
                elif updated_row:
                    stats.updated += 1
                    updated += 1
                else:
                    stats.skipped_duplicates += 1
                    unchanged += 1

            log.info(
                "%s (%s): fetched %d, added %d, updated %d, skipped %d "
                "(outside %d / dup %d / unchanged %d)",
                token,
                source_name,
                fetched_n,
                added,
                updated,
                outside + duplicates + unchanged,
                outside,
                duplicates,
                unchanged,
            )
            _record_source_result(
                db,
                src,
                status=STATUS_SUCCESS,
                error=None,
                jobs_found=added + updated,
                failure_threshold=threshold,
                stats=stats,
            )
    finally:
        for adapter in source_instances.values():
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

    stats.deleted_expired = _delete_expired(db, window_start)
    db.commit()
    stats.finished_at = _utcnow().isoformat()
    return stats


def _record_source_result(
    db: Session,
    src: Source,
    *,
    status: str,
    error: str | None,
    jobs_found: int | None,
    failure_threshold: int,
    stats: IngestStats,
) -> None:
    """Write the per-source telemetry onto the Source row and commit.

    Tracks `consecutive_failures` so a board that's been erroring for
    `failure_threshold` runs in a row gets `enabled=False`d — the
    operator can re-enable it once the underlying token is fixed. We
    only count `STATUS_ERROR` toward the streak; `STATUS_SKIPPED` is a
    config issue (unknown source_type), not a board failure.

    Committing per-source means a crash halfway through the run still
    leaves an honest picture of what ran (and what didn't) on the table.
    """
    src.last_run_at = _utcnow()
    src.last_status = status
    src.last_error = error
    src.jobs_found_last_run = jobs_found

    if status == STATUS_SUCCESS:
        src.consecutive_failures = 0
    elif status == STATUS_ERROR:
        src.consecutive_failures = (src.consecutive_failures or 0) + 1
        if src.enabled and src.consecutive_failures >= failure_threshold:
            src.enabled = False
            stats.boards_auto_disabled.append(f"{src.source_type}:{src.token}")
            log.warning(
                "%s (%s): auto-disabled after %d consecutive failures",
                src.token,
                src.source_type,
                src.consecutive_failures,
            )
    # STATUS_SKIPPED (unknown source_type) doesn't move the counter.

    db.commit()


def _upsert(db: Session, nj: NormalizedJob, source_updated: datetime) -> tuple[bool, bool]:
    """Return (inserted, updated). Both False means no-change (same hash)."""
    new_hash = _content_hash(nj)
    posted = _ensure_aware(nj.posted_at)

    existing = db.execute(
        select(Job).where(Job.source == nj.source, Job.external_id == nj.external_id)
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            Job(
                source=nj.source,
                external_id=nj.external_id,
                company=nj.company,
                title=nj.title,
                location=nj.location,
                remote=nj.remote,
                employment_type=nj.employment_type,
                skills=list(nj.skills),
                sponsors_visa=nj.sponsors_visa,
                url=nj.url,
                description=nj.description,
                content_hash=new_hash,
                posted_at=posted,
                source_updated_at=source_updated,
            )
        )
        return True, False

    if existing.content_hash == new_hash:
        # Same content — leave the row alone. Timestamp comparison would be
        # unreliable across SQLite/Postgres (tz precision), and refreshing
        # `source_updated_at` artificially would extend the rolling window
        # beyond what the source actually reports.
        return False, False

    existing.company = nj.company
    existing.title = nj.title
    existing.location = nj.location
    existing.remote = nj.remote
    existing.employment_type = nj.employment_type
    existing.skills = list(nj.skills)
    existing.sponsors_visa = nj.sponsors_visa
    existing.url = nj.url
    existing.description = nj.description
    existing.content_hash = new_hash
    existing.posted_at = posted
    existing.source_updated_at = source_updated
    return False, True


def _delete_expired(db: Session, window_start: datetime) -> int:
    """Delete any job whose source_updated_at is older than the window
    (or NULL — those can't satisfy the freshness guarantee).

    Manual jobs (source == MANUAL_SOURCE) are exempt — they persist until
    explicitly deleted via the admin DELETE endpoint.
    """
    from sqlalchemy import or_

    from app.models.job import MANUAL_SOURCE

    result = db.execute(
        delete(Job).where(
            Job.source != MANUAL_SOURCE,
            or_(Job.source_updated_at.is_(None), Job.source_updated_at < window_start),
        )
    )
    return result.rowcount or 0


# ─── Background runner ─────────────────────────────────────────────────────


def _launch_worker(target, args: tuple) -> None:
    """Indirection so tests can monkey-patch to run inline.

    Production: spawns a daemon thread. Tests: replace this with
    `lambda t, a: t(*a)` to execute the worker synchronously."""
    threading.Thread(target=target, args=args, daemon=True).start()


def _finish_run(
    db: Session,
    run_id: str,
    *,
    status: str,
    stats: dict,
    error: str | None,
) -> None:
    """Write the terminal status onto an IngestRun row. Silent if the row
    is missing (e.g. operator-deleted) — we'd rather log + move on than
    crash the worker."""
    run = db.execute(select(IngestRun).where(IngestRun.run_id == run_id)).scalar_one_or_none()
    if run is None:
        log.warning("ingest run %s row not found at finish", run_id)
        return
    run.status = status
    run.stats = stats
    run.error = error
    run.finished_at = _utcnow()
    db.commit()


def _execute_ingest_run(run_id: str, settings: Settings) -> None:
    """Worker entrypoint. Opens its own DB session(s); never raises out."""
    try:
        with SessionLocal() as db:
            stats = run_ingest(db, settings)
        with SessionLocal() as db:
            _finish_run(
                db,
                run_id,
                status=INGEST_STATUS_SUCCESS,
                stats=stats.to_dict(),
                error=None,
            )
    except Exception as e:  # noqa: BLE001
        log.exception("ingest run %s failed", run_id)
        try:
            with SessionLocal() as db:
                _finish_run(
                    db,
                    run_id,
                    status=INGEST_STATUS_FAILED,
                    stats={},
                    error=str(e),
                )
        except Exception:  # noqa: BLE001
            log.exception(
                "failed to record failure for ingest run %s — DB unreachable?",
                run_id,
            )


def start_background_ingest(settings: Settings) -> str:
    """Create an IngestRun row + spawn a worker. Returns the run_id so
    the caller can hand it back to the client immediately.

    The HTTP request that triggered this call is free to return 202 the
    moment we return — the actual work continues in `_execute_ingest_run`
    until it commits its terminal status onto the IngestRun row.
    """
    run_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(IngestRun(run_id=run_id, status="running", stats={}))
        db.commit()
    _launch_worker(_execute_ingest_run, (run_id, settings))
    return run_id
