"""Editable seed list of company board tokens.

╭───────────────────────────────────────────────────────────────────────────╮
│ HOW TO ADD A COMPANY                                                      │
│   1. Find its public board on one of the three supported ATSes:           │
│       Greenhouse:       https://boards-api.greenhouse.io/v1/boards/<TOKEN>/jobs │
│       Lever:            https://api.lever.co/v0/postings/<TOKEN>?mode=json     │
│       SmartRecruiters:  https://api.smartrecruiters.com/v1/companies/<TOKEN>/postings │
│   2. Drop the `<TOKEN>` into the matching list below — one per line.      │
│      SmartRecruiters identifiers are case-sensitive (e.g. "Versant3").    │
│   3. Save. The next ingest pass picks it up; unreachable tokens are       │
│      auto-skipped, so it's safe to commit aspirational entries.           │
│   4. (Optional) `python -m app.cli validate-companies` reports which      │
│      tokens resolved vs were skipped, with posting counts.                │
╰───────────────────────────────────────────────────────────────────────────╯
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
