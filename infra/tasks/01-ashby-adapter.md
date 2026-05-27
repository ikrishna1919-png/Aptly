Task 1 — Add Ashby adapter and bulk-load Ashby companies
Add Ashby as a new ATS source (source_type='ashby'), following the existing JobSource adapter pattern (Greenhouse / Lever / Workday / SmartRecruiters) in backend/app/sources/. It plugs into the existing sources table, validation, 48-hour window, dedup, single jobs table, async fetch with bounded concurrency, per-source timeout + isolation, and auto-prune machinery — do not change any of that, only add the adapter.

Ashby has a public job board API. Read Ashby's current job board API docs to confirm the exact endpoint, then build the adapter. The standard pattern is a per-company endpoint that returns JSON job postings; no auth needed for published boards. Each company is identified by a board "token" (their slug — e.g. linear, posthog, notion).
Map Ashby's response into the same normalized job shape the other adapters produce: external id, title, company, location, URL (apply link), description (HTML), posted/updated date. Run descriptions through the same HTML-entity-decode + plain-text normalization the other adapters use, since Ashby descriptions are also HTML.
Bulk-load a candidate list of known Ashby-using companies. Use a public dataset of Ashby board tokens if one exists; otherwise seed with a list of well-known Ashby users (e.g. Linear, PostHog, Notion, Ramp, Vanta, Replicate, Modal, Anthropic, Hex, Census, Cohere, Anrok, Replit, Browserbase, Together AI, Mercury, Coda) PLUS slugified candidates from existing infra/company_seed.tsv rows inserted as source_type='ashby' candidates (same approach used for Greenhouse + Lever bulk-load). Rely on the existing validator + auto-prune to keep what resolves and drop what doesn't.
Confirm a real Ashby company ends a run with last_status='success' and a real jobs_found_last_run count (respecting the 48-hour window).
Do not change HOURS_WINDOW, ingestion batching, or any auth-related code.

Open a PR base: main, merge main first if conflicts, summarize, then stop.
