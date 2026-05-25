"""Ingest orchestrator: fetch → dedupe → upsert → expire.

The rolling-window guarantee: after every run, the `jobs` table contains
exactly the postings whose `source_updated_at` falls within the last
`HOURS_WINDOW` hours. Anything older is deleted.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.job import Job
from app.sources import SOURCES, JobSource, NormalizedJob
from app.sources.base import SourceUnavailable
from app.sources.companies import COMPANIES

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    """What an ingest run did. Returned to the CLI / admin endpoint."""

    window_hours: int
    boards_attempted: int = 0
    boards_failed: int = 0
    boards_failures: list[dict] = field(default_factory=list)  # [{board, error}]
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


def run_ingest(
    db: Session,
    settings: Settings,
    companies: list[tuple[str, str]] | None = None,
    source_factories: dict[str, type[JobSource]] | None = None,
) -> IngestStats:
    """Run a full ingest+cleanup pass. Commits at the end."""

    companies = companies if companies is not None else COMPANIES
    source_factories = source_factories if source_factories is not None else SOURCES

    stats = IngestStats(window_hours=settings.hours_window, started_at=_utcnow().isoformat())
    window_start = _utcnow() - timedelta(hours=settings.hours_window)
    seen: set[tuple[str, str]] = set()

    # Cache source instances so we don't open one client per board.
    source_instances: dict[str, JobSource] = {}

    try:
        for source_name, token in companies:
            cls = source_factories.get(source_name)
            if cls is None:
                log.warning("unknown source %r; skipping %r", source_name, token)
                continue
            source = source_instances.get(source_name)
            if source is None:
                source = cls()
                source_instances[source_name] = source

            stats.boards_attempted += 1
            try:
                postings = list(source.fetch(token))
            except SourceUnavailable as e:
                stats.boards_failed += 1
                stats.boards_failures.append({"board": f"{source_name}:{token}", "error": str(e)})
                log.warning("skipping %s:%s — %s", source_name, token, e)
                continue
            except Exception as e:  # noqa: BLE001
                stats.boards_failed += 1
                stats.boards_failures.append(
                    {"board": f"{source_name}:{token}", "error": f"unexpected: {e}"}
                )
                log.exception("unexpected failure on %s:%s", source_name, token)
                continue

            for nj in postings:
                stats.fetched += 1
                source_updated = _ensure_aware(nj.source_updated_at)
                if source_updated is None or source_updated < window_start:
                    stats.skipped_outside_window += 1
                    continue
                key = (nj.source, nj.external_id)
                if key in seen:
                    stats.skipped_duplicates += 1
                    continue
                seen.add(key)
                inserted, updated = _upsert(db, nj, source_updated)
                if inserted:
                    stats.inserted += 1
                elif updated:
                    stats.updated += 1
                else:
                    stats.skipped_duplicates += 1
    finally:
        for source in source_instances.values():
            close = getattr(source, "close", None)
            if callable(close):
                close()

    stats.deleted_expired = _delete_expired(db, window_start)
    db.commit()
    stats.finished_at = _utcnow().isoformat()
    return stats


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
    (or NULL — those can't satisfy the freshness guarantee)."""
    from sqlalchemy import or_

    result = db.execute(
        delete(Job).where(
            or_(Job.source_updated_at.is_(None), Job.source_updated_at < window_start)
        )
    )
    return result.rowcount or 0
