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

**Bounded + rotating**: each invocation pulls only the next
`INGEST_MAX_PER_RUN` sources ordered by `last_run_at ASC NULLS FIRST`
— so never-checked rows go first and successive runs rotate through
the table. This keeps each pass finishable on a free-tier scheduler
even as `sources` grows past a thousand rows.

**Incremental commit**: the bounded slice is processed in
`INGEST_BATCH_SIZE` batches; each batch is async-fetched in parallel,
then sync-written + committed (per-source, inside
`_record_source_result`) BEFORE the next batch's fetch begins. A
mid-run timeout or OOM therefore leaves already-completed sources'
telemetry + jobs on disk, instead of the all-or-nothing behaviour an
end-of-run commit would have.

The fetch step is parallelized with `httpx.AsyncClient` + an
`asyncio.Semaphore(settings.ingest_concurrency)` so the per-board
network waits overlap. DB writes stay sync — coroutines collect their
postings into the per-source result list first, then the existing
synchronous loop upserts them through the existing `Session`.

Per-board failures (timeout, 404, malformed JSON) are caught + logged +
counted but never abort the run — one slow board can't stall the rest.
HTTP-level timeouts live on the source adapters' `httpx.Client`
(`timeout=20.0` for all three) and are honoured by the async path via
the shared `AsyncClient` timeout.

