"""Workday adapter tests — httpx.MockTransport-based, no real network.

Same shape as the Greenhouse / Lever / SmartRecruiters tests:

  * Happy path: list → detail per posting → normalized shape, with the
    description stored as HTML and the plain-text view driving the
    skill / remote heuristics.
  * Pagination: walks past `total` correctly, doesn't infinite-loop on
    a misbehaving API that returns the same page twice.
  * Date handling: prefers detail's `startDate`; falls back to the
    list's relative `"Posted N Days Ago"` string; leaves `None` for
    anything we can't read.
  * Failure isolation: a single bad detail (404) drops only that
    posting; a list-level error raises `SourceUnavailable` so the
    orchestrator records `last_status='error'` on the row.
  * Token parsing: malformed `tenant:dc:site` triples raise
    `SourceUnavailable` rather than crashing the run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.sources.base import SourceUnavailable
from app.sources.workday import WorkdaySource


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


_GM_TOKEN = "generalmotors:wd5:Careers_GM"


# ── Canned API payloads ────────────────────────────────────────────────────


_SUMMARY_ENG = {
    "title": "Senior Software Engineer",
    "externalPath": "/job/Detroit/Senior-Software-Engineer_JR-100",
    "locationsText": "Detroit, MI",
    "postedOn": "Posted 3 Days Ago",
    "bulletFields": ["JR-100"],
}

_SUMMARY_DES = {
    "title": "Product Designer",
    "externalPath": "/job/Remote/Product-Designer_JR-200",
    "locationsText": "Remote - US",
    "postedOn": "Posted Today",
    "bulletFields": ["JR-200"],
}

_DETAIL_ENG = {
    "jobPostingInfo": {
        "id": "JR-100",
        "jobReqId": "JR-100",
        "title": "Senior Software Engineer",
        "externalUrl": "https://generalmotors.wd5.myworkdayjobs.com/en-US/Careers_GM/job/Senior-Software-Engineer_JR-100",
        "jobDescription": (
            "<p>Build distributed systems with <strong>Python</strong> and "
            "Kafka. Deploy on AWS.</p>"
        ),
        "location": "Detroit, MI",
        "startDate": "2026-05-26",
        "timeType": "Full time",
    }
}

_DETAIL_DES = {
    "jobPostingInfo": {
        "id": "JR-200",
        "title": "Product Designer",
        # No externalUrl — adapter must fall back to the public-jobs URL.
        "jobDescription": (
            "<p>Design the next-gen interior dashboard. We do not sponsor visas for this role.</p>"
        ),
        "location": "Remote - US",
        "startDate": "2026-05-27",
        "timeType": "Full time",
    }
}


# ── Happy path ─────────────────────────────────────────────────────────────


def test_parses_list_then_detail_and_normalises_fields():
    list_payload = {
        "total": 2,
        "jobPostings": [_SUMMARY_ENG, _SUMMARY_DES],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "generalmotors.wd5.myworkdayjobs.com" in url
        if request.method == "POST":
            # List endpoint — body carries limit/offset.
            assert "/wday/cxs/generalmotors/Careers_GM/jobs" in url
            body = json.loads(request.content)
            assert body["limit"] == 20
            assert body["offset"] == 0
            return httpx.Response(200, json=list_payload)
        # Detail GET.
        if "JR-100" in url:
            return httpx.Response(200, json=_DETAIL_ENG)
        if "JR-200" in url:
            return httpx.Response(200, json=_DETAIL_DES)
        return httpx.Response(404)

    jobs = list(WorkdaySource(client=_client(handler)).fetch(_GM_TOKEN))
    assert len(jobs) == 2

    eng = jobs[0]
    assert eng.source == "workday"
    assert eng.external_id == "JR-100"
    assert eng.company == "generalmotors"
    assert eng.title == "Senior Software Engineer"
    assert "myworkdayjobs.com" in eng.url
    assert eng.location == "Detroit, MI"
    assert eng.employment_type and eng.employment_type.lower().startswith("full")
    # JD stored as HTML — the frontend sanitizes + renders it. Tag
    # noise must NOT pollute the skill / location signal: the
    # heuristics consume the plain-text view internally.
    assert "<p>" in (eng.description or "")
    assert "<strong>Python</strong>" in (eng.description or "")
    assert "Python" in eng.skills
    assert "Kafka" in eng.skills
    assert "AWS" in eng.skills
    # startDate from the detail endpoint wins.
    assert eng.source_updated_at.isoformat().startswith("2026-05-26")

    des = jobs[1]
    # No externalUrl on the detail payload → construct from the convention.
    assert des.url == (
        "https://generalmotors.wd5.myworkdayjobs.com/en-US/Careers_GM"
        "/job/Remote/Product-Designer_JR-200"
    )
    assert des.sponsors_visa is False


# ── Pagination ─────────────────────────────────────────────────────────────


def test_walks_multiple_pages_until_total_reached():
    page_size = 2
    page1 = {
        "total": 3,
        "jobPostings": [
            {"title": "A", "externalPath": "/job/A", "postedOn": "Posted Today"},
            {"title": "B", "externalPath": "/job/B", "postedOn": "Posted Yesterday"},
        ],
    }
    page2 = {
        "total": 3,
        "jobPostings": [{"title": "C", "externalPath": "/job/C", "postedOn": "Posted 1 Day Ago"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(200, json=page1 if body["offset"] == 0 else page2)
        # Trivial detail — just enough to parse.
        ext_id = url.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "jobPostingInfo": {
                    "id": ext_id,
                    "title": ext_id.upper(),
                    "jobDescription": "x",
                    "startDate": "2026-05-25",
                }
            },
        )

    source = WorkdaySource(client=_client(handler), page_size=page_size)
    jobs = list(source.fetch(_GM_TOKEN))
    assert [j.external_id for j in jobs] == ["A", "B", "C"]


def test_respects_max_postings_per_company():
    big = {
        "total": 1000,  # API claims a lot more than we'd allow
        "jobPostings": [
            {"title": f"R{i}", "externalPath": f"/job/{i}", "postedOn": "Posted Today"}
            for i in range(20)
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=big)
        ext_id = str(request.url).rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "jobPostingInfo": {
                    "id": ext_id,
                    "title": "X",
                    "jobDescription": "x",
                    "startDate": "2026-05-25",
                }
            },
        )

    source = WorkdaySource(client=_client(handler), max_postings_per_company=5)
    jobs = list(source.fetch(_GM_TOKEN))
    assert len(jobs) == 5


# ── Date handling ──────────────────────────────────────────────────────────


def test_falls_back_to_relative_posted_when_start_date_missing():
    """No `startDate` on the detail → use the list's `postedOn`."""
    list_payload = {
        "total": 1,
        "jobPostings": [
            {
                "title": "Old role",
                "externalPath": "/job/old",
                "postedOn": "Posted 5 Days Ago",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=list_payload)
        # No startDate / externalUrl — adapter must derive from list.
        return httpx.Response(
            200,
            json={
                "jobPostingInfo": {
                    "id": "old",
                    "title": "Old role",
                    "jobDescription": "x",
                }
            },
        )

    jobs = list(WorkdaySource(client=_client(handler)).fetch(_GM_TOKEN))
    assert len(jobs) == 1
    when = jobs[0].source_updated_at
    assert when is not None
    delta = datetime.now(UTC) - when
    # "Posted 5 Days Ago" → approximately now - 5d (allow a wide
    # tolerance for test runtime).
    assert timedelta(days=4) <= delta <= timedelta(days=6)


def test_unparseable_date_leaves_source_updated_at_none():
    """If neither detail.startDate nor list.postedOn yields a date,
    leave the row dateless so the 48h filter drops it cleanly."""
    list_payload = {
        "total": 1,
        "jobPostings": [
            {"title": "x", "externalPath": "/job/x", "postedOn": "(unknown)"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=list_payload)
        return httpx.Response(
            200,
            json={"jobPostingInfo": {"id": "x", "title": "X", "jobDescription": "x"}},
        )

    jobs = list(WorkdaySource(client=_client(handler)).fetch(_GM_TOKEN))
    assert jobs[0].source_updated_at is None


# ── Failure isolation ──────────────────────────────────────────────────────


def test_404_on_list_raises_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    with pytest.raises(SourceUnavailable):
        list(WorkdaySource(client=_client(handler)).fetch(_GM_TOKEN))


def test_500_on_list_raises_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(SourceUnavailable):
        list(WorkdaySource(client=_client(handler)).fetch(_GM_TOKEN))


def test_unexpected_list_payload_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": "not an array"})

    with pytest.raises(SourceUnavailable):
        list(WorkdaySource(client=_client(handler)).fetch(_GM_TOKEN))


def test_one_failing_detail_does_not_abort_the_company():
    """One 404'd posting must NOT take down the rest of the board —
    same per-posting contract as Greenhouse / Lever / SR."""
    list_payload = {
        "total": 2,
        "jobPostings": [
            {"title": "Good", "externalPath": "/job/good", "postedOn": "Posted Today"},
            {"title": "Bad", "externalPath": "/job/bad", "postedOn": "Posted Today"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json=list_payload)
        if "/good" in url:
            return httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "id": "good",
                        "title": "Good Posting",
                        "jobDescription": "Good content",
                        "startDate": "2026-05-26",
                    }
                },
            )
        if "/bad" in url:
            return httpx.Response(404, text="gone")
        return httpx.Response(404)

    jobs = list(WorkdaySource(client=_client(handler)).fetch(_GM_TOKEN))
    assert [j.external_id for j in jobs] == ["good"]


def test_malformed_detail_payload_skipped():
    """Detail payload missing `jobPostingInfo` is skipped, not fatal."""
    list_payload = {
        "total": 2,
        "jobPostings": [
            {"title": "OK", "externalPath": "/job/ok", "postedOn": "Posted Today"},
            {"title": "X", "externalPath": "/job/broken", "postedOn": "Posted Today"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json=list_payload)
        if "/ok" in url:
            return httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "id": "ok",
                        "title": "OK",
                        "jobDescription": "x",
                        "startDate": "2026-05-26",
                    }
                },
            )
        # No `jobPostingInfo` key — adapter must skip not crash.
        return httpx.Response(200, json={"id": "broken"})

    jobs = list(WorkdaySource(client=_client(handler)).fetch(_GM_TOKEN))
    assert [j.external_id for j in jobs] == ["ok"]


# ── Token parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_token",
    [
        "",
        "onlytenant",
        "tenant:dc",  # missing site
        "tenant::site",  # empty dc
        ":dc:site",  # empty tenant
        "tenant:dc:site:extra",  # too many parts
    ],
)
def test_malformed_token_raises_source_unavailable(bad_token):
    """The orchestrator records this as `last_error` and moves on —
    same blast radius as a 404. We don't raise a plain ValueError
    because that would land in the unexpected-exception branch."""
    source = WorkdaySource(client=_client(lambda req: httpx.Response(500)))
    with pytest.raises(SourceUnavailable):
        list(source.fetch(bad_token))
