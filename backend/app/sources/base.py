from __future__ import annotations

import abc
import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

import httpx


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
    """Interface every ATS adapter implements.

    Adapters expose both a sync `fetch` (used by tests + the
    validate-companies CLI) and an async `fetch_async` (used by the
    parallelized ingest path). Adapters with a native async
    implementation override `fetch_async`; the default falls back to
    running the sync `fetch` in a worker thread, which still parallelises
    correctly since `httpx.Client` releases the GIL on network I/O.
    """

    name: str  # short identifier persisted on the Job row, e.g. "greenhouse"

    @abc.abstractmethod
    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        """Yield every posting for the given board token.

        Raises `SourceUnavailable` if the token doesn't resolve (404, etc.)
        so callers can skip cleanly.
        """

    async def fetch_async(
        self,
        token: str,
        async_client: httpx.AsyncClient | None = None,
    ) -> Iterable[NormalizedJob]:
        """Concurrent variant of `fetch`. Native-async adapters override
        this and use the supplied `async_client` (shared across the
        ingest run for connection pooling). The default offloads the
        sync `fetch` to a worker thread so adapters that haven't been
        portable to async still benefit from the orchestrator's
        parallelism."""
        del async_client  # default path doesn't need it
        return await asyncio.to_thread(self.fetch, token)


class SourceUnavailable(Exception):
    """Raised when a board token can't be reached (404, network, malformed)."""
