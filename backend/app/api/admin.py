"""Admin endpoint — runs ingest + cleanup. Protected by a shared token.

The scheduled GitHub Actions workflow calls this every 6h. Local dev and
the CLI go through `app.services.ingest.run_ingest` directly; this route
is the production trigger.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.services.ingest import run_ingest

router = APIRouter()


def _require_admin(settings: Settings, token: str | None) -> None:
    expected = settings.admin_token
    if not expected:
        # If no admin token is configured, the endpoint is locked shut —
        # never serve an unprotected admin call.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin endpoint disabled: ADMIN_TOKEN is not configured",
        )
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


@router.post("/admin/ingest")
def admin_ingest(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    _require_admin(settings, x_admin_token)
    stats = run_ingest(db, settings)
    return stats.to_dict()
