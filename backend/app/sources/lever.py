"""Lever public-postings adapter.

API: GET https://api.lever.co/v0/postings/{company}?mode=json
Docs: https://github.com/lever/postings-api

Response is a JSON array (NOT an object). Per-posting (abbreviated):
    {
      "id": "abc-123",
      "text": "Software Engineer",
      "hostedUrl": "https://jobs.lever.co/...",
      "applyUrl": "https://jobs.lever.co/.../apply",
      "createdAt": 1716200000000,           // ms since epoch
      "updatedAt": 1716300000000,           // present on most boards
      "categories": {
        "location": "San Francisco",
        "team": "Engineering",
        "commitment": "Full-time"
      },
      "workplaceType": "remote",            // remote | onsite | hybrid
      "descriptionPlain": "...",
      "description": "<p>...</p>"
    }
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

import httpx

from app.services.skills import extract_skills
from app.sources._text import clean_html, infer_remote, infer_sponsorship, strip_html
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable

BASE_URL = "https://api.lever.co/v0/postings/{company}"


class LeverSource(JobSource):
    name = "lever"

    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._timeout = timeout

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        url = BASE_URL.format(company=token)
        try:
            resp = self._client.get(url, params={"mode": "json"})
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"lever:{token} request failed: {e}") from e
        return self._parse_response(token, resp.status_code, resp.text, resp.json)

    async def fetch_async(
        self,
        token: str,
        async_client: httpx.AsyncClient | None = None,
    ) -> Iterable[NormalizedJob]:
        url = BASE_URL.format(company=token)
        own = async_client is None
        client = async_client or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                resp = await client.get(url, params={"mode": "json"})
            except httpx.HTTPError as e:
                raise SourceUnavailable(f"lever:{token} request failed: {e}") from e
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
            raise SourceUnavailable(f"lever:{token} not found (404)")
        if status_code >= 400:
            raise SourceUnavailable(f"lever:{token} HTTP {status_code}: {text[:200]}")
        try:
            payload = json_loader()
        except ValueError as e:
            raise SourceUnavailable(f"lever:{token} bad JSON: {e}") from e
        if not isinstance(payload, list):
            raise SourceUnavailable(f"lever:{token} unexpected payload shape (not a list)")
        return list(self._parse_jobs(token, payload))

    def _parse_jobs(self, token: str, jobs: list[dict]) -> Iterator[NormalizedJob]:
        for raw in jobs:
            try:
                yield self._parse_one(token, raw)
            except (KeyError, ValueError, TypeError):
                continue

    def _parse_one(self, token: str, raw: dict) -> NormalizedJob:
        external_id = str(raw["id"])
        title = str(raw["text"]).strip()
        # Lever exposes both hostedUrl and applyUrl. applyUrl drops the user
        # directly into the application form, which is what we want.
        url = str(raw.get("applyUrl") or raw["hostedUrl"])

        categories = raw.get("categories") or {}
        location = categories.get("location") if isinstance(categories, dict) else None
        employment_type = categories.get("commitment") if isinstance(categories, dict) else None

        # Prefer the rich HTML `description` for storage — the frontend
        # renders it (sanitized) with paragraphs, lists, and emphasis
        # intact. Fall back to the plain-text `descriptionPlain` field
        # wrapped in a paragraph when HTML isn't provided, so the
        # frontend's render path always sees HTML.
        raw_html = raw.get("description")
        raw_plain = raw.get("descriptionPlain")
        if raw_html:
            description_html: str | None = clean_html(raw_html) or None
        elif raw_plain:
            cleaned_plain = clean_html(raw_plain)
            description_html = f"<p>{cleaned_plain}</p>" if cleaned_plain else None
        else:
            description_html = None
        # Heuristics see plain text regardless of which field carried
        # the JD.
        description_text = strip_html(raw_html or raw_plain or "")

        # Lever ships timestamps as ms epoch. updatedAt isn't always present;
        # fall back to createdAt.
        updated_at = _parse_ms_epoch(raw.get("updatedAt")) or _parse_ms_epoch(raw.get("createdAt"))
        posted_at = _parse_ms_epoch(raw.get("createdAt")) or updated_at

        workplace = (raw.get("workplaceType") or "").lower() or None
        if workplace == "remote":
            remote: bool | None = True
        elif workplace == "on-site" or workplace == "onsite":
            remote = False
        else:
            remote = infer_remote(location, description_text)

        return NormalizedJob(
            source=self.name,
            external_id=external_id,
            company=token,
            title=title,
            url=url,
            location=location,
            remote=remote,
            employment_type=employment_type,
            description=description_html,
            posted_at=posted_at,
            source_updated_at=updated_at or posted_at or datetime.now(UTC),
            sponsors_visa=infer_sponsorship(description_text),
            skills=extract_skills(description_text),
        )


def _parse_ms_epoch(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
