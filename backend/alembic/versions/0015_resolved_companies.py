"""seed resolved companies from operator triage

Revision ID: 0015_resolved_companies
Revises: 0014_bulk_load_ats_tokens
Create Date: 2026-05-28

Adds the next batch of companies the operator resolved from job-URL
triage into the existing `sources` table. No new adapter code — every
token below maps to an existing supported source type
(greenhouse / lever / smartrecruiters / ashby / workday). Auto-prune
handles any that don't resolve on the next ingest pass.

One notable Workday entry uses the alternate `myworkdaysite.com` host
that some tenants are served from — the adapter learned to handle
that variant in this PR (see migration message + `app.sources.workday`).

Idempotent: `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING` keyed on
the existing `(source_type, token)` unique constraint.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op

revision: str = "0015_resolved_companies"
down_revision: str | None = "0014_bulk_load_ats_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.0015_resolved_companies")


# Per-platform resolved sources. Each tuple is (token, display_name).
# Tokens follow the per-adapter shape rules:
#   * Greenhouse / Lever / Ashby — lowercase slug.
#   * SmartRecruiters            — case-sensitive identifier.
#   * Workday                    — `tenant:dc:site` OR
#                                  `tenant:dc:site:host` for tenants
#                                  served from the `myworkdaysite.com`
#                                  variant rather than the default
#                                  `myworkdayjobs.com`.

GREENHOUSE = (
    ("clear", "CLEAR"),
    ("shift4", "Shift4"),
    ("robinhood", "Robinhood"),
    ("evolutioncloudservicesevocs", "Evolution Cloud Services"),
    ("andurilindustries", "Anduril Industries"),
    ("fglife", "F&G Life"),
)

LEVER = (
    ("zeta", "Zeta"),
    ("veeva", "Veeva"),
)

SMARTRECRUITERS = (("Socotec", "Socotec"),)

ASHBY = (("sentilink", "SentiLink"),)

# Workday: tenant:dc:site triples (default `myworkdayjobs.com` host)
# UNLESS the company is hosted on `myworkdaysite.com`, in which case
# the 4th component pins it explicitly. RLI Corp falls into that
# bucket — the adapter parses both shapes.
WORKDAY = (
    ("mpc:wd1:MPCCareers", "Marathon Petroleum"),
    ("copart:wd12:Copart", "Copart"),
    ("bcbst:wd1:External", "BlueCross BlueShield of Tennessee"),
    ("healthcare:wd1:Search", "Health Care Service Corporation"),
    ("geico:wd1:External", "GEICO"),
    ("guidehouse:wd1:External", "Guidehouse"),
    ("wintrust:wd1:Search", "Wintrust"),
    ("osv-cci:wd1:CCICareers", "CCI"),
    ("pennmutual:wd1:_penn-careers", "Penn Mutual"),
    ("meredith:wd5:EXT", "Meredith"),
    ("oldrepublic:wd1:oldrepublictitle", "Old Republic Title"),
    # `:myworkdaysite.com` pins the alternate Workday host — the
    # adapter parses the 4-component form and substitutes the host
    # into its URL templates.
    ("rlicorp:wd1:RLI_Corp_Careers:myworkdaysite.com", "RLI Corp"),
)


def _rows() -> dict[str, list[dict]]:
    return {
        "greenhouse": [
            {"source_type": "greenhouse", "token": t, "display_name": n, "location": None}
            for t, n in GREENHOUSE
        ],
        "lever": [
            {"source_type": "lever", "token": t, "display_name": n, "location": None}
            for t, n in LEVER
        ],
        "smartrecruiters": [
            {"source_type": "smartrecruiters", "token": t, "display_name": n, "location": None}
            for t, n in SMARTRECRUITERS
        ],
        "ashby": [
            {"source_type": "ashby", "token": t, "display_name": n, "location": None}
            for t, n in ASHBY
        ],
        "workday": [
            {"source_type": "workday", "token": t, "display_name": n, "location": None}
            for t, n in WORKDAY
        ],
    }


def upgrade() -> None:
    bind = op.get_bind()
    by_platform = _rows()

    sources = sa.table(
        "sources",
        sa.column("source_type", sa.String),
        sa.column("token", sa.String),
        sa.column("display_name", sa.String),
        sa.column("location", sa.String),
    )

    dialect = bind.dialect.name
    for source_type, rows in by_platform.items():
        existing = bind.execute(
            sa.text("SELECT COUNT(*) FROM sources WHERE source_type = :st"),
            {"st": source_type},
        ).scalar_one()

        if rows:
            if dialect == "postgresql":
                stmt = (
                    pg_insert(sources)
                    .values(rows)
                    .on_conflict_do_nothing(index_elements=["source_type", "token"])
                )
                bind.execute(stmt)
            else:
                bind.execute(
                    sa.text(
                        "INSERT OR IGNORE INTO sources "
                        "(source_type, token, display_name, location) "
                        "VALUES (:source_type, :token, :display_name, :location)"
                    ),
                    rows,
                )

        after = bind.execute(
            sa.text("SELECT COUNT(*) FROM sources WHERE source_type = :st"),
            {"st": source_type},
        ).scalar_one()
        log.info(
            "resolved-seed %s: found=%d inserted=%d already_existed=%d total_after=%d",
            source_type,
            len(rows),
            int(after - existing),
            int(len(rows) - (after - existing)),
            int(after),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for source_type, rows in _rows().items():
        tokens = [r["token"] for r in rows]
        if not tokens:
            continue
        placeholders = ", ".join(f":t{i}" for i in range(len(tokens)))
        params = {"st": source_type, **{f"t{i}": tok for i, tok in enumerate(tokens)}}
        bind.execute(
            sa.text(f"DELETE FROM sources WHERE source_type = :st AND token IN ({placeholders})"),
            params,
        )
