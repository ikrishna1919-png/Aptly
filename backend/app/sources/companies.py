"""Editable seed list of company board tokens.

The ingest pipeline calls `fetch()` for each entry and **skips any token
that doesn't resolve** (404 / network error / malformed payload), so it's
safe to keep aspirational entries here — they get auto-filtered.

Run `python -m app.cli validate-companies` to check the current state of
the list against the live endpoints before relying on it.

Format: list of (source_name, board_token). `source_name` MUST be a key
in `app.sources.SOURCES`.
"""

from __future__ import annotations

# Tuples of (source, token). Edit freely; unknown sources or stale tokens
# are dropped at ingest time.
COMPANIES: list[tuple[str, str]] = [
    # ── Greenhouse ──
    ("greenhouse", "stripe"),
    ("greenhouse", "airbnb"),
    ("greenhouse", "reddit"),
    ("greenhouse", "dropbox"),
    ("greenhouse", "mongodb"),
    ("greenhouse", "instacart"),
    ("greenhouse", "doordash"),
    ("greenhouse", "gitlab"),
    ("greenhouse", "asana"),
    ("greenhouse", "segment"),
    ("greenhouse", "palantir"),
    ("greenhouse", "twilio"),
    ("greenhouse", "robinhood"),
    ("greenhouse", "brex"),
    ("greenhouse", "plaid"),
    ("greenhouse", "datadog"),
    ("greenhouse", "coinbase"),
    ("greenhouse", "lyft"),
    ("greenhouse", "retool"),
    ("greenhouse", "snowflake"),
    # ── Lever ──
    ("lever", "netflix"),
    ("lever", "github"),
    ("lever", "ramp"),
    ("lever", "mixpanel"),
]
