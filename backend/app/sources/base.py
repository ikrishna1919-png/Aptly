from __future__ import annotations

import abc
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NormalizedJob:
    """Source-agnostic job representation.

    Adapters return these; the ingest service handles dedupe, expiry, and
    persistence. `source_updated_at` is the source-reported timestamp used
    by the rolling-window logic — NOT the DB row timestamp.
    """

    source: str
    external_id: str
    company: str
    title: str
    url: str
    source_updated_at: datetime
    location: str | None = None
    remote: bool | None = None
    employment_type: str | None = None
    description: str | None = None
    posted_at: datetime | None = None
    sponsors_visa: bool | None = None
    skills: list[str] = field(default_factory=list)


class JobSource(abc.ABC):
    """Interface every ATS adapter implements."""

    name: str  # short identifier persisted on the Job row, e.g. "greenhouse"

    @abc.abstractmethod
    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        """Yield every posting for the given board token.

        Raises `SourceUnavailable` if the token doesn't resolve (404, etc.)
        so callers can skip cleanly.
        """


class SourceUnavailable(Exception):
    """Raised when a board token can't be reached (404, network, malformed)."""