Long ingests don't fit inside a typical HTTP request budget (Render's
free tier kills connections at 100s), so `POST /api/admin/ingest` no
longer awaits `run_ingest()` directly. It calls
`start_background_ingest()`, which writes an `IngestRun` row and spawns
a daemon thread to do the work, and returns the new `run_id` immediately
so the caller can poll `GET /api/admin/ingest/{run_id}` for completion.
The async fetch step runs INSIDE that background thread via
`asyncio.run` — it parallelises the network within the existing job,
not in place of it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import SessionLocal
from app.models.ingest_run import (
    INGEST_STATUS_FAILED,
    INGEST_STATUS_RUNNING,
    INGEST_STATUS_STALE,
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
    # Unknown-source-type rows that the orchestrator skipped — separate
    # from `boards_failed` because a config-level miss isn't a board
    # failure.
    boards_skipped: int = 0
    boards_failures: list[dict] = field(default_factory=list)  # [{board, error}]
    boards_auto_disabled: list[str] = field(default_factory=list)  # ["greenhouse:foo", …]
    fetched: int = 0  # raw postings returned by sources
    skipped_outside_window: int = 0
    skipped_duplicates: int = 0
    inserted: int = 0
    updated: int = 0
    deleted_expired: int = 0
    # Number of source rows this run planned to process (selected up front).
    # Surfaced in the very first heartbeat so the operator sees the planned
    # size immediately and can gauge progress (attempted / total_sources).
    total_sources: int = 0
    # Set true when a soft per-run budget (ingest_run_budget_seconds) cut the
    # pass short before all selected sources were processed. The run still
    # ends cleanly (success); rotation covers the rest next run.
    budget_truncated: bool = False
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


def _load_due_sources(db: Session, *, limit: int | None) -> list[Source]:
    """The next `limit` sources to ingest, oldest-checked first.

    Order is `last_run_at ASC NULLS FIRST` so never-checked rows are
    drained before any already-checked row gets a second pass. Over
    successive scheduled runs the cap rotates through every enabled
    source — important once `sources` has hundreds of rows and one
    pass can't cover them all within the scheduler's budget.
    `source_type` + `token` are tiebreakers so the order is stable
    across processes (log diffs are sane).

    `limit=None` loads EVERY enabled source (used when
    INGEST_MAX_PER_RUN<=0 — one pass covers the whole table)."""
    q = (
        select(Source)
        .where(Source.enabled.is_(True))
        .order_by(
            Source.last_run_at.asc().nullsfirst(),
            Source.source_type,
            Source.token,
        )
    )
    if limit is not None:
        q = q.limit(limit)
    return list(db.execute(q).scalars())


def _chunked(items: list, size: int):
    """Yield successive `size`-sized chunks from `items` (list-only)."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _ensure_no_tx(db: Session) -> None:
    """Leave the session with NO open transaction before long network I/O.

    A SQLAlchemy Session autobegins a transaction on the first statement and
    holds it until commit/rollback. If we entered the async fetch (up to 20s
    per board, ~25 boards) with a transaction open, the connection would sit
    idle-in-transaction and Neon would terminate it
    (`idle_in_transaction_session_timeout`, a few minutes, not ours to raise) —
    the next per-source `UPDATE sources …` then fails on a dead connection and
    the whole run dies with `attempted` stuck. We commit any pending work (it's
    meant to be durable per-source anyway) so the fetch starts clean."""
    if db.in_transaction():
        db.commit()


@dataclass(frozen=True)
class _SourceRef:
    """Plain, detached snapshot of a source row's identity.

    Passed through the async fetch phase INSTEAD of the `Source` ORM object so
    the network I/O never touches the Session: reading a token/type can't
    trigger a lazy-load (the rows are expired after each commit) that would
    autobegin a transaction and leave the connection idle-in-transaction during
    the fetch. The DB phase re-queries the row by `id`."""

    id: int
    source_type: str
    token: str


@dataclass
class _FetchOutcome:
    """One source row's network result, ready for the sync DB phase.

    `status` is one of `"ok"`, `"unknown"`, `"error"`, `"unexpected"`.
    `postings` is set only when `status == "ok"`; `error` carries the
    message in the other cases. Carries a detached `_SourceRef` (not the ORM
    row) so nothing in the async phase holds a Session-bound object."""

    ref: _SourceRef
    status: str
    postings: list[NormalizedJob] | None = None
    error: str | None = None


async def _fetch_one(
    ref: _SourceRef,
    adapter: JobSource | None,
    async_client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> _FetchOutcome:
    """Fetch a single source under the semaphore. Catches everything so
    one failure can't cancel sibling tasks — `asyncio.gather` with
    bare-raise semantics would tear the whole batch down on the first
    exception, and even `return_exceptions=True` muddles the
    per-source-type info we need for the telemetry write."""
    async with sem:
        if adapter is None:
            return _FetchOutcome(
                ref=ref,
                status="unknown",
                error=f"unknown source type {ref.source_type!r}",
            )
        try:
            postings = await adapter.fetch_async(ref.token, async_client=async_client)
            return _FetchOutcome(ref=ref, status="ok", postings=list(postings))
        except SourceUnavailable as e:
            return _FetchOutcome(ref=ref, status="error", error=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("%s (%s): unexpected fetch failure", ref.token, ref.source_type)
            return _FetchOutcome(ref=ref, status="unexpected", error=f"unexpected: {e}")


async def _fetch_all_async(
    source_refs: list[_SourceRef],
    source_factories: dict[str, type[JobSource]],
    concurrency: int,
    timeout: float,
) -> list[_FetchOutcome]:
    """Run every source's `fetch_async` concurrently, bounded by a
    semaphore. Returns one `_FetchOutcome` per input ref in input
    order. Operates only on detached `_SourceRef`s — never the Session — so
    it can't open a transaction that idles through the network waits."""
    if not source_refs:
        return []

    # Instantiate every adapter we'll need up front so the per-task
    # cache lookup is race-free. Adapters are shared across tasks; for
    # the native-async overrides (Greenhouse, Lever) the only mutable
    # state is the async_client we pass in, so concurrent calls are
    # safe. For the default `to_thread(fetch)` path, the underlying
    # sync `httpx.Client` is already thread-safe.
    instances: dict[str, JobSource] = {}
    for ref in source_refs:
        if ref.source_type in instances:
            continue
        cls = source_factories.get(ref.source_type)
        if cls is None:
            continue
        instances[ref.source_type] = cls()

    sem = asyncio.Semaphore(max(1, concurrency))
    try:
        async with httpx.AsyncClient(timeout=timeout) as async_client:
            tasks = [
                _fetch_one(ref, instances.get(ref.source_type), async_client, sem)
                for ref in source_refs
            ]
            return await asyncio.gather(*tasks)
    finally:
        # Sync adapters expose a `close()`; closing the unused sync
        # client is cheap and avoids a "unclosed httpx.Client" warning
        # when the adapter's sync client was opened in __init__ but
        # never used by the async path.
        for adapter in instances.values():
            close = getattr(adapter, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass


def run_ingest(
    db: Session,
    settings: Settings,
    sources: list[Source] | None = None,
    source_factories: dict[str, type[JobSource]] | None = None,
    run_id: str | None = None,
) -> IngestStats:
    """Run a bounded ingest+cleanup pass.

    Picks the next `INGEST_MAX_PER_RUN` enabled sources
    (`last_run_at ASC NULLS FIRST`), then processes them in batches of
    `INGEST_BATCH_SIZE`. Each batch is async-fetched in parallel, then
    sync-written + committed (via the per-source commit inside
    `_record_source_result`) BEFORE the next batch's fetch starts —
    so a mid-run timeout / OOM never wipes already-completed work.
    Over successive scheduled runs, the cap rotates through the
    table so every enabled source eventually gets pulled."""

    source_factories = source_factories if source_factories is not None else SOURCES
    raw_max = int(getattr(settings, "ingest_max_per_run", 150))
    batch_size = max(1, int(getattr(settings, "ingest_batch_size", 25)))
    threshold = max(1, int(getattr(settings, "source_failure_threshold", 3)))
    concurrency = max(1, int(getattr(settings, "ingest_concurrency", 10)))

    if sources is not None:
        source_rows = sources
    elif raw_max <= 0:
        # <= 0 disables the per-run cap: pull every enabled source in one
        # pass (sensible once the host is always-on). Still rotation-ordered.
        source_rows = _load_due_sources(db, limit=None)
    else:
        source_rows = _load_due_sources(db, limit=max(1, raw_max))

    # Snapshot each selected source's identity into a PLAIN, detached struct
    # NOW (rows are fresh / not yet expired). The async fetch phase uses only
    # these — never the ORM rows — so it can't lazy-load across a commit and
    # leave the connection idle-in-transaction during the network waits. The DB
    # phase re-queries each row by id.
    source_refs = [
        _SourceRef(id=s.id, source_type=s.source_type, token=s.token) for s in source_rows
    ]

    stats = IngestStats(window_hours=settings.hours_window, started_at=_utcnow().isoformat())
    stats.total_sources = len(source_refs)
    window_start = _utcnow() - timedelta(hours=settings.hours_window)
    seen: set[tuple[str, str]] = set()

    # Soft per-run wall-clock budget (0 = unlimited). monotonic so a clock
    # change can't skew it.
    budget = max(0, int(getattr(settings, "ingest_run_budget_seconds", 0)))
    run_start = time.monotonic()

    # Initial heartbeat BEFORE batch 1, so a live run is distinguishable from a
    # dead one within seconds (and the operator sees total_sources) instead of
    # the row sitting at {} until batch 1's `batch_size` fetches all resolve.
    if run_id is not None:
        _write_heartbeat(db, run_id, stats)

    for batch in _chunked(source_refs, batch_size):
        # Soft budget: once exceeded, stop STARTING new batches. Already-
        # processed batches are durable; we end the pass cleanly (success +
        # budget_truncated) and rotation picks up the rest next run — instead
        # of a time-limited / free-tier host killing the worker mid-fetch.
        if budget and (time.monotonic() - run_start) >= budget:
            stats.budget_truncated = True
            log.info(
                "ingest run budget %ds reached after %d/%d sources; stopping cleanly",
                budget,
                stats.boards_attempted + stats.boards_skipped,
                stats.total_sources,
            )
            break

        # Transaction hygiene: enter the network phase with NO open transaction
        # (the initial heartbeat / prior batch's per-source commits, or the
        # source-selection SELECT, may have left one open). Otherwise the
        # connection idles in-transaction through the fetch and Neon kills it.
        _ensure_no_tx(db)

        # Fetch this batch concurrently (bounded by the semaphore inside
        # `_fetch_all_async`), then drain its outcomes into the DB. By
        # the time we move to the next batch, every row in this batch
        # has had its telemetry committed — partial progress is
        # durable.
        outcomes = asyncio.run(
            _fetch_all_async(
                batch,
                source_factories,
                concurrency=concurrency,
                timeout=20.0,
            )
        )
        for outcome in outcomes:
            _process_outcome(
                db,
                outcome,
                stats=stats,
                window_start=window_start,
                seen=seen,
                threshold=threshold,
            )
            # Heartbeat after EVERY source so `attempted` advances
            # continuously (not just at batch boundaries). The per-source
            # telemetry already commits, so this is one cheap extra UPDATE;
            # network fetches — not commits — dominate run time. No-op when
            # run outside the worker (run_id=None: CLI / direct test calls).
            if run_id is not None:
                _write_heartbeat(db, run_id, stats)

    stats.deleted_expired = _delete_expired(db, window_start)
    db.commit()
    stats.finished_at = _utcnow().isoformat()

    # End-of-run summary so each scheduled invocation's outcome is
    # one log line away.
    log.info(
        "ingest complete: processed=%d succeeded=%d errored=%d skipped=%d "
        "jobs_added=%d (inserted=%d updated=%d) auto_disabled=%d deleted_expired=%d",
        stats.boards_attempted + stats.boards_skipped,
        stats.boards_attempted - stats.boards_failed,
        stats.boards_failed,
        stats.boards_skipped,
        stats.inserted + stats.updated,
        stats.inserted,
        stats.updated,
        len(stats.boards_auto_disabled),
        stats.deleted_expired,
    )
    return stats


def _process_outcome(
    db: Session,
    outcome: _FetchOutcome,
    *,
    stats: IngestStats,
    window_start: datetime,
    seen: set[tuple[str, str]],
    threshold: int,
) -> None:
    """Apply one fetch outcome to the DB: upsert its postings (if any)
    and commit per-source telemetry via `_record_source_result`. Splits
    out of `run_ingest` so the batch loop reads cleanly and so partial
    work survives — every call here ends in a commit."""
    ref = outcome.ref
    source_name = ref.source_type
    token = ref.token
    # Re-query the source row by id (we deliberately did NOT hold the ORM
    # object across the network phase). This attaches a fresh row in THIS
    # post-fetch transaction, so the telemetry UPDATE never runs on the idle
    # connection used for I/O. None ⇒ row deleted mid-run: skip its telemetry.
    src = db.get(Source, ref.id)

    if outcome.status == "unknown":
        stats.boards_skipped += 1
        log.warning("%s (%s): skipped — %s", token, source_name, outcome.error)
        if src is not None:
            _record_source_result(
                db,
                src,
                status=STATUS_SKIPPED,
                error=outcome.error,
                jobs_found=None,
                failure_threshold=threshold,
                stats=stats,
            )
        return

    stats.boards_attempted += 1

    if outcome.status in ("error", "unexpected"):
        stats.boards_failed += 1
        stats.boards_failures.append(
            {"board": f"{source_name}:{token}", "error": outcome.error or ""}
        )
        log.warning("%s (%s): error — %s", token, source_name, outcome.error)
        if src is not None:
            _record_source_result(
                db,
                src,
                status=STATUS_ERROR,
                error=outcome.error,
                jobs_found=0,
                failure_threshold=threshold,
                stats=stats,
            )
        return

    postings = outcome.postings or []
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
        "%s (%s): fetched %d, saved %d (within window) "
        "[inserted %d / updated %d / outside %d / dup %d / unchanged %d]",
        token,
        source_name,
        fetched_n,
        added + updated,
        added,
        updated,
        outside,
        duplicates,
        unchanged,
    )
    if src is not None:
        _record_source_result(
            db,
            src,
            status=STATUS_SUCCESS,
            error=None,
            jobs_found=added + updated,
            failure_threshold=threshold,
            stats=stats,
        )
    else:
        # Source row vanished mid-run; its upserted jobs are still pending and
        # get committed by the next heartbeat / the final commit.
        db.commit()


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


def _write_heartbeat(db: Session, run_id: str, stats: IngestStats) -> None:
    """Persist a progress snapshot onto the IngestRun row.

    Called once up front and after every source outcome, so `attempted`
    advances continuously and a live run is never mistaken for a dead one.
    Writes stats-so-far plus a `last_progress_at` timestamp into the JSON
    `stats` column (no schema change needed) and commits. If the worker
    thread is later killed mid-run, the row keeps this last snapshot
    instead of the empty {} it started with — so the status endpoints and
    the operator can see how far it got. Silent if the row is gone."""
    run = db.execute(select(IngestRun).where(IngestRun.run_id == run_id)).scalar_one_or_none()
    if run is None:
        return
    snapshot = stats.to_dict()
    snapshot["last_progress_at"] = _utcnow().isoformat()
    run.stats = snapshot
    db.commit()


def _last_activity(run: IngestRun) -> datetime | None:
    """When the run last showed signs of life: its newest heartbeat
    (`stats['last_progress_at']`) if present, else its `started_at`.
    Always tz-aware so it's safe to compare against `_utcnow()`."""
    stats = run.stats if isinstance(run.stats, dict) else {}
    ts = stats.get("last_progress_at")
    if ts:
        try:
            return _ensure_aware(datetime.fromisoformat(ts))
        except (TypeError, ValueError):
            pass
    return _ensure_aware(run.started_at)


def effective_run_status(run: IngestRun, settings: Settings) -> str:
    """The status to REPORT for a run (read-only — never mutates/commits).

    A row still marked `running` whose last activity is older than
    `STALE_RUN_MINUTES` is reported as `stale` — the worker was almost
    certainly killed mid-run. Terminal statuses pass through unchanged.
    The actual self-heal to `failed` happens in `_reap_stale_runs` on the
    next trigger; this is just so a dead run stops masquerading as live."""
    if run.status != INGEST_STATUS_RUNNING:
        return run.status
    last = _last_activity(run)
    if last is None:
        return run.status
    stale_after = max(1, int(getattr(settings, "stale_run_minutes", 15)))
    age_minutes = (_utcnow() - last).total_seconds() / 60.0
    return INGEST_STATUS_STALE if age_minutes > stale_after else run.status


def _reap_stale_runs(db: Session, settings: Settings) -> int:
    """Mark abandoned `running` rows as `failed` so the table self-heals.

    Called at the start of every trigger. Any row still `running` whose
    last activity is older than `STALE_RUN_MINUTES` is flipped to `failed`
    with error 'superseded/stale': a worker killed by a sleepy host never
    wrote its terminal status, so without this it would masquerade as live
    forever and `GET /admin/ingest` would lie. Returns the count reaped."""
    stale_after = max(1, int(getattr(settings, "stale_run_minutes", 15)))
    cutoff = _utcnow() - timedelta(minutes=stale_after)
    running = list(
        db.execute(select(IngestRun).where(IngestRun.status == INGEST_STATUS_RUNNING)).scalars()
    )
    reaped = 0
    for run in running:
        last = _last_activity(run)
        if last is not None and last < cutoff:
            run.status = INGEST_STATUS_FAILED
            run.error = f"superseded/stale: no progress for over {stale_after} min"
            run.finished_at = _utcnow()
            reaped += 1
    if reaped:
        db.commit()
        log.warning("reaped %d stale ingest run(s) to failed", reaped)
    return reaped


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
            stats = run_ingest(db, settings, run_id=run_id)
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
        # Self-heal first: flip any abandoned (killed-mid-run) `running`
        # rows to `failed` so the table is trustworthy and this new run is
        # the only live one.
        _reap_stale_runs(db, settings)
        db.add(IngestRun(run_id=run_id, status="running", stats={}))
        db.commit()
    _launch_worker(_execute_ingest_run, (run_id, settings))
    return run_id
