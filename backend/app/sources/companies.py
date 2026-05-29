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
    # ── Batch added 2026-05 — well-known public Greenhouse boards.
    #    Slugs follow the standard boards.greenhouse.io/{slug} convention.
    #    These are convention-based candidates: the ingest run probes each
    #    token and the per-source auto-disable threshold parks any that
    #    don't resolve, so a wrong slug is self-healing, not harmful
    #    (same contract as the TSV-expanded candidate seeding). ──
    "figma",
    "databricks",
    "discord",
    "gusto",
    "samsara",
    "affirm",
    "benchling",
    "cloudflare",
    "hashicorp",
    "elastic",
    "flexport",
    "faire",
    "rippling",
    "grammarly",
    "webflow",
    "sofi",
    "opendoor",
    "nerdwallet",
    "squarespace",
    "etsy",
    "pinterest",
    "wayfair",
    "cockroachlabs",
    "anduril",
    "betterment",
    "verkada",
    "checkr",
    "airtable",
    "udemy",
    "thumbtack",
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

# ── Ashby board tokens ────────────────────────────────────────────────────
# `{token}` in https://api.ashbyhq.com/posting-api/job-board/{token} —
# the public slug a company picks when they set up their Ashby board.
# These are the well-known Ashby users seeded by migration 0010 alongside
# the TSV-expanded candidate set; auto-prune handles any that have since
# migrated away.
ASHBY_KNOWN_TOKENS: list[tuple[str, str]] = [
    ("linear", "Linear"),
    ("posthog", "PostHog"),
    ("notion", "Notion"),
    ("ramp", "Ramp"),
    ("vanta", "Vanta"),
    ("replicate", "Replicate"),
    ("modal", "Modal"),
    ("anthropic", "Anthropic"),
    ("hex", "Hex"),
    ("census", "Census"),
    ("cohere", "Cohere"),
    ("anrok", "Anrok"),
    ("replit", "Replit"),
    ("browserbase", "Browserbase"),
    ("together", "Together AI"),
    ("mercury", "Mercury"),
    ("coda", "Coda"),
    # ── Batch added 2026-05 — well-known public Ashby boards. Same
    #    self-healing contract: ingest probes the token, auto-disable
    #    parks any that don't resolve. ──
    ("openai", "OpenAI"),
    ("ironclad", "Ironclad"),
    ("watershed", "Watershed"),
    ("baseten", "Baseten"),
    ("perplexity", "Perplexity"),
    ("substack", "Substack"),
]


# Compose the (source, token) list ingest + validation consume. Don't add
# entries here directly — extend the per-source lists above.
COMPANIES: list[tuple[str, str]] = (
    [("greenhouse", token) for token in GREENHOUSE_TOKENS]
    + [("lever", token) for token in LEVER_TOKENS]
    + [("smartrecruiters", token) for token in SMARTRECRUITERS_TOKENS]
)
