# CLAUDE.md — Aptly

> Context file for Claude Code. Read this first in every session.

## What Aptly is
A job platform for **international students who need visa (H-1B) sponsorship**. Core value: aggregate tech jobs that sponsor, surface sponsorship signals, and tailor resumes/cover letters per job. The moat is **sponsorship intelligence from free public DOL/LCA data** + aggregation + tailoring — NOT breadth of listings.

Audience is high-stakes (visa timelines). Trustworthiness > flashiness. Never fabricate data on a user's resume.

## Live infrastructure
- **Domain:** aptly.fyi (bought via Vercel; Vercel = registrar + DNS host)
  - Frontend: `https://aptly.fyi` (Vercel)
  - Backend: `https://api.aptly.fyi` (Render) — health: `/api/health`, docs: `/docs`
- Backend service name on Render: `aptly-backend-47l1`
- DB: Postgres on Neon
- Repo: github.com/ikrishna1919-png/Aptly (account ikrishna1919-png)
- Model in use: `claude-sonnet-4-6`
- Google OAuth client_id: `162079275825-7t9qbopjh4i5m8e4ocpmujdkeid1hoi1.apps.googleusercontent.com`
- NOTE: there are TWO Vercel projects on this repo (`aptly` and `aptly-buvg`). `aptly-buvg` is the old default and should be disconnected once confirmed unused. Until then, both build on each PR (double failure noise).

## Stack
- Backend: FastAPI (Py 3.11) + SQLAlchemy 2.x + Alembic. Anthropic SDK.
- Frontend: Next.js (App Router) + TypeScript + Tailwind + shadcn/ui. Framer Motion for animation.
- Monorepo: `/backend`, `/frontend`, `/infra`.

## Key paths
- `backend/app/main.py` — FastAPI app
- `backend/app/sources/` — JobSource base class + per-ATS adapters
- `backend/app/services/tailor.py` — resume/cover-letter tailoring (GENERATE step)
- `backend/scripts/start.sh` — runs `alembic upgrade head` then uvicorn (`set -euo pipefail`, so a bad migration crashes the deploy and Render keeps the prior deploy live)
- `render.yaml` — Render Blueprint, Root Dir = backend
- `infra/company_seed.tsv`, `infra/tasks/`
- `backend/tests/fixtures/golden_parse_reference.json` — known-correct parse of the AWS resume (regression target)

## Current working state (as of handoff)
WORKING:
- Auth: Google sign-in, FIXED via custom domain (cookie is now first-party on `.aptly.fyi`). Works 9/10; rare `redirect_uri_mismatch` (stale/cached request, not a config bug).
- Job aggregation across multiple ATS adapters (see below).
- Resume parsing (PDF/DOCX/text) — improved significantly via sending PDFs DIRECTLY to Anthropic as a document (NOT pdfplumber). Still has minor gaps; profile is editable to cover misses.
- Manual profile entry (primary path) with all sections.
- AI resume tailoring.
- Landing page, app nav shell, light-blue design system, profile page (clean layout).

ADAPTERS DONE: Greenhouse, Lever, Workday, SmartRecruiters, Ashby. All plug into the `sources` table.

## Hard rules (do not violate)
- For PDFs: send the PDF directly to Anthropic as a base64 `document` block. NEVER text-extract PDFs with pdfplumber (it strips spaces → "AzureDevOps" → corrupts everything). DOCX text extraction is OK (clean).
- Resume tailoring: NEVER fabricate metrics, skills, or achievements. Ground every bullet in the user's real, confirmed experience. Professional-but-human writing, NOT casual/informal.
- Migrations must be Postgres-valid (no SQLite-isms like `DEFAULT 1`; use `server_default=sa.text('true')`). A bad migration crashes the whole deploy.
- Don't split jobs per source type — one `jobs` table with a `source` field.
- Don't propose Spark/microservices — the bottleneck is network I/O; async + concurrency is the right model.
- Don't auto-merge auth/migration PRs. Verify PR base = main.
- Be honest about live vs coming-soon features in all copy. Don't advertise auto-apply/email-finder/interview-prep as available — they're coming-soon.

## Database notes
- `sources` table drives ingestion (source_type, token, enabled, last_run_at, last_status, jobs_found_last_run, etc.). Unique (source_type, token).
- `parse_runs` table columns: id, finished_at, user_id, raw_llm_output, profile, started_at, run_id, status, error.
  - Diagnostic query: `select id, status, error, raw_llm_output, profile from parse_runs order by started_at desc limit 1;`
  - `raw_llm_output` is the ground truth for diagnosing parse issues (extraction vs mapping vs display).

## Env vars (names only; values in dashboards)
- Render: ADMIN_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL (Neon pooled, `postgresql+psycopg://`), CORS_ORIGINS=https://aptly.fyi, FRONTEND_URL=https://aptly.fyi, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI=https://api.aptly.fyi/api/auth/google/callback, COOKIE_DOMAIN=.aptly.fyi, ADMIN_EMAILS (comma-sep; starts with ikrishna1919@gmail.com), HOURS_WINDOW (still 48 — widen to surface more jobs), INGEST_CONCURRENCY (default 10), INGEST_MAX_PER_RUN (default 150).
- Vercel: NEXT_PUBLIC_API_URL=https://api.aptly.fyi
- GitHub Actions: APTLY_API_URL=https://api.aptly.fyi, APTLY_ADMIN_TOKEN

## Recurring lessons (hard-won)
1. DIAGNOSE FROM LOGS / GROUND-TRUTH BEFORE FIXING. Guessing has caused multiple wrong-fix cycles. Use `raw_llm_output`, Render logs, and `npm run build` locally to reproduce before changing code.
2. Long ops on Render free tier → timeout. Use background jobs; always write a terminal status (success/error), never leave "running".
3. Anthropic structured-output 400s: reject `additionalProperties` (unless false), `minimum`/`maximum`, `minItems`/`maxItems`. Keep schemas clean.
4. Dependency works locally, breaks in serverless (e.g. isomorphic-dompurify ESM). Run `npm run build` to catch before deploy.
5. `Cannot GET` = wrong host (Node/Express 404). FastAPI 404 = `{"detail":"Not Found"}` = right host, wrong path.
6. After a failed Vercel build: the LIVE site stays on the last good deploy, but the broken build sits in `main` and blocks the NEXT deploy. Fix the build; don't just roll back.
7. Keep risky changes (auth, migrations) in separate PRs from cosmetic ones.

## Free-tier note
Render free tier sleeps and cold-starts (~30s). Upgrade to Starter (~$7/mo, always-on) before real users — it's the biggest snappiness lever. Set an Anthropic billing alert (usage-based cost is the wild card).
