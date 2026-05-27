"""Workday public-board adapter.

API: per-tenant JSON over POST + GET — no auth for public boards.

  LIST:   POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
          body: {"appliedFacets":{},"limit":20,"offset":N,"searchText":""}
          response: {"total": N, "jobPostings": [{"externalPath":"/job/…",
                     "title":"…","locationsText":"…",
                     "postedOn":"Posted 5 Days Ago", …}]}

  DETAIL: GET  https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}
          response: {"jobPostingInfo": {"id":"JR-…","title":"…",
                     "jobDescription":"<p>…</p>","location":"…",
                     "startDate":"2026-05-20","externalUrl":"https://…",
                     "jobReqId":"JR-…", "timeType":"Full time", …}}

The list page only carries relative-time strings (`"Posted 5 Days Ago"`)
rather than a real timestamp; the detail endpoint usually has a real
`startDate`. We use the detail's `startDate` when present and fall back
to parsing the list's relative text. If neither is parseable the row is
left without `source_updated_at` so the 48h-window filter drops it
cleanly — preferable to inventing a date.

Per-company config (tenant + data center + site) is packed into
`sources.token` as a colon-separated triple, e.g.
`generalmotors:wd5:Careers_GM`. Going composite keeps the existing
sources schema unchanged; the adapter unpacks at the top of every
fetch. `sources.display_name` carries the human-friendly company name.

Failure isolation: a single bad detail call (404, slow timeout,
malformed JSON) is caught + logged inside the per-company loop and
drops only that posting. A list-level failure raises
`SourceUnavailable`, which the ingest orchestrator records as the
row's `last_status='error'` without aborting the run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta

import httpx

from app.services.skills import extract_skills
from app.sources._text import clean_html, infer_remote, infer_sponsorship, strip_html
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable

log = logging.getLogger(__name__)

LIST_URL_TEMPLATE = "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
DETAIL_URL_TEMPLATE = (
    "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{external_path}"
)
# Convention-based public apply URL when the detail endpoint omits
# `externalUrl`. Note: this is the host-facing URL, not the API URL.
PUBLIC_URL_TEMPLATE = "https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}{external_path}"

# Cap to keep a single huge Workday board from dominating an ingest
# run. GM-scale boards are well under this; larger boards will get the
# first N postings — auto-prune surfaces the cap if it ever bites.
_MAX_POSTINGS_PER_COMPANY = 200
_PAGE_SIZE = 20  # Workday list endpoint's hard max.

# Matches "Posted N Days Ago", "Posted 30+ Days Ago", "Posted Today",
# "Posted Yesterday". `N+` ("30+ days ago") gets treated as exactly N
# days — Workday uses it as a ceiling, so older-than-N is the worst
# case the 48h window cares about.
_POSTED_REL_RE = re.compile(
    r"posted\s+(?P<value>today|yesterday|\d+\+?)\s*(?:days?\s*ago)?",
    re.IGNORECASE,
)


def _parse_token(token: str) -> tuple[str, str, str]:
    """Unpack `tenant:dc:site`. Raises SourceUnavailable on a malformed
    token — the orchestrator records that as the row's `last_error`
    and moves on, exactly as for a 404."""
    parts = token.split(":")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise SourceUnavailable(
            f"workday:{token} malformed token "
            "(expected 'tenant:dc:site', e.g. 'generalmotors:wd5:Careers_GM')"
        )
    return parts[0], parts[1], parts[2]


class WorkdaySource(JobSource):
    name = "workday"

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
        max_postings_per_company: int = _MAX_POSTINGS_PER_COMPANY,
        page_size: int = _PAGE_SIZE,
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._max_postings = max_postings_per_company
        self._page_size = page_size

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ── Public API ────────────────────────────────────────────────────────

    def fetch(self, token: str) -> Iterable[NormalizedJob]:
        tenant, dc, site = _parse_token(token)
        summaries = list(self._iter_summaries(tenant, dc, site))
        if not summaries:
            return []
        summaries = summaries[: self._max_postings]

        jobs: list[NormalizedJob] = []
        for summary in summaries:
            external_path = str(summary.get("externalPath") or "").strip()
            if not external_path:
                continue
            try:
                jobs.append(self._fetch_detail(tenant, dc, site, summary, external_path))
            except SourceUnavailable as e:
                # One bad posting can't abort the whole company — same
                # per-board contract the other adapters honour.
                log.warning("skipping workday:%s:%s — %s", tenant, external_path, e)
            except (KeyError, ValueError, TypeError) as e:
                log.warning(
                    "skipping workday:%s:%s malformed payload — %s",
                    tenant,
                    external_path,
                    e,
                )
        return jobs

    # ── List pagination ───────────────────────────────────────────────────

    def _iter_summaries(self, tenant: str, dc: str, site: str) -> Iterator[dict]:
        url = LIST_URL_TEMPLATE.format(tenant=tenant, dc=dc, site=site)
        offset = 0
        yielded = 0
        while yielded < self._max_postings:
            page = self._fetch_list_page(tenant, url, offset)
            postings = page.get("jobPostings") or []
            if not isinstance(postings, list) or not postings:
                return
            for item in postings:
                if isinstance(item, dict):
                    yield item
                    yielded += 1
                    if yielded >= self._max_postings:
                        return
            total = _coerce_int(page.get("total"))
            offset += len(postings)
            if total is not None and offset >= total:
                return
            # Defensive: a misbehaving API that hands back fewer than
            # we asked for AND hasn't told us the total is done — bail
            # rather than risk an infinite loop.
            if len(postings) < self._page_size:
                return

    def _fetch_list_page(self, tenant: str, url: str, offset: int) -> dict:
        body = {
            "appliedFacets": {},
            "limit": self._page_size,
            "offset": offset,
            "searchText": "",
        }
        try:
            resp = self._client.post(url, json=body)
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"workday:{tenant} list failed: {e}") from e
        if resp.status_code == 404:
            raise SourceUnavailable(f"workday:{tenant} not found (404)")
        if resp.status_code >= 400:
            raise SourceUnavailable(f"workday:{tenant} HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise SourceUnavailable(f"workday:{tenant} bad JSON: {e}") from e
        if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
            raise SourceUnavailable(f"workday:{tenant} unexpected list payload shape")
        return payload

    # ── Detail ────────────────────────────────────────────────────────────

    def _fetch_detail(
        self,
        tenant: str,
        dc: str,
        site: str,
        summary: dict,
        external_path: str,
    ) -> NormalizedJob:
        url = DETAIL_URL_TEMPLATE.format(
            tenant=tenant, dc=dc, site=site, external_path=external_path
        )
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"workday:{tenant}:{external_path} request failed: {e}") from e
        if resp.status_code == 404:
            raise SourceUnavailable(f"workday:{tenant}:{external_path} not found (404)")
        if resp.status_code >= 400:
            raise SourceUnavailable(f"workday:{tenant}:{external_path} HTTP {resp.status_code}")
        try:
            raw = resp.json()
        except ValueError as e:
            raise SourceUnavailable(f"workday:{tenant}:{external_path} bad JSON: {e}") from e
        return self._parse(tenant, dc, site, summary, external_path, raw)

    # ── Normalisation ─────────────────────────────────────────────────────

    def _parse(
        self,
        tenant: str,
        dc: str,
        site: str,
        summary: dict,
        external_path: str,
        raw: dict,
    ) -> NormalizedJob:
        info = raw.get("jobPostingInfo") if isinstance(raw, dict) else None
        if not isinstance(info, dict):
            raise KeyError("missing jobPostingInfo")

        external_id = str(info.get("id") or info.get("jobReqId") or external_path)
        title = str(info.get("title") or summary.get("title") or "").strip()
        if not title:
            raise KeyError("missing title")

        # Public-facing apply URL: prefer the API's `externalUrl`; fall
        # back to the convention.
        apply_url = str(info.get("externalUrl") or "").strip()
        if not apply_url:
            apply_url = PUBLIC_URL_TEMPLATE.format(
                tenant=tenant, dc=dc, site=site, external_path=external_path
            )

        location = info.get("location") or summary.get("locationsText") or info.get("locationsText")
        location = str(location).strip() if location else None

        # Two views of the JD: HTML for storage + UI rendering, plain
        # text for the keyword / remote / sponsorship heuristics.
        raw_html = info.get("jobDescription")
        description_html = clean_html(raw_html) or None
        description_text = strip_html(raw_html or "")

        employment_type = str(info.get("timeType") or "").strip() or None

        posted_at = _parse_workday_date(info.get("startDate")) or _parse_relative_posted(
            summary.get("postedOn")
        )
        # Workday doesn't reliably expose an "updated" timestamp — re-use
        # posted_at so the freshness window applies on the only signal
        # we have.
        updated_at = posted_at

        return NormalizedJob(
            source=self.name,
            external_id=external_id,
            company=tenant,
            title=title,
            url=apply_url,
            location=location,
            remote=infer_remote(location, description_text),
            employment_type=employment_type,
            description=description_html,
            posted_at=posted_at,
            source_updated_at=updated_at,
            sponsors_visa=infer_sponsorship(description_text),
            skills=extract_skills(description_text),
        )


# ── Helpers ────────────────────────────────────────────────────────────────


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_workday_date(value: object) -> datetime | None:
    """Detail's `startDate` is usually `YYYY-MM-DD`. Treat it as midnight
    UTC so the 48h window comparison is well-defined."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Some tenants ship full ISO 8601 instead of just a date — try that
    # first; fall back to the date-only form.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_relative_posted(value: object) -> datetime | None:
    """Best-effort parse of list-only relative strings like
    `"Posted 5 Days Ago"`. Returns `None` for anything we can't read —
    `None` will cause the 48h-window filter to drop the row, which is
    the safer outcome (better than inventing a freshness signal)."""
    if not isinstance(value, str):
        return None
    m = _POSTED_REL_RE.search(value)
    if not m:
        return None
    word = m.group("value").lower()
    now = datetime.now(UTC)
    if word == "today":
        return now
    if word == "yesterday":
        return now - timedelta(days=1)
    # Strip a trailing "+" — `30+` means "at least 30", treat as 30.
    digits = word.rstrip("+")
    try:
        days = int(digits)
    except ValueError:
        return None
    return now - timedelta(days=days)
