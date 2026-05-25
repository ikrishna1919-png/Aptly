from __future__ import annotations

import httpx
import pytest

from app.sources.base import SourceUnavailable
from app.sources.lever import LeverSource


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parses_array_payload_and_uses_apply_url():
    payload = [
        {
            "id": "abc-123",
            "text": "Backend Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/abc-123",
            "applyUrl": "https://jobs.lever.co/acme/abc-123/apply",
            "createdAt": 1716200000000,
            "updatedAt": 1716300000000,
            "categories": {
                "location": "Remote - US",
                "team": "Engineering",
                "commitment": "Full-time",
            },
            "workplaceType": "remote",
            "descriptionPlain": "Work with Go and Kubernetes. We will sponsor visa.",
        },
        {
            "id": "xyz-999",
            "text": "Sales Lead",
            "hostedUrl": "https://jobs.lever.co/acme/xyz-999",
            "createdAt": 1716250000000,
            "categories": {"location": "New York", "commitment": "Full-time"},
            "workplaceType": "onsite",
            "description": "<p>Lead our sales team.</p>",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.lever.co" in str(request.url)
        assert request.url.params["mode"] == "json"
        return httpx.Response(200, json=payload)

    jobs = list(LeverSource(client=_client(handler)).fetch("acme"))
    assert len(jobs) == 2

    j1 = jobs[0]
    assert j1.source == "lever"
    assert j1.external_id == "abc-123"
    assert j1.url.endswith("/apply")  # prefer applyUrl
    assert j1.remote is True
    assert j1.employment_type == "Full-time"
    assert j1.sponsors_visa is True
    assert "Go" in j1.skills
    assert "Kubernetes" in j1.skills

    j2 = jobs[1]
    assert j2.url == "https://jobs.lever.co/acme/xyz-999"  # falls back to hostedUrl
    assert j2.remote is False  # workplaceType=onsite
    assert "<p>" not in (j2.description or "")


def test_404_raises_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(SourceUnavailable):
        list(LeverSource(client=_client(handler)).fetch("nope"))


def test_unexpected_shape_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})  # Lever returns array, not object

    with pytest.raises(SourceUnavailable):
        list(LeverSource(client=_client(handler)).fetch("acme"))
