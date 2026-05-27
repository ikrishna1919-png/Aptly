"""SmartRecruiters public-postings adapter.

API: GET https://api.smartrecruiters.com/v1/companies/{company}/postings
Docs: https://developers.smartrecruiters.com/reference/postingsget

Unlike Greenhouse (one fat list with `content=true`) and Lever (one list
with `descriptionPlain` inline), SmartRecruiters splits posting summaries
from posting bodies:

  LIST   /v1/companies/{company}/postings?limit=100&offset=0
         → { offset, limit, totalFound, content: [summary, summary, …] }
  DETAIL /v1/companies/{company}/postings/{postingId}
         → { id, name, location, applyUrl, jobAd: { sections: { … } }, … }

So `fetch()` walks the list (offset-paginated, hard-capped at
`_MAX_POSTINGS` per company to keep one slow / huge company from
dominating a run), then hits DETAIL per posting to pull the JD text.
A single bad / 404 / slow detail call only drops that posting — the
others still flow through.

No auth needed for published postings.
"""

from __future__ import annotations

import html as html_module
import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

import httpx

from app.services.skills import extract_skills
from app.sources._text import clean_html, infer_remote, infer_sponsorship, strip_html
from app.sources.base import JobSource, NormalizedJob, SourceUnavailable

log = logging.getLogger(__name__)

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"
# Public apply URLs are convention-based when the API omits `applyUrl`.
APPLY_URL_TEMPLATE = "https://jobs.smartrecruiters.com/{company}/{posting_id}"

# Cap to avoid one giant company dominating a run. Versant3-scale boards
# are well under this; larger boards will get the first N.
_MAX_POSTINGS_PER_COMPANY = 200
_PAGE_SIZE = 100  # SmartRecruiters max per page.


