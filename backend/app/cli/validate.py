"""`python -m app.cli validate-companies` — probe enabled board tokens.

Reads from the same `sources` table the live ingest reads from, so the
output mirrors what the next ingest pass will actually pull. Disabled
rows are listed separately rather than probed.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models.source import Source
from app.sources import SOURCES
from app.sources.base import SourceUnavailable

log = logging.getLogger(__name__)


def run() -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    reachable: list[dict] = []
    unreachable: list[dict] = []
    disabled: list[dict] = []

    with SessionLocal() as db:
        all_rows = list(
            db.execute(select(Source).order_by(Source.source_type, Source.token)).scalars()
        )

    by_source: dict[str, object] = {}

    try:
        for src in all_rows:
            if not src.enabled:
                disabled.append({"source": src.source_type, "token": src.token})
                continue
            source_name = src.source_type
            token = src.token
            cls = SOURCES.get(source_name)
            if cls is None:
                unreachable.append(
                    {"source": source_name, "token": token, "error": "unknown source"}
                )
                continue
            adapter = by_source.get(source_name)
            if adapter is None:
                adapter = cls()
                by_source[source_name] = adapter
            try:
                postings = list(adapter.fetch(token))  # type: ignore[attr-defined]
            except SourceUnavailable as e:
                unreachable.append({"source": source_name, "token": token, "error": str(e)})
                log.warning("✗ %s:%s — %s", source_name, token, e)
                continue
            except Exception as e:  # noqa: BLE001
                unreachable.append(
                    {"source": source_name, "token": token, "error": f"unexpected: {e}"}
                )
                log.exception("✗ %s:%s unexpected", source_name, token)
                continue
            reachable.append({"source": source_name, "token": token, "postings": len(postings)})
            log.info("✓ %s:%s — %d postings", source_name, token, len(postings))
    finally:
        for adapter in by_source.values():
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

    return {
        "total": len(all_rows),
        "reachable_count": len(reachable),
        "unreachable_count": len(unreachable),
        "disabled_count": len(disabled),
        "reachable": reachable,
        "unreachable": unreachable,
        "disabled": disabled,
    }
