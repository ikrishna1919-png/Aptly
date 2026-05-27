# Aptly — Claude Context

This file gives Claude (or any AI assistant or new contributor) the durable context for working on this project. **Read this first.**

## What Aptly is

A job aggregator + AI resume-tailoring product targeted at **international students who need H-1B visa sponsorship**. Not a general-purpose job board; not trying to compete with LinkedIn or JobRight on breadth.

Core value loop: aggregate postings from company ATS platforms → filter (sponsorship, location, title, skills) → AI analyzes the job description, asks targeted questions about missing skills only, rewrites the resume ATS-optimized → user downloads DOCX/PDF.

## Strategic positioning (do not drift)

- We **do not** try to out-aggregate LinkedIn / Glassdoor / JobRight. Unwinnable solo.
- We **do** own the international-student / H-1B-sponsorship niche, which the giants underserve.
- Differentiator = aggregation + AI tailoring + **sponsorship intelligence from free public DOL H-1B/LCA data**.
- Paid breadth (Adzuna, TheirStack, Coresignal, etc.) is a Phase 6 lever, not a Phase 1 dependency.

## Stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic. Hosted on Render.
- **Database:** Postgres on Neon (prod). **No local dev** — Postgres is the only target.
- **Frontend:** Next.js (App Router), TypeScript, Tailwind, shadcn/ui. Hosted on Vercel.
- **AI:** Anthropic API. Model = `claude-sonnet-4-6`.
- **Layout:** monorepo with `/backend`, `/frontend`, `/infra`.

## Key paths

- `backend/app/main.py` — FastAPI entry, routers, `/api/health`.
- `backend/app/sources/` — ATS source adapters. Pluggable via the `JobSource` base class. Currently: Greenhouse, Lever, Workday, SmartRecruiters. Each plugs into the same `sources` table.
- `backend/app/services/tailor.py` — AI analyze/generate logic for resume tailoring.
- `backend/app/api/tailor.py` — tailoring HTTP endpoints.
- `backend/scripts/start.sh` — Render start script: runs Alembic migrations then uvicorn. `set -euo pipefail` means a failed migration crashes startup (Render keeps the previous deploy live).
- `render.yaml` at repo root — Render Blueprint. Start command lives here, not in the Render UI. Root Directory = `backend`.
- `infra/company_seed.tsv` — tab-separated company seed list (name, location).
- `infra/tasks/` — durable Claude Code task prompts (see `ROADMAP.md`).

## Sources table — central nervous system

All ingestion is driven by the `sources` table (one row per ATS board). Columns: `id`, `source_type` (greenhouse / lever / workday / smartrecruiters / ashby / theirstack / direct / other), `token`, `display_name`, `enabled`, `last_run_at`, `last_status` (success / error / skipped), `last_error`, `jobs_found_last_run`, `created_at`, optional `location`. Unique constraint on (source_type, token).

**Do not split jobs into per-source-type tables.** All jobs live in one `jobs` table with a `source` column. Per-source split happens at the *sources config* layer, not the jobs layer.

Every ingest run writes per-source status incrementally (not batched at the end), so a partial run persists progress and `last_status` is trustworthy.

## Hard data-sourcing rules

- **Never** scrape LinkedIn, Indeed, Glassdoor, or any general aggregator.
- **Never** pull from JobRight.ai. It is a direct competitor and a display surface, not an origin.
- LinkedIn / JobRight / Glassdoor are **display surfaces**, not sources. Companies post to their ATS (Greenhouse / Lever / Workday / etc.), which syndicates outward. Pull from the ATS directly.
- A `gh_jid` query parameter on a company's careers page means they are on Greenhouse. The board *token* is then findable.
- Manual entry of a single job (summarized in our own words + link back) is an acceptable stopgap, not a strategy.

## Recurring failure patterns (lessons learned the hard way)

1. **Long ops on Render free tier → 502 / timeout.** Anything slow must be a background job (endpoint returns 202 + run id; client polls). Ingestion does this; resume-parse needs the same treatment.
2. **Anthropic structured-output schema 400s.** The API rejects `additionalProperties` if not explicitly false, and rejects `minimum`/`maximum` on integers and `minItems`/`maxItems` on arrays. Enforce ranges/limits in prompt text + code, not in the schema.
3. **Alembic migration crashes deploy.** Example: `enabled BOOLEAN DEFAULT 1` (SQLite-ism; Postgres wants `TRUE`/`FALSE`). Every migration must be Postgres-valid. Use `server_default=sa.text('true')`, not `1` or Python `True`.
4. **Wrong-branch PRs.** Always verify PR base = main. Standard suffix: *"Open a PR base: main, merge main first if conflicts, summarize, then stop."*
5. **Dependency works locally but breaks in serverless runtime.** Example: `isomorphic-dompurify` drags in `jsdom` → `ERR_REQUIRE_ESM` in Vercel. Prefer client-side or server-safe alternatives for anything that manipulates HTML/DOM.
6. **Bundling unrelated changes hides root cause.** Keep risky core-engine changes (ingestion, auth) in separate PRs from cosmetic ones.
7. **Diagnose from logs before fixing.** Guessing wastes deploy cycles. Read the actual stack trace first.

## Secrets (names only; values live in dashboards)

- **Render:** `ADMIN_TOKEN`, `ANTHROPIC_API_KEY`, `DATABASE_URL` (Neon pooled string, prefixed `postgresql+psycopg://`), `CORS_ORIGINS` (Vercel URL, no trailing slash), `HOURS_WINDOW` (default 48), `INGEST_CONCURRENCY`, `INGEST_MAX_PER_RUN`.
- **Vercel:** `NEXT_PUBLIC_API_URL` (backend URL).
- **GitHub Actions:** `APTLY_API_URL`, `APTLY_ADMIN_TOKEN` (same value as Render's `ADMIN_TOKEN`).
- **Coming with auth (Task 3):** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `INITIAL_USER_EMAIL`.

Admin token gates `/api/admin/*` via the `X-Admin-Token` header.

## Workflow conventions

- All code changes go through Claude Code → PR → review → merge → auto-deploy.
- Always verify PR base = `main` before merging.
- After merge: wait for Vercel "Ready" and Render "Live" status, then hard-refresh.
- Diagnose from production logs (Render + Vercel) before prescribing fixes.
- Standard Claude Code task prompt suffix:

  > Open a PR base: main, merge main first if conflicts, summarize, then stop.

## What Claude should NOT do here

- Do not propose Spark / distributed computing / microservices. Bottleneck is network I/O, not compute. Async + bounded concurrency is the right tool.
- Do not propose scraping LinkedIn / Indeed / Glassdoor / JobRight under any framing.
- Do not split jobs into per-source-type tables.
- Do not auto-merge PRs touching auth or migrations without human review.
- Do not bundle risky changes (ingestion engine, auth) with cosmetic ones.
