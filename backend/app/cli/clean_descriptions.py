"""`python -m app.cli clean-descriptions [--dry-run]`

One-off backfill that runs `strip_html()` over every Job.description that
still carries HTML markup or encoded entities, in place. Idempotent — runs
that find nothing to do return zero changes. Cheap to re-run.

Designed to fix data ingested before the strip_html rewrite, where rows
were stored as raw HTML and now look like a wall of `<p>…</p>`.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models.job import Job
from app.sources._text import looks_like_html, strip_html

log = logging.getLogger(__name__)


def run(*, dry_run: bool = False) -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    scanned = 0
    htmlish = 0
    changed = 0
    examples: list[dict] = []

    with SessionLocal() as db:
        for job in db.execute(select(Job)).scalars():
            scanned += 1
            raw = job.description
            if not raw or not looks_like_html(raw):
                continue
            htmlish += 1
            cleaned = strip_html(raw)
            if cleaned == raw:
                # Was flagged by the cheap heuristic but the cleaner is
                # a no-op — skip to avoid an empty UPDATE.
                continue
            changed += 1
            if len(examples) < 5:
                examples.append(
                    {
                        "job_id": job.id,
                        "before_chars": len(raw),
                        "after_chars": len(cleaned),
                        "preview": cleaned[:140],
                    }
                )
            if not dry_run:
                job.description = cleaned

        if not dry_run:
            db.commit()
        else:
            db.rollback()

    return {
        "scanned": scanned,
        "htmlish": htmlish,
        "changed": changed,
        "dry_run": dry_run,
        "examples": examples,
    }
