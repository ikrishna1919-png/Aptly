"""Ashby adapter tests — httpx.MockTransport-driven, no real network.

Same shape as the Greenhouse / Lever / SmartRecruiters / Workday
tests: pin the happy-path mapping, the failure-isolation contract,
the date handling, and the native-async wiring.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.sources.ashby import AshbySource
from app.sources.base import SourceUnavailable


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── Canned payloads ────────────────────────────────────────────────────────


_JOB_ENG = {
    "id": "abc-eng-1",
    "title": "Senior Software Engineer",
    "departmentName": "Engineering",
    "teamName": "Platform",
    "locationName": "Remote",
    "employmentType": "FullTime",
    "publishedDate": "2026-05-20T09:00:00Z",
    "updatedAt": "2026-05-25T10:00:00Z",
    "jobUrl": "https://jobs.ashbyhq.com/linear/abc-eng-1",
    "applyUrl": "https://jobs.ashbyhq.com/linear/abc-eng-1/application",
    "descriptionHtml": "<p>Build with <strong>Python</strong> and Kafka. Deploy on AWS.</p>",
    "descriptionPlain": "Build with Python and Kafka. Deploy on AWS.",
    "isRemote": True,
}

_JOB_DES = {
    "id": "xyz-des-9",
    "title": "Product Designer",
    "locationName": "New York, NY",
    "employmentType": "PartTime",
    "publishedDate": "2026-05-24T08:00:00Z",
    "jobUrl": "https://jobs.ashbyhq.com/linear/xyz-des-9",
    # No applyUrl — adapter must fall back to jobUrl.
    "descriptionHtml": "<p>Design our brand. We do not sponsor visas for this role.</p>",
    "descriptionPlain": "Design our brand. We do not sponsor visas for this role.",
    "isRemote": False,
}


# ── Happy path ─────────────────────────────────────────────────────────────


def test_parses_jobs_and_stores_html_description():
    payload = {"apiVersion": "1", "jobs": [_JOB_ENG, _JOB_DES]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.ashbyhq.com" in str(request.url)
        assert "/posting-api/job-board/linear" in str(request.url)
        assert request.url.params["includeCompensation"] == "true"
        return httpx.Response(200, json=payload)

    jobs = list(AshbySource(client=_client(handler)).fetch("linear"))
    assert len(jobs) == 2

    eng = jobs[0]
    assert eng.source == "ashby"
    assert eng.external_id == "abc-eng-1"
    assert eng.company == "linear"
    assert eng.title == "Senior Software Engineer"
    # Prefer applyUrl over jobUrl.
    assert eng.url.endswith("/application")
    assert eng.location == "Remote"
    # `isRemote=true` is taken at face value — no text inference needed.
    assert eng.remote is True
    assert eng.employment_type == "Full-time"
    # JD stored as HTML; heuristics consumed the plain-text view, so
    # the skill list isn't polluted by tag noise.
    assert "<p>" in (eng.description or "")
    assert "<strong>Python</strong>" in (eng.description or "")
    assert "Python" in eng.skills
    assert "Kafka" in eng.skills
    assert "AWS" in eng.skills
    # updatedAt wins over publishedDate when both present.
    assert eng.source_updated_at.isoformat().startswith("2026-05-25T10:00:00")

    des = jobs[1]
    # No applyUrl on this one → fallback to jobUrl.
    assert des.url == "https://jobs.ashbyhq.com/linear/xyz-des-9"
    assert des.remote is False
    assert des.employment_type == "Part-time"
    assert des.sponsors_visa is False


def test_employment_type_normalisation_round_trip():
    """Ashby returns CamelCase enums (`FullTime`, `Contract`, `Intern`);
    other adapters store user-facing strings (`Full-time`, `Part-time`).
    The adapter normalises so the feed is consistent."""
    cases = [
        ("FullTime", "Full-time"),
        ("PartTime", "Part-time"),
        ("Contract", "Contract"),
        ("Intern", "Intern"),
        ("Internship", "Intern"),
        ("Temporary", "Temporary"),
        # Unknown values pass through as-is.
        ("Apprenticeship", "Apprenticeship"),
    ]
    for raw, expected in cases:
        payload = {
            "apiVersion": "1",
            "jobs": [{**_JOB_ENG, "employmentType": raw, "id": f"job-{raw}"}],
        }
        jobs = list(
            AshbySource(client=_client(lambda r, p=payload: httpx.Response(200, json=p))).fetch("x")
        )
        assert jobs[0].employment_type == expected, raw


def test_isremote_false_overrides_text_hints():
    """`isRemote=false` is a structured signal — must win even if the
    description mentions "remote work" in some peripheral sentence."""
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                **_JOB_ENG,
                "isRemote": False,
                "descriptionPlain": "We have a remote-friendly culture.",
                "descriptionHtml": "<p>We have a remote-friendly culture.</p>",
            }
        ],
    }
    jobs = list(AshbySource(client=_client(lambda r: httpx.Response(200, json=payload))).fetch("x"))
    assert jobs[0].remote is False


def test_falls_back_to_text_inference_when_isremote_missing():
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                **_JOB_ENG,
                "isRemote": None,
                "locationName": "Remote",
                "descriptionPlain": "fully remote",
                "descriptionHtml": "<p>fully remote</p>",
            }
        ],
    }
    jobs = list(AshbySource(client=_client(lambda r: httpx.Response(200, json=payload))).fetch("x"))
    assert jobs[0].remote is True


# ── Failure isolation ──────────────────────────────────────────────────────


def test_404_raises_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    with pytest.raises(SourceUnavailable):
        list(AshbySource(client=_client(handler)).fetch("nope"))


def test_500_raises_source_unavailable():
    with pytest.raises(SourceUnavailable):
        list(AshbySource(client=_client(lambda r: httpx.Response(500, text="boom"))).fetch("flaky"))


def test_unexpected_payload_raises():
    # No `jobs` array.
    with pytest.raises(SourceUnavailable):
        list(
            AshbySource(client=_client(lambda r: httpx.Response(200, json={"version": 1}))).fetch(
                "acme"
            )
        )


def test_malformed_posting_skipped_not_fatal():
    """One missing-field posting drops only that posting — the rest of
    the board still ingests."""
    payload = {
        "apiVersion": "1",
        "jobs": [
            _JOB_ENG,
            # Missing required `title`.
            {"id": "broken-1", "jobUrl": "https://x/y"},
            _JOB_DES,
        ],
    }
    jobs = list(AshbySource(client=_client(lambda r: httpx.Response(200, json=payload))).fetch("x"))
    assert [j.external_id for j in jobs] == ["abc-eng-1", "xyz-des-9"]


# ── Native-async wiring ────────────────────────────────────────────────────


def test_async_fetch_uses_supplied_async_client():
    """`fetch_async` must route through the orchestrator's shared
    `httpx.AsyncClient` (so connection pooling works across boards)."""
    payload = {"apiVersion": "1", "jobs": [_JOB_ENG]}
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    source = AshbySource()
    try:

        async def go():
            async with httpx.AsyncClient(transport=transport) as ac:
                return list(await source.fetch_async("linear", async_client=ac))

        jobs = asyncio.run(go())
    finally:
        source.close()

    assert len(jobs) == 1
    assert "api.ashbyhq.com" in calls[0]
    assert "/job-board/linear" in calls[0]


def test_async_404_propagates_as_source_unavailable():
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    source = AshbySource()
    try:

        async def go():
            async with httpx.AsyncClient(transport=transport) as ac:
                await source.fetch_async("missing", async_client=ac)

        with pytest.raises(SourceUnavailable):
            asyncio.run(go())
    finally:
        source.close()