class SmartRecruitersSource(JobSource):
    name = "smartrecruiters"

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
        summaries = list(self._iter_summaries(token))
        if not summaries:
            return []

        # Cap defensively in case the list endpoint returns more than the
        # paginator promised.
        summaries = summaries[: self._max_postings]

        jobs: list[NormalizedJob] = []
        for summary in summaries:
            posting_id = str(summary.get("id") or "")
            if not posting_id:
                continue
            try:
                jobs.append(self._fetch_detail(token, posting_id))
            except SourceUnavailable as e:
                # One bad posting must NOT abort the whole company —
                # log loudly and continue. Matches the existing per-board
                # contract documented in run_ingest().
                log.warning("skipping smartrecruiters:%s:%s — %s", token, posting_id, e)
            except (KeyError, ValueError, TypeError) as e:
                log.warning(
                    "skipping smartrecruiters:%s:%s malformed payload — %s",
                    token,
                    posting_id,
                    e,
                )
        return jobs

    # ── List pagination ───────────────────────────────────────────────────

    def _iter_summaries(self, token: str) -> Iterator[dict]:
        url = LIST_URL.format(company=token)
        offset = 0
        yielded = 0
        while yielded < self._max_postings:
            page = self._fetch_list_page(token, url, offset)
            content = page.get("content") or []
            if not isinstance(content, list) or not content:
                return
            for item in content:
                if isinstance(item, dict):
                    yield item
                    yielded += 1
                    if yielded >= self._max_postings:
                        return
            total = _coerce_int(page.get("totalFound"))
            offset += len(content)
            if total is not None and offset >= total:
                return
            # Defensive: if the API misbehaves and returns the same page,
            # bail to avoid an infinite loop.
            if len(content) < self._page_size:
                return

    def _fetch_list_page(self, token: str, url: str, offset: int) -> dict:
        try:
            resp = self._client.get(url, params={"limit": self._page_size, "offset": offset})
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"smartrecruiters:{token} list failed: {e}") from e
        if resp.status_code == 404:
            raise SourceUnavailable(f"smartrecruiters:{token} not found (404)")
        if resp.status_code >= 400:
            raise SourceUnavailable(
                f"smartrecruiters:{token} HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            payload = resp.json()
        except ValueError as e:
            raise SourceUnavailable(f"smartrecruiters:{token} bad JSON: {e}") from e
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
            raise SourceUnavailable(f"smartrecruiters:{token} unexpected list payload shape")
        return payload

    # ── Detail ────────────────────────────────────────────────────────────

    def _fetch_detail(self, token: str, posting_id: str) -> NormalizedJob:
        url = DETAIL_URL.format(company=token, posting_id=posting_id)
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            raise SourceUnavailable(
                f"smartrecruiters:{token}:{posting_id} request failed: {e}"
            ) from e
        if resp.status_code == 404:
            raise SourceUnavailable(f"smartrecruiters:{token}:{posting_id} not found (404)")
        if resp.status_code >= 400:
            raise SourceUnavailable(f"smartrecruiters:{token}:{posting_id} HTTP {resp.status_code}")
        try:
            raw = resp.json()
        except ValueError as e:
            raise SourceUnavailable(f"smartrecruiters:{token}:{posting_id} bad JSON: {e}") from e
        return self._parse(token, raw)

    # ── Normalisation ─────────────────────────────────────────────────────

    def _parse(self, token: str, raw: dict) -> NormalizedJob:
        external_id = str(raw["id"])
        title = str(raw["name"]).strip()

        # Apply URL: prefer the API-supplied applyUrl; fall back to the
        # public-jobs domain convention.
        apply_url = str(raw.get("applyUrl") or "").strip()
        if not apply_url:
            apply_url = APPLY_URL_TEMPLATE.format(company=token, posting_id=external_id)

        location, remote_hint = _extract_location(raw)

        employment_type = _extract_employment_type(raw)
        description_html = _extract_description(raw)
        # Plain-text view drives the heuristics so they don't trip on
        # the HTML tags we now keep in the stored description.
        description_text = strip_html(description_html or "")

        # Prefer explicit remote signal from the API's location.remote
        # field; otherwise fall back to the shared inference helper.
        if remote_hint is None:
            remote = infer_remote(location, description_text)
        else:
            remote = remote_hint

        updated_at = (
            _parse_iso(raw.get("updatedOn"))
            or _parse_iso(raw.get("releasedDate"))
            or _parse_iso(raw.get("createdOn"))
        )
        posted_at = _parse_iso(raw.get("releasedDate")) or _parse_iso(raw.get("createdOn"))

        return NormalizedJob(
            source=self.name,
            external_id=external_id,
            company=token,
            title=title,
            url=apply_url,
            location=location,
            remote=remote,
            employment_type=employment_type,
            description=description_html,
            posted_at=posted_at,
            source_updated_at=updated_at or posted_at or datetime.now(UTC),
            sponsors_visa=infer_sponsorship(description_text),
            skills=extract_skills(description_text),
        )


# ── Helpers ────────────────────────────────────────────────────────────────


def _extract_location(raw: dict) -> tuple[str | None, bool | None]:
    """Pull a single human-readable location string + an explicit remote
    flag if the API provides one. SmartRecruiters' `location` object has
    `city` / `region` / `country` and sometimes a boolean `remote`."""
    loc = raw.get("location")
    if not isinstance(loc, dict):
        return None, None
    parts: list[str] = []
    for key in ("city", "region", "country"):
        value = loc.get(key)
        if value:
            parts.append(str(value).strip())
    location = ", ".join(p for p in parts if p) or None
    remote = loc.get("remote") if isinstance(loc.get("remote"), bool) else None
    return location, remote


def _extract_employment_type(raw: dict) -> str | None:
    et = raw.get("typeOfEmployment")
    if not isinstance(et, dict):
        return None
    label = et.get("label") or et.get("id")
    if not label:
        return None
    # "full-time" → "Full-time" so it matches the Lever / manual-job style.
    text = str(label).strip()
    if "-" in text and text.islower():
        text = text.replace("-", "-").capitalize()
    return text


# Section keys we know contain real JD text. SmartRecruiters jobAds use a
# small fixed vocabulary; anything else is appended at the end for
# completeness.
_KNOWN_SECTIONS = (
    "companyDescription",
    "jobDescription",
    "qualifications",
    "additionalInformation",
)


def _extract_description(raw: dict) -> str | None:
    """Assemble the JD sections into one HTML blob.

    Each section becomes an `<h3>{title}</h3>` + the section's HTML
    body (entity-decoded, tags preserved). The frontend sanitizes +
    renders the result with prose styling, so the headings and any
    list / emphasis structure SmartRecruiters provides show through
    cleanly.
    """
    job_ad = raw.get("jobAd")
    if not isinstance(job_ad, dict):
        return None
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return None

    blocks: list[str] = []

    def add(section_key: str, section: dict) -> None:
        text = section.get("text") if isinstance(section, dict) else None
        if not text:
            return
        title = (section.get("title") if isinstance(section, dict) else None) or section_key
        body = clean_html(str(text))
        if body:
            blocks.append(f"<h3>{html_module.escape(str(title))}</h3>\n{body}")

    # Known sections first, in a stable order.
    for key in _KNOWN_SECTIONS:
        section = sections.get(key)
        if isinstance(section, dict):
            add(key, section)

    # Then anything else SmartRecruiters might add later — preserved so
    # we don't drop signal silently.
    for key, section in sections.items():
        if key in _KNOWN_SECTIONS:
            continue
        if isinstance(section, dict):
            add(key, section)

    return "\n\n".join(blocks) if blocks else None


def _parse_iso(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    # SmartRecruiters uses ISO 8601 with a trailing Z.
    s = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
