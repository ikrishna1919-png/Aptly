"""The single-user demo candidate.

Phase 4 is intentionally single-user — there are no accounts yet. The
canonical candidate now lives in the `candidates` table (seeded by Alembic
migration 0005) so it's observable in the live DB. This module:

  - holds DEMO_CANDIDATE: the seed payload (also imported by the migration)
  - exposes get_candidate(db): the runtime accessor used by tailor service.
    Reads from the DB; falls back to DEMO_CANDIDATE if the row is missing
    (e.g. fresh test DBs that don't run migrations).

Phase 2 will replace this with real user profiles built from resume parsing.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

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


def get_candidate(db: Session) -> dict[str, Any]:
    """Return the canonical candidate profile.

    Reads the `demo` row from the `candidates` table. Falls back to the
    in-code DEMO_CANDIDATE only when the row doesn't exist — this happens
    in test setups that create the schema via `Base.metadata.create_all`
    without running the seed migration. In production the migration ensures
    a row is always present.
    """
    # Local imports — keep this module importable from migrations without
    # pulling the whole model tree.
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.candidate import DEMO_SLUG, Candidate  # noqa: PLC0415

    row = db.execute(select(Candidate).where(Candidate.slug == DEMO_SLUG)).scalar_one_or_none()
    if row is None:
        return DEMO_CANDIDATE
    return row.profile
