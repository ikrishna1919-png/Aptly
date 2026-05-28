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
| `GOOGLE_REDIRECT_URI` | yes | Full URL of the callback **on the frontend's origin** (since the browser is now talking to Vercel via the same-origin API proxy — see below). e.g. `https://aptly-buvg.vercel.app/api/auth/google/callback`. Must match what's registered in the Google Cloud console exactly. The proxy forwards this to the backend's `/api/auth/google/callback` handler; the cookie returns first-party to the Vercel origin. |
| `SESSION_SECRET` | yes (prod) | Long random string — signs the session cookie. The default is an obvious placeholder so deploys can't accidentally ship without setting this. Rotate to invalidate every session. |
| `FRONTEND_URL` | **yes (no default)** | Where to bounce the user after a successful OAuth callback — e.g. `https://aptly-buvg.vercel.app` in prod, `http://localhost:3000` for local dev. **There is intentionally no default**: a missing value raises HTTP 500 from `/api/auth/google/login` so a misconfigured deploy fails loud instead of silently redirecting users at `localhost:3000` and showing `ERR_CONNECTION_REFUSED`. |
| `CORS_ORIGINS` | yes | Comma-separated list of origins allowed to send credentialed requests. With the same-origin proxy in front of the backend, browser-originated requests now arrive from the proxy server (Vercel edge), not the user's browser directly. Listing the frontend origin still helps for any legacy direct-from-browser flow and is harmless otherwise. |
| `ENVIRONMENT` | yes (prod) | Set to `production` outside local dev so the session cookie picks up `Secure=True`. SameSite is now `Lax` on both prod and dev (first-party via the proxy — see below). |
| `INITIAL_USER_EMAIL` | yes (first deploy) | The Google address of the owner. Migration 0012 seeds a `users` row with this email + `google_subject_id=NULL`; on the owner's first sign-in the auth handler links the Google `sub` to that row, preserving the existing single-user data. After that this var is informational only. |

Google Cloud setup (one-time):

1. Create an OAuth 2.0 Client ID in the Google Cloud console (Web
   application).
2. Add the callback as an **Authorised redirect URI** — note this is
   the FRONTEND origin's `/api/auth/google/callback`, since the
   proxy forwards it to the backend:
   `https://<your-vercel-host>/api/auth/google/callback`. For local
   dev also add `http://localhost:3000/api/auth/google/callback`.
3. Copy the Client ID + Secret into the env vars above.
4. Add the Vercel URL (and `http://localhost:3000` for dev) to
   **Authorised JavaScript origins**. The Render origin no longer
   needs to be listed — the browser never calls it directly.
5. Restrict the OAuth consent screen to the scopes Aptly uses:
   `openid`, `email`, `profile`. No Drive / Calendar access.

### Same-origin API proxy (Vercel rewrites → Render)

The frontend (Vercel) and the backend (Render) live on different
domains, so the session cookie used to be third-party — Safari +
incognito blocked it and sign-in failed silently. **Fix**: the
frontend proxies every browser → backend call through its own
origin via Next.js rewrites (`frontend/next.config.mjs`). The
browser only ever calls the Vercel domain; the `Set-Cookie` lands
as first-party for that origin and survives ITP.

  * **`API_PROXY_TARGET`** (Vercel env var; server-side only, NOT
    `NEXT_PUBLIC_`) — full backend URL the rewrite forwards to,
    e.g. `https://aptly-backend-47l1.onrender.com`. Defaults to
    `http://localhost:8000` so `next dev` works without any extra
    setup. The legacy `NEXT_PUBLIC_API_URL` is still respected as
    a fallback but should be unset in prod so the browser uses the
    proxy path.
  * **Frontend API client** (`frontend/lib/api.ts`) uses relative
    URLs (`/api/...`); the browser hits Vercel, which rewrites the
    request to `${API_PROXY_TARGET}/api/...`. `Set-Cookie` from the
    backend flows back through the proxy unchanged and the browser
    stores it as first-party for the Vercel origin.
  * **Backend session cookie**: `SameSite=Lax; HttpOnly` in both
    prod and dev, plus `Secure` in prod. Lax works because the
    cookie is first-party from the browser's perspective; `None`
    is no longer needed (and is what Safari was blocking).
  * **OAuth flow**: `GOOGLE_REDIRECT_URI` MUST be on the Vercel
    origin (see above). Google sends the user there → Vercel
    rewrites to the backend's `/api/auth/google/callback` → backend
    sets the session cookie → backend 302s to
    `${FRONTEND_URL}/<next>` → user lands signed-in.

Sanity check after a deploy: open the app in Safari OR in a Chrome
incognito window, sign in with Google. You should land at `/jobs`
with the session intact; the in-app "Sign out" button works; a
parse / profile-save round-trips. If the browser shows you at the
sign-in page after the callback, the cookie didn't stick — most
likely `GOOGLE_REDIRECT_URI` is still pointing at the Render origin
instead of the Vercel proxy path.

## Current phase
**Phase 0 — Foundation.** Goal: clean monorepo, Postgres wired with one real
migration, local Docker Postgres, hello-world backend + frontend, CI, and an
early deploy. See ROADMAP.md for the full phase list. Do not jump ahead to
later phases unless the user asks.

## Open input needed (for Phase 1)
Target companies / sponsors to seed the job feed (which ATS board tokens to
ingest first). Ask the user when Phase 1 starts.
