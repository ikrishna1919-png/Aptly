"""The single-user demo candidate.

Phase 4 is intentionally single-user — there are no accounts yet, so the
tailoring endpoints all run against this hardcoded profile. Phase 2 will
introduce real user profiles built from resume parsing.

Keep this representative: a real-looking 7-year senior software-engineer
profile makes the analyze + generate prompts behave realistically end-to-end.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

DEMO_CANDIDATE: dict[str, Any] = {
    "name": "Alex Rivera",
    "headline": "Senior Software Engineer",
    "email": "alex.rivera@example.com",
    "phone": "+1 (555) 123-4567",
    "location": "San Francisco, CA (open to remote)",
    "links": {
        "linkedin": "linkedin.com/in/alex-rivera-demo",
        "github": "github.com/alex-rivera-demo",
    },
    "summary": (
        "Senior software engineer with 7+ years building backend services and "
        "developer-facing platforms. Comfortable across Python, TypeScript, and "
        "distributed systems; led migrations that cut infra cost and improved "
        "reliability. Have shipped both 0-to-1 features and large refactors."
    ),
    "skills": [
        "Python",
        "TypeScript",
        "FastAPI",
        "Django",
        "Node.js",
        "React",
        "Next.js",
        "PostgreSQL",
        "Redis",
        "Kafka",
        "AWS",
        "Kubernetes",
        "Docker",
        "Terraform",
        "CI/CD",
        "SQL",
        "REST",
        "GraphQL",
        "gRPC",
        "Distributed systems",
        "Observability",
    ],
    "experience": [
        {
            "company": "Forge Labs",
            "title": "Senior Software Engineer",
            "location": "San Francisco, CA",
            "start": "2023-02",
            "end": "Present",
            "bullets": [
                "Led the migration of a monolithic billing service to event-driven "
                "microservices on Kafka; reduced peak p95 latency from 480ms to 110ms.",
                "Designed an internal feature-flag platform (FastAPI + Postgres) "
                "adopted by 6 product teams; ~3,000 flags evaluated per second.",
                "Owned on-call rotation for the platform; reduced page volume 60% "
                "by adding SLO-driven dashboards and rewriting noisy alerts.",
                "Mentored 3 mid-level engineers through promotion to senior.",
            ],
        },
        {
            "company": "Northwind Analytics",
            "title": "Software Engineer",
            "location": "Remote",
            "start": "2020-06",
            "end": "2023-01",
            "bullets": [
                "Built the data ingestion pipeline (Python, Airflow, Snowflake) "
                "processing 4B events/day from 200+ customer integrations.",
                "Shipped the customer-facing query API (Django + GraphQL) that "
                "became the company's top-grossing product feature.",
                "Reduced AWS spend ~28% by right-sizing workloads and introducing "
                "spot fleets for batch jobs.",
            ],
        },
        {
            "company": "Beacon Health",
            "title": "Software Engineer",
            "location": "Boston, MA",
            "start": "2018-07",
            "end": "2020-05",
            "bullets": [
                "Implemented HL7/FHIR ingestion service in Python; integrated with "
                "9 hospital EMRs.",
                "Wrote the first integration-test harness for the platform; raised "
                "deploy confidence and cut rollbacks roughly in half.",
            ],
        },
    ],
    "education": [
        {
            "school": "Carnegie Mellon University",
            "degree": "B.S. Computer Science",
            "location": "Pittsburgh, PA",
            "graduation": "2018",
        }
    ],
}


def candidate_fingerprint(candidate: dict[str, Any] | None = None) -> str:
    """Stable SHA-256 of the candidate profile. Used to invalidate cached
    analyses when the candidate changes — same job, different candidate
    means a different cache entry."""
    payload = json.dumps(candidate or DEMO_CANDIDATE, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()
