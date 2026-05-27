"""First-deploy seed list of company board tokens.

Runtime source of truth is the `sources` table (see `app.models.source`).
These lists exist so Alembic migration `0007_sources_table` can seed that
table the first time it runs, and so the test suite can pin the seed
contents. After the migration, `run_ingest` reads enabled rows from the
DB — editing this file no longer changes what ingest pulls on a deployed
instance. Add new tokens by writing a follow-up Alembic migration (the
seed insert here is `ON CONFLICT DO NOTHING`, so re-running it is safe
but it will NOT add new rows on a DB that already has the table).
"""

from __future__ import annotations

# ── Greenhouse board tokens ────────────────────────────────────────────────
# Paste a new token on its own line below. Trailing comma is required by
# Black; the format helps `git diff` show one row per change.
GREENHOUSE_TOKENS: list[str] = [
    "stripe",
    "airbnb",
    "reddit",
    "dropbox",
    "mongodb",
    "instacart",
    "doordash",
    "gitlab",
    "asana",
    "segment",
    "palantir",
    "twilio",
    "robinhood",
    "brex",
    "plaid",
    "datadog",
    "coinbase",
    "lyft",
    "retool",
    "snowflake",
    "vulcanelements",
    "sigmacomputing",
    "greenthumbindustries",
    "assystinc",
    "atek",
    "sayari",
    "torcrobotics",
    "lovelytics",
    "virtru",
    "amendconsulting",
    "cleerlyhealth",
    "orioninnovation",
    "vectorusa",
]

# ── Lever board tokens ─────────────────────────────────────────────────────
LEVER_TOKENS: list[str] = [
    "netflix",
    "github",
    "ramp",
    "mixpanel",
]

# ── SmartRecruiters company identifiers ────────────────────────────────────
# These are the `{company}` in
# https://api.smartrecruiters.com/v1/companies/{company}/postings — note
# the casing matters (it's an identifier, not a slug).
SMARTRECRUITERS_TOKENS: list[str] = [
    "Versant3",
]


# Compose the (source, token) list ingest + validation consume. Don't add
# entries here directly — extend the per-source lists above.
COMPANIES: list[tuple[str, str]] = (
    [("greenhouse", token) for token in GREENHOUSE_TOKENS]
    + [("lever", token) for token in LEVER_TOKENS]
    + [("smartrecruiters", token) for token in SMARTRECRUITERS_TOKENS]
)
