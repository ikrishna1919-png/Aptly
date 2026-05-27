"""Ashby public-board adapter.

API: GET https://api.ashbyhq.com/posting-api/job-board/{board_token}
     ?includeCompensation=true
Docs: https://developers.ashbyhq.com/reference/jobboard

Response shape (abbreviated):

    {
      "apiVersion": "1",
      "jobs": [
        {
          "id": "ad7…uuid",
          "title": "Senior Software Engineer",
          "departmentName": "Engineering",
          "teamName": "Platform",
          "locationName": "Remote",
          "employmentType": "FullTime",
          "publishedDate": "2026-05-20T10:00:00Z",
          "updatedAt": "2026-05-25T10:00:00Z",
          "jobUrl": "https://jobs.ashbyhq.com/{token}/{id}",
          "applyUrl": "https://jobs.ashbyhq.com/{token}/{id}/application",
          "descriptionHtml": "<p>HTML body…</p>",
          "descriptionPlain": "Plain text body…",
          "isRemote": true,
          ...
        }
      ]
    }

No auth is needed for published boards. The endpoint returns every
posting in one response (no pagination) so the adapter is closer in
shape to Greenhouse than to SmartRecruiters / Workday.

Per-board failure isolation: one malformed posting drops only itself;
a list-level failure (4xx, 5xx, bad JSON, unexpected payload shape)
raises `SourceUnavailable` so the orchestrator records
`last_status='error'` on the row and moves on.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

import httpx

from app.services.skills import extract_skills
from app.sources._text import clean_html, infer_remote, infer_sponsorship, strip_html
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


class AshbySource(JobSource):
    name = "ashby"

    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._timeout = timeout

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ── Public API ────────────────────────────────────────────────────────

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        url = BASE_URL.format(token=token)
        try:
            resp = self._client.get(url, params={"includeCompensation": "true"})
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"ashby:{token} request failed: {e}") from e
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
                resp = await client.get(url, params={"includeCompensation": "true"})
            except httpx.HTTPError as e:
                raise SourceUnavailable(f"ashby:{token} request failed: {e}") from e
            return self._parse_response(token, resp.status_code, resp.text, resp.json)
        finally:
            if own:
                await client.aclose()

    # ── Shared response parsing ───────────────────────────────────────────

    def _parse_response(
        self,
        token: str,
        status_code: int,
        text: str,
        json_loader,
    ) -> list[NormalizedJob]:
        if status_code == 404:
            raise SourceUnavailable(f"ashby:{token} not found (404)")
        if status_code >= 400:
            raise SourceUnavailable(f"ashby:{token} HTTP {status_code}: {text[:200]}")
        try:
            payload = json_loader()
        except ValueError as e:
            raise SourceUnavailable(f"ashby:{token} bad JSON: {e}") from e
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise SourceUnavailable(f"ashby:{token} unexpected payload shape")
        return list(self._parse_jobs(token, payload["jobs"]))

    def _parse_jobs(self, token: str, jobs: list[dict]) -> Iterator[NormalizedJob]:
        for raw in jobs:
            if not isinstance(raw, dict):
                continue
            try:
                yield self._parse_one(token, raw)
            except (KeyError, ValueError, TypeError):
                # One malformed posting can't kill the whole board —
                # same per-posting contract as the other adapters.
                continue

    def _parse_one(self, token: str, raw: dict) -> NormalizedJob:
        external_id = str(raw["id"])
        title = str(raw["title"]).strip()
        if not title:
            raise KeyError("missing title")

        # Apply URL: prefer the dedicated `applyUrl` (drops the user
        # straight into the application form); fall back to the
        # general `jobUrl`.
        url = str(raw.get("applyUrl") or raw.get("jobUrl") or "").strip()
        if not url:
            raise KeyError("missing url")

        location = _coerce_str(raw.get("locationName"))

        # Two views of the JD: HTML for storage / rendering, plain
        # text for the keyword + remote + sponsorship heuristics.
        # Ashby gives us both directly — use them rather than
        # round-tripping through strip_html.
        raw_html = raw.get("descriptionHtml")
        raw_plain = raw.get("descriptionPlain")
        description_html = clean_html(raw_html) or None
        if raw_plain and isinstance(raw_plain, str):
            description_text = raw_plain
        else:
            description_text = strip_html(raw_html or "")

        # `isRemote` is the most reliable remote signal Ashby gives;
        # fall back to text inference when the field is absent / not
        # a boolean.
        is_remote = raw.get("isRemote")
        if isinstance(is_remote, bool):
            remote: bool | None = is_remote
        else:
            remote = infer_remote(location, description_text)

        employment_type = _normalize_employment_type(raw.get("employmentType"))

        posted_at = _parse_iso(raw.get("publishedDate"))
        updated_at = _parse_iso(raw.get("updatedAt")) or posted_at

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
            source_updated_at=updated_at or posted_at or _utcnow(),
            sponsors_visa=infer_sponsorship(description_text),
            skills=extract_skills(description_text),
        )


# ── Helpers ────────────────────────────────────────────────────────────────


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    # Ashby uses ISO 8601 with a trailing Z. fromisoformat in 3.11+
    # accepts "Z" directly, but normalising keeps the path safe on
    # older Pythons + on the date-only edge case.
    s = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _normalize_employment_type(value: object) -> str | None:
    """Ashby returns CamelCase enums (`FullTime`, `PartTime`,
    `Contract`, `Intern`, `Temporary`). Other adapters store the
    user-facing string (`Full-time`, `Part-time`, …) so normalise."""
    if not isinstance(value, str) or not value.strip():
        return None
    norm = value.strip()
    mapping = {
        "fulltime": "Full-time",
        "parttime": "Part-time",
        "contract": "Contract",
        "intern": "Intern",
        "internship": "Intern",
        "temporary": "Temporary",
    }
    return mapping.get(norm.lower(), norm)


def _utcnow() -> datetime:
    return datetime.now(UTC)
