"""Greenhouse public-board adapter.

API: GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Docs: https://developers.greenhouse.io/job-board.html

Response shape (abbreviated):
    {
      "jobs": [
        {
          "id": 12345,
          "title": "Software Engineer",
          "absolute_url": "https://...",
          "updated_at": "2026-05-20T15:30:00Z",
          "first_published": "2026-04-01T...",
          "location": {"name": "Remote, US"},
          "content": "<p>HTML-encoded JD ...</p>"
        }
      ],
      "meta": {"total": 123}
    }
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

import httpx

from app.services.skills import extract_skills
from app.sources._text import clean_html, infer_remote, infer_sponsorship, strip_html
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseSource(JobSource):
    name = "greenhouse"

    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._timeout = timeout

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        url = BASE_URL.format(token=token)
        try:
            resp = self._client.get(url, params={"content": "true"})
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"greenhouse:{token} request failed: {e}") from e
        return self._parse_response(token, resp.status_code, resp.text, resp.json)

    async def fetch_async(
        self,
        token: str,
        async_client: httpx.AsyncClient | None = None,
    ) -> Iterable[NormalizedJob]:
        url = BASE_URL.format(token=token)
        own = async_client is None
        client = async_client or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                resp = await client.get(url, params={"content": "true"})
            except httpx.HTTPError as e:
                raise SourceUnavailable(f"greenhouse:{token} request failed: {e}") from e
            return self._parse_response(token, resp.status_code, resp.text, resp.json)
        finally:
            if own:
                await client.aclose()

    def _parse_response(
        self,
        token: str,
        status_code: int,
        text: str,
        json_loader,
    ) -> list[NormalizedJob]:
        if status_code == 404:
            raise SourceUnavailable(f"greenhouse:{token} not found (404)")
        if status_code >= 400:
            raise SourceUnavailable(f"greenhouse:{token} HTTP {status_code}: {text[:200]}")
        try:
            payload = json_loader()
        except ValueError as e:
            raise SourceUnavailable(f"greenhouse:{token} bad JSON: {e}") from e
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise SourceUnavailable(f"greenhouse:{token} unexpected payload shape")
        return list(self._parse_jobs(token, payload["jobs"]))

    def _parse_jobs(self, token: str, jobs: list[dict]) -> Iterator[NormalizedJob]:
        for raw in jobs:
            try:
                yield self._parse_one(token, raw)
            except (KeyError, ValueError, TypeError):
                # One malformed posting shouldn't kill the whole board.
                continue

    def _parse_one(self, token: str, raw: dict) -> NormalizedJob:
        external_id = str(raw["id"])
        title = str(raw["title"]).strip()
        url = str(raw["absolute_url"])
        location_obj = raw.get("location") or {}
        location = (location_obj.get("name") or None) if isinstance(location_obj, dict) else None

        # Two views of the JD: HTML for storage + UI rendering, plain
        # text for the keyword / remote / sponsorship heuristics
        # (which would otherwise key on `<p>` tags as if they were
        # content).
        raw_content = raw.get("content")
        description_html = clean_html(raw_content) or None
        description_text = strip_html(raw_content)

        updated_at = _parse_iso(raw.get("updated_at"))
        posted_at = _parse_iso(raw.get("first_published")) or updated_at

        return NormalizedJob(
            source=self.name,
            external_id=external_id,
            company=token,
            title=title,
            url=url,
            location=location,
            remote=infer_remote(location, description_text),
            employment_type=None,  # Greenhouse boards don't expose this consistently.
            description=description_html,
            posted_at=posted_at,
            source_updated_at=updated_at or posted_at or _utcnow(),
            sponsors_visa=infer_sponsorship(description_text),
            skills=extract_skills(description_text),
        )


def _parse_iso(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    # Greenhouse uses ISO 8601 with trailing Z.
    s = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _utcnow() -> datetime:

    return datetime.now(UTC)
