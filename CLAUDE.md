# CLAUDE.md — Aptly

> Context file for Claude Code. Read this first in every session. Describes what IS, not what will be.

## What Aptly is
A job platform for **international students who need visa (H-1B) sponsorship**. Core value: aggregate tech jobs that sponsor, surface sponsorship signals, and tailor resumes/cover letters per job. The moat is **sponsorship intelligence from free public DOL/LCA data** + aggregation + tailoring — NOT breadth of listings.

Audience is high-stakes (visa timelines). Trustworthiness > flashiness. Never fabricate data on a user's resume.

## Live infrastructure
- **Domain:** aptly.fyi (bought via Vercel; Vercel = registrar + DNS host)
  - Frontend: `https://aptly.fyi` (Vercel)
  - Backend: `https://api.aptly.fyi` (Render) — health: `/api/health`, docs: `/docs`
- Backend service name on Render: `aptly-backend-47l1`, `plan: free` (per `render.yaml`).
- DB: Postgres on Neon
- Repo: github.com/ikrishna1919-png/Aptly (account ikrishna1919-png)
- Model in use: `claude-sonnet-4-6` (generation); `claude-haiku-4-5-20251001` (fast classify/analyze steps).
- Google OAuth client_id: `162079275825-7t9qbopjh4i5m8e4ocpmujdkeid1hoi1.apps.googleusercontent.com`
- **Vercel projects (CRITICAL):** Live Vercel project is **`aptly-buvg`** (bound to aptly.fyi). The project named **`aptly`** is the orphan to delete once confirmed unused. **Do NOT disconnect or delete `aptly-buvg` under any circumstances.** Until the orphan is removed, both build on each PR (double failure noise).

## Stack
- Backend: FastAPI (Py 3.11) + SQLAlchemy 2.x + Alembic. Anthropic SDK.
- Frontend: Next.js (App Router) + TypeScript + Tailwind + shadcn/ui. Framer Motion (`motion/react`).
- Extension: Manifest V3, plain JS (no framework); content scripts bundled to IIFE by `extension/scripts/build.mjs`.
- Monorepo: `/backend`, `/frontend`, `/extension`, `/infra`.

## Key paths
- `backend/app/main.py` — FastAPI app (router registration)
- `backend/app/sources/` — JobSource base class + per-ATS adapters
- `backend/app/services/tailor.py` — resume tailoring (analyze + generate, prompt-based JSON)
- `backend/app/services/ats.py` — /ats generation + DOCX keyword-injection
- `backend/app/services/cover_letter.py` — cover-letter generation + render
- `backend/app/services/{docx_export,pdf_export}.py` — format renderers (Modern/Classic/Minimal/Plain + custom)
- `backend/scripts/start.sh` — `alembic upgrade head` then uvicorn (`set -euo pipefail`, so a bad migration crashes the deploy and Render keeps the prior deploy live)
- `render.yaml` — Render Blueprint, Root Dir = backend
- `infra/company_seed.tsv`, `infra/tasks/`
- `backend/tests/fixtures/golden_parse_reference.json` — known-correct parse of the AWS resume (regression target)

## Adapters (in `backend/app/sources/`)
Greenhouse, Lever, Ashby, SmartRecruiters, Workday — all plug into the `sources` table. One `jobs` table with a `source` field (never split per source type).

## Chrome Extension (`/extension`)
- **Path:** `/extension` at repo root. Manifest V3, plain JS.
- **Build:** content scripts are bundled to a flat **IIFE** by `extension/scripts/build.mjs` (`npm run build`), output committed to `extension/content/greenhouse.js` and referenced by the manifest. **DO NOT** reference ESM-`import` source files directly in the manifest — MV3 content scripts can't load ES modules and crash with `SyntaxError`. Popup (HTML) and background (manifest `"type":"module"`) CAN use ESM.
- **Supports:** Greenhouse only (v1.0). Lever/Ashby/Workday adapters are separate phased PRs (not built).
- **Auth:** separate token system from the web app — `extension_sessions` table, bearer token in `chrome.storage.local`. NOT cookies. Backend validates the token on every `/api/extension/*` call.
- **Distribution:** dev install via "Load unpacked" today. Chrome Web Store submission not yet done.
- **Hard rule:** the extension is a **user-initiated form-fill assistant** — it NEVER auto-submits. The user clicks submit themselves; demographic/sensitive fields are left blank unless explicitly opted in.

## ATS Toolkit (`/ats`)
5-feature hub (nav label "ATS Toolkit"; URL path stays `/ats`):
- `/ats/format` — choose default resume format
- `/ats/builder` — resume builder (from profile / LinkedIn ZIP import / import + reformat)
- `/ats/generate` — ATS resume generator (uses saved default format; per-run override)
- `/ats/cover-letter` — cover letter generator
- `/ats/cover-letter-format` — default cover letter format

Default formats persist on the candidate row (`default_resume_format` / `default_cover_letter_format` JSON). The in-job "Tailor my resume for this job" CTA routes to `/ats/generate?jobId=` (format step skipped, default applied).

