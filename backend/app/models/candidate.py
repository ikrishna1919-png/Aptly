"""The Candidate model.

Phase 4 is single-user: there is exactly one candidate row in the
database (slug=`demo`), seeded by migration 0005. The tailoring service
reads from this row instead of the hardcoded Python dict so that:

  - the seed is observable in the live DB (Neon, Render-managed)
  - future migrations can edit the canonical candidate without code change
  - Phase 2 can extend this table per-user without a schema rewrite
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


DEMO_SLUG = "demo"
