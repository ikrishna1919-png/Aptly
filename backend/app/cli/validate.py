"""`python -m app.cli validate-companies` — probe seeded board tokens."""

from __future__ import annotations

import logging

from app.sources import SOURCES
from app.sources.base import SourceUnavailable
from app.sources.companies import COMPANIES

log = logging.getLogger(__name__)


def run() -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    reachable: list[dict] = []
    unreachable: list[dict] = []

    by_source: dict[str, object] = {}

    try:
        for source_name, token in COMPANIES:
            cls = SOURCES.get(source_name)
            if cls is None:
                unreachable.append(
                    {"source": source_name, "token": token, "error": "unknown source"}
                )
                continue
            source = by_source.get(source_name)
            if source is None:
                source = cls()
                by_source[source_name] = source
            try:
                postings = list(source.fetch(token))  # type: ignore[attr-defined]
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
        for source in by_source.values():
            close = getattr(source, "close", None)
            if callable(close):
                close()

    return {
        "total": len(COMPANIES),
        "reachable_count": len(reachable),
        "unreachable_count": len(unreachable),
        "reachable": reachable,
        "unreachable": unreachable,
    }
