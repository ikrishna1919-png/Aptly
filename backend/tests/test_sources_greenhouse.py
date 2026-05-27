from __future__ import annotations

import httpx
import pytest

from app.sources.base import SourceUnavailable
from app.sources.greenhouse import GreenhouseSource


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parses_jobs_and_strips_html():
    payload = {
        "jobs": [
            {
                "id": 101,
                "title": "  Senior Software Engineer  ",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
                "updated_at": "2026-05-25T10:00:00Z",
                "first_published": "2026-05-20T09:00:00Z",
                "location": {"name": "Remote, US"},
                "content": "<p>Build things with <strong>Python</strong> and React.</p>",
            },
            {
                "id": 102,
                "title": "Designer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/102",
                "updated_at": "2026-05-24T08:00:00Z",
                "location": {"name": "New York"},
                "content": "We do not sponsor visas for this role.",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "boards-api.greenhouse.io" in str(request.url)
        assert "acme/jobs" in str(request.url)
        assert request.url.params["content"] == "true"
        return httpx.Response(200, json=payload)

    source = GreenhouseSource(client=_client(handler))
    jobs = list(source.fetch("acme"))

    assert len(jobs) == 2

    j1 = jobs[0]
    assert j1.source == "greenhouse"
    assert j1.external_id == "101"
    assert j1.title == "Senior Software Engineer"
    assert j1.company == "acme"
    assert j1.location == "Remote, US"
    assert j1.remote is True
    assert "Python" in j1.skills
    assert "React" in j1.skills
    # The stored description is real HTML — paragraphs and emphasis tags
    # are preserved so the (sanitized) frontend render keeps the
    # JD's formatting.
    assert "<p>" in (j1.description or "")
    assert "<strong>" in (j1.description or "")
    assert "Python" in (j1.description or "")
    assert j1.source_updated_at.isoformat().startswith("2026-05-25T10:00:00")

    j2 = jobs[1]
    assert j2.sponsors_visa is False
    assert j2.remote is None  # neither remote nor onsite clearly stated


def test_404_raises_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    source = GreenhouseSource(client=_client(handler))
    with pytest.raises(SourceUnavailable):
        list(source.fetch("nonexistent-co"))


def test_500_raises_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    source = GreenhouseSource(client=_client(handler))
    with pytest.raises(SourceUnavailable):
        list(source.fetch("flaky"))


def test_malformed_payload_skipped_per_posting():
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "OK",
                "absolute_url": "https://x",
                "updated_at": "2026-05-25T10:00:00Z",
            },
            {"nope": True},  # missing required fields — should be skipped, not crash
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    jobs = list(GreenhouseSource(client=_client(handler)).fetch("acme"))
    assert len(jobs) == 1
    assert jobs[0].external_id == "1"
