"""SmartRecruiters adapter tests — same shape as the Greenhouse / Lever
tests (httpx.MockTransport-based, no real network)."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.sources.base import SourceUnavailable
from app.sources.smartrecruiters import SmartRecruitersSource


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── Canned API payloads ───────────────────────────────────────────────────


_SUMMARY_ENG = {"id": "abc-1", "name": "Senior Software Engineer"}
_SUMMARY_DES = {"id": "xyz-9", "name": "Product Designer"}

_DETAIL_ENG = {
    "id": "abc-1",
    "name": "Senior Software Engineer",
    "location": {
        "city": "San Francisco",
        "region": "CA",
        "country": "US",
        "remote": False,
    },
    "applyUrl": "https://jobs.smartrecruiters.com/Versant3/abc-1",
    "releasedDate": "2026-05-20T09:00:00Z",
    "updatedOn": "2026-05-25T10:00:00Z",
    "createdOn": "2026-05-18T09:00:00Z",
    "typeOfEmployment": {"id": "full-time"},
    "jobAd": {
        "sections": {
            "jobDescription": {
                "title": "About the role",
                "text": (
                    "<p>Build distributed systems with "
                    "<strong>Python</strong> and Kafka. Deploy on AWS.</p>"
                ),
            },
            "qualifications": {
                "title": "Requirements",
                "text": "<ul><li>5+ years backend</li><li>PostgreSQL</li></ul>",
            },
        }
    },
}

_DETAIL_DES = {
    "id": "xyz-9",
    "name": "Product Designer",
    "location": {"city": "Remote", "country": "US", "remote": True},
    # No applyUrl — adapter must fall back to the public-jobs URL.
    "releasedDate": "2026-05-24T08:00:00Z",
    "typeOfEmployment": {"id": "part-time"},
    "jobAd": {
        "sections": {
            "jobDescription": {
                "title": "Role",
                "text": "Design our product surface. We do not sponsor visas for this role.",
            }
        }
    },
}


# ── Happy path ─────────────────────────────────────────────────────────────


def test_parses_list_then_detail_and_normalises_fields():
    list_payload = {
        "offset": 0,
        "limit": 100,
        "totalFound": 2,
        "content": [_SUMMARY_ENG, _SUMMARY_DES],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "api.smartrecruiters.com" in url
        assert "/Versant3/postings" in url
        if "/postings/abc-1" in url:
            return httpx.Response(200, json=_DETAIL_ENG)
        if "/postings/xyz-9" in url:
            return httpx.Response(200, json=_DETAIL_DES)
        # List call
        params = parse_qs(request.url.query.decode())
        assert params.get("limit") == ["100"]
        assert params.get("offset") == ["0"]
        return httpx.Response(200, json=list_payload)

    source = SmartRecruitersSource(client=_client(handler))
    jobs = list(source.fetch("Versant3"))
    assert len(jobs) == 2

    eng = jobs[0]
    assert eng.source == "smartrecruiters"
    assert eng.external_id == "abc-1"
    assert eng.company == "Versant3"
    assert eng.title == "Senior Software Engineer"
    assert eng.url == "https://jobs.smartrecruiters.com/Versant3/abc-1"
    assert eng.location == "San Francisco, CA, US"
    # location.remote=False is an explicit signal — must win over the
    # textual heuristic.
    assert eng.remote is False
    assert eng.employment_type and eng.employment_type.lower().startswith("full")
    # updatedOn wins over releasedDate for the source_updated_at field.
    assert eng.source_updated_at.isoformat().startswith("2026-05-25T10:00:00")
    # JD is stored as HTML: section heading + the body's tags survive.
    # The frontend sanitizes + renders with prose styling.
    assert "<h3>About the role</h3>" in (eng.description or "")
    assert "<p>" in (eng.description or "")
    assert "<strong>Python</strong>" in (eng.description or "")
    # Skills extraction operates on the plain-text view, so tag noise
    # doesn't pollute the skill list.
    assert "Python" in eng.skills
    assert "Kafka" in eng.skills
    assert "AWS" in eng.skills

    des = jobs[1]
    # No applyUrl on the payload → construct from the convention.
    assert des.url == "https://jobs.smartrecruiters.com/Versant3/xyz-9"
    # Explicit remote=True via location.remote.
    assert des.remote is True
    assert des.sponsors_visa is False


# ── Pagination ─────────────────────────────────────────────────────────────


def test_walks_multiple_pages_until_totalfound_reached():
    """Two-page response. The adapter must follow pagination until
    `totalFound` is consumed and not loop forever."""
    page_size = 2  # smaller so we don't have to fabricate 100 summaries
    page1 = {
        "offset": 0,
        "limit": page_size,
        "totalFound": 3,
        "content": [
            {"id": "a", "name": "A"},
            {"id": "b", "name": "B"},
        ],
    }
    page2 = {
        "offset": 2,
        "limit": page_size,
        "totalFound": 3,
        "content": [{"id": "c", "name": "C"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/postings/" in url:
            # Trivial detail — just enough to parse.
            posting_id = url.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": posting_id,
                    "name": posting_id.upper(),
                    "releasedDate": "2026-05-25T00:00:00Z",
                    "jobAd": {"sections": {"jobDescription": {"text": "x"}}},
                },
            )
        offset = parse_qs(request.url.query.decode()).get("offset", ["0"])[0]
        return httpx.Response(200, json=page1 if offset == "0" else page2)

    source = SmartRecruitersSource(client=_client(handler), page_size=page_size)
    jobs = list(source.fetch("Versant3"))
    assert [j.external_id for j in jobs] == ["a", "b", "c"]


def test_respects_max_postings_per_company():
    big = {
        "offset": 0,
        "limit": 100,
        "totalFound": 1000,  # API claims a lot more than we'd allow
        "content": [{"id": str(i), "name": f"Role {i}"} for i in range(100)],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/postings/" in str(request.url):
            posting_id = str(request.url).rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": posting_id,
                    "name": "X",
                    "releasedDate": "2026-05-25T00:00:00Z",
                    "jobAd": {"sections": {"jobDescription": {"text": "x"}}},
                },
            )
        return httpx.Response(200, json=big)

    # Cap to 5 — adapter must stop fetching detail after 5, not all 100.
    source = SmartRecruitersSource(client=_client(handler), max_postings_per_company=5)
    jobs = list(source.fetch("Versant3"))
    assert len(jobs) == 5


# ── Failure isolation ──────────────────────────────────────────────────────


def test_404_on_list_raises_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    source = SmartRecruitersSource(client=_client(handler))
    with pytest.raises(SourceUnavailable):
        list(source.fetch("nope"))


def test_500_on_list_raises_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(SourceUnavailable):
        list(SmartRecruitersSource(client=_client(handler)).fetch("flaky"))


def test_unexpected_list_payload_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": "not an array"})

    with pytest.raises(SourceUnavailable):
        list(SmartRecruitersSource(client=_client(handler)).fetch("acme"))


def test_one_failing_detail_does_not_abort_the_company():
    """The whole point of per-posting try/except: if one detail call
    404s (posting taken down between LIST and DETAIL, or any other
    upstream hiccup), the other postings still flow through."""
    list_payload = {
        "offset": 0,
        "limit": 100,
        "totalFound": 2,
        "content": [{"id": "good", "name": "G"}, {"id": "bad", "name": "B"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/postings/good" in url:
            return httpx.Response(
                200,
                json={
                    "id": "good",
                    "name": "Good Posting",
                    "releasedDate": "2026-05-25T00:00:00Z",
                    "jobAd": {"sections": {"jobDescription": {"text": "all good"}}},
                },
            )
        if "/postings/bad" in url:
            return httpx.Response(404, text="gone")
        return httpx.Response(200, json=list_payload)

    jobs = list(SmartRecruitersSource(client=_client(handler)).fetch("Versant3"))
    assert [j.external_id for j in jobs] == ["good"]


def test_malformed_detail_payload_skipped():
    """Detail payload missing required fields (`id` / `name`) is skipped
    — same per-posting tolerance as Greenhouse and Lever."""
    list_payload = {
        "offset": 0,
        "limit": 100,
        "totalFound": 2,
        "content": [{"id": "ok", "name": "OK"}, {"id": "broken", "name": "X"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/postings/ok" in url:
            return httpx.Response(
                200,
                json={
                    "id": "ok",
                    "name": "OK Posting",
                    "releasedDate": "2026-05-25T00:00:00Z",
                    "jobAd": {"sections": {"jobDescription": {"text": "ok"}}},
                },
            )
        if "/postings/broken" in url:
            # Missing required `name` field — should be caught.
            return httpx.Response(200, json={"id": "broken"})
        return httpx.Response(200, json=list_payload)

    jobs = list(SmartRecruitersSource(client=_client(handler)).fetch("Versant3"))
    assert [j.external_id for j in jobs] == ["ok"]
