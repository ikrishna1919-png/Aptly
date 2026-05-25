"""`python -m app.cli ingest` — run ingest+cleanup against the configured DB."""

from __future__ import annotations

import logging

from app.config import get_settings
from app.database import SessionLocal
from app.services.ingest import run_ingest


def run() -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    with SessionLocal() as db:
        stats = run_ingest(db, settings)
    return stats.to_dict()