## Hard rules (do not violate)
- For PDFs: send the PDF directly to Anthropic as a base64 `document` block. NEVER text-extract PDFs with pdfplumber (it strips spaces → "AzureDevOps" → corrupts everything). DOCX text extraction is OK (clean).
- Resume/cover-letter tailoring: NEVER fabricate metrics, skills, achievements, employers, or roles. Ground every line in the user's real, confirmed experience. Professional-but-human, NOT casual.
- Migrations must be Postgres-valid (no SQLite-isms like `DEFAULT 1`; use `server_default=sa.text('true')`). A bad migration crashes the whole deploy. **One migration-containing PR open at a time, ever.**
- Don't split jobs per source type — one `jobs` table with a `source` field.
- Don't propose Spark/microservices — the bottleneck is network I/O; async + concurrency is the right model.
- Don't scrape job boards (LinkedIn/Indeed/Glassdoor/etc.). LinkedIn import is via the user's own data-export ZIP only.
- Don't auto-merge auth/migration PRs. Verify PR base = main.
- Be honest about live vs coming-soon (see Honest framing rules).

## Honest framing rules
- Never advertise coming-soon features as live (auto-apply, email-finder, interview-prep are coming-soon).
- Never invent metrics, stats, scores, testimonials, or "N students trust us" social proof.
- Show real screenshots / product mocks, not stock photos of people.
- **JD keyword coverage %** (deterministic keyword overlap) is the honest alternative to "ATS score" — NEVER display an invented 0–100 score.

## Database notes
- `sources` table drives ingestion (source_type, token, enabled, last_run_at, last_status, jobs_found_last_run, …). Unique (source_type, token).
- `parse_runs`: id, finished_at, user_id, raw_llm_output, profile, started_at, run_id, status, error.
  - Diagnostic: `select id, status, error, raw_llm_output, profile from parse_runs order by started_at desc limit 1;`
  - `raw_llm_output` is the ground truth for diagnosing parse issues (extraction vs mapping vs display).
- `extension_sessions` (bearer tokens), `saved_qa_pairs` (extension learning loop), `tailor_runs` (tailor + /ats runs), `cover_letters`.

## Env vars (names only; values in dashboards)
- Render: ADMIN_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL (Neon pooled, `postgresql+psycopg://`), CORS_ORIGINS=https://aptly.fyi, FRONTEND_URL=https://aptly.fyi, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI=https://api.aptly.fyi/api/auth/google/callback, COOKIE_DOMAIN=.aptly.fyi, ADMIN_EMAILS (comma-sep; starts with ikrishna1919@gmail.com), HOURS_WINDOW (48), INGEST_CONCURRENCY (default 10), INGEST_MAX_PER_RUN (default 150).
- Vercel: NEXT_PUBLIC_API_URL=https://api.aptly.fyi
- GitHub Actions: APTLY_API_URL=https://api.aptly.fyi, APTLY_ADMIN_TOKEN

## Recurring lessons (hard-won)
1. DIAGNOSE FROM LOGS / GROUND-TRUTH BEFORE FIXING. Guessing has caused multiple wrong-fix cycles. Use `raw_llm_output`, Render logs, and `npm run build` locally to reproduce before changing code.
2. Long ops on Render free tier → timeout. Use background jobs; always write a terminal status (success/error), never leave "running".
3. Anthropic **"Grammar compilation timed out"** = a complex JSON schema sent via strict structured output (`response_format`/json_schema). Fix: use **prompt-based JSON** output, parse with `json.loads`, **retry once on JSONDecodeError** with a corrective prompt. No strict `response_format` with complex schemas. (Also: strict-output 400s reject `additionalProperties` unless false, `minimum`/`maximum`, `minItems`/`maxItems`.)
4. **Migrations: one PR with a migration open at a time. Never parallel.** Multi-head Alembic errors happen even with sequential merges if branches were created from the same ancestor.
5. **Render free tier cold-starts are ~30s.** If on free tier, factor this into latency expectations.
6. **MV3 content-script ESM imports require build-time bundling (IIFE).** Source files with `import` referenced directly in the manifest crash with `Cannot use import statement outside a module`. Verify with `grep "^import" extension/content/greenhouse.js` → empty.
7. Dependency works locally, breaks in serverless (e.g. isomorphic-dompurify ESM). Run `npm run build` to catch before deploy.
8. `Cannot GET` = wrong host (Node/Express 404). FastAPI 404 = `{"detail":"Not Found"}` = right host, wrong path.
9. After a failed Vercel build: the LIVE site stays on the last good deploy, but the broken build sits in `main` and blocks the NEXT deploy. Fix the build; don't just roll back.
10. Keep risky changes (auth, migrations) in separate PRs from cosmetic ones.

## Free-tier note
- **Render:** `plan: free` per `render.yaml` — sleeps and cold-starts (~30s). Starter (~$7/mo, always-on) is the biggest snappiness lever before real users. Set an Anthropic billing alert (usage-based cost is the wild card). *(Render dashboard is authoritative if it diverges from render.yaml — confirm there.)*
- **Vercel:** live project is `aptly-buvg`. The free tier has a **daily build rate limit** that's been hit while pushing many PRs in a day. *(Current Vercel plan + any upgrade date: confirm in the Vercel dashboard — not verifiable from the repo.)*
