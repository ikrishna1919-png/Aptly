# CLAUDE.md — Aptly

Project memory for Claude Code. Read this and `ROADMAP.md` at the start of
every session before making changes.

## What this is
Aptly aggregates real job postings, filters them for what matters (visa
sponsorship, location, title, skills), uses Claude to tailor the user's resume
per role for ATS, exports DOCX/PDF, and later assists with applying.

**The wedge:** aggregation + AI resume tailoring + assisted apply, with
trustworthy sponsorship filtering. We are NOT cloning LinkedIn. Stay focused on
this loop; don't sprawl into social-network features.

## Stack (decided — don't swap without asking the user)
- Backend: **FastAPI** (Python 3.11+), SQLAlchemy 2.x, Alembic for migrations.
- DB: **PostgreSQL** (managed: Neon or Supabase). Local dev via Docker Compose.
- Frontend: **Next.js** (App Router) + TypeScript + Tailwind + shadcn/ui.
- Auth: **Google OAuth via `authlib`** + signed-cookie sessions (Phase 5). Email/password is intentionally not supported. See "Backend env vars (auth)" below.
- AI: **Claude API** (resume tailoring + resume parsing).
- Hosting: Vercel (frontend) + Railway/Render/Fly (backend) + managed Postgres.
- Background ingestion: cron to start; RQ/Celery later.

## Repo structure (monorepo)
```
/backend     FastAPI app, SQLAlchemy models, Alembic, services, routers
/frontend    Next.js app
/infra        docker-compose (local Postgres), deploy configs
ROADMAP.md   the phased plan
CLAUDE.md    this file
```

## Hard rules (do not violate)
1. **Never scrape LinkedIn, Indeed, or Glassdoor directly.** Legal + anti-bot
   risk. Use public ATS APIs (Greenhouse, Lever, Ashby, Workable), then Adzuna,
   then a paid aggregator at scale.
2. **Auto-apply stays human-in-the-loop.** Prepare/pre-fill applications;
   the user confirms submission. No bots submitting through sites that forbid
   automation.
3. **Secrets** live in env vars only — never commit `.env`, keys, or tokens.
4. **Data quality:** dedupe across sources, track freshness, flag ghost jobs
   (~1 in 5 postings is stale/fake).
5. Keep AI cost down: cache analyses; don't re-tailor unchanged inputs.

## Conventions
- Python: type hints, `ruff` + `black`, pytest. Pydantic for I/O schemas.
- TS: strict mode, ESLint + Prettier.
- Every feature: tests + a short note in the PR/commit on what changed.
- Small, reviewable commits. Run tests before declaring done.
- Migrations via Alembic only — never hand-edit the DB schema.

## Backend env vars (auth, Phase 5)

Google sign-in needs the following env vars set on the backend
service (Render). Without `GOOGLE_*` the auth router still loads
but `/api/auth/google/login` returns 503.

| Var | Required | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID` | yes | From the Google Cloud console OAuth client. |
| `GOOGLE_CLIENT_SECRET` | yes | Same place. |
| `GOOGLE_REDIRECT_URI` | yes | Full URL of the callback, e.g. `https://api.aptly.app/api/auth/google/callback`. Must match what's registered in the console exactly. |
| `SESSION_SECRET` | yes (prod) | Long random string — signs the session cookie. The default is an obvious placeholder so deploys can't accidentally ship without setting this. Rotate to invalidate every session. |
| `FRONTEND_URL` | yes | Where to bounce the user after a successful OAuth callback. Local dev: `http://localhost:3000`. Prod: the Vercel deployment URL. |
| `INITIAL_USER_EMAIL` | yes (first deploy) | The Google address of the owner. Migration 0012 seeds a `users` row with this email + `google_subject_id=NULL`; on the owner's first sign-in the auth handler links the Google `sub` to that row, preserving the existing single-user data. After that this var is informational only. |

Google Cloud setup (one-time):

1. Create an OAuth 2.0 Client ID in the Google Cloud console (Web
   application).
2. Add the callback as an **Authorised redirect URI**:
   `https://api.<your-render-host>/api/auth/google/callback`. For
   local dev also add `http://localhost:8000/api/auth/google/callback`.
3. Copy the Client ID + Secret into the env vars above.
4. Add `https://<your-render-host>` and the Vercel URL to
   **Authorised JavaScript origins**.
5. Restrict the OAuth consent screen to the scopes Aptly uses:
   `openid`, `email`, `profile`. No Drive / Calendar access.

The frontend needs `NEXT_PUBLIC_API_URL` to point at the backend
host so the session cookie rides correctly on cross-origin fetches
(set on Vercel; `http://localhost:8000` for local dev).

## Current phase
**Phase 0 — Foundation.** Goal: clean monorepo, Postgres wired with one real
migration, local Docker Postgres, hello-world backend + frontend, CI, and an
early deploy. See ROADMAP.md for the full phase list. Do not jump ahead to
later phases unless the user asks.

## Open input needed (for Phase 1)
Target companies / sponsors to seed the job feed (which ATS board tokens to
ingest first). Ask the user when Phase 1 starts.
