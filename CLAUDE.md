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
| `GOOGLE_REDIRECT_URI` | yes | Full URL of the OAuth callback on the BACKEND. With the custom domain in place, that's `https://api.aptly.fyi/api/auth/google/callback`. Must match the URI registered in the Google Cloud console exactly. |
| `SESSION_SECRET` | yes (prod) | Long random string — signs the session cookie. The default is an obvious placeholder so deploys can't accidentally ship without setting this. Rotate to invalidate every session. |
| `FRONTEND_URL` | **yes (no default)** | Where to bounce the user after a successful OAuth callback — `https://aptly.fyi` in prod, `http://localhost:3000` for local dev. **There is intentionally no default**: a missing value raises HTTP 500 from `/api/auth/google/login` so a misconfigured deploy fails loud instead of silently redirecting users at `localhost:3000` and showing `ERR_CONNECTION_REFUSED`. |
| `CORS_ORIGINS` | yes | Comma-separated list of origins allowed to send credentialed requests. With both subdomains under the shared parent (`aptly.fyi` and `api.aptly.fyi`), set this to `https://aptly.fyi` (and add `http://localhost:3000` locally). The browser sends the session cookie cross-subdomain via `Domain=.aptly.fyi`; CORS still has to allowlist the frontend origin or browsers refuse the credentialed request. |
| `COOKIE_DOMAIN` | yes (prod) | Parent domain to scope the session cookie to, with a LEADING DOT — `.aptly.fyi` for prod. SessionMiddleware emits `Domain=.aptly.fyi` on set; the logout handler mirrors the same value on delete so the cookie clears cleanly. **Leave empty for local dev** (host-only cookie is correct when there's only one origin) AND for any legacy setup where the browser never touches the backend origin directly. |
| `ENVIRONMENT` | yes (prod) | Set to `production` outside local dev so the session cookie picks up `Secure=True`. SameSite is `Lax` everywhere — correct because `aptly.fyi` and `api.aptly.fyi` are same-site, so the cookie travels on top-level navigations and fetch calls without needing `SameSite=None`. |
| `INITIAL_USER_EMAIL` | yes (first deploy) | The Google address of the owner. Migration 0012 seeds a `users` row with this email + `google_subject_id=NULL`; on the owner's first sign-in the auth handler links the Google `sub` to that row, preserving the existing single-user data. After that this var is informational only. |

Google Cloud setup (one-time):

1. Create an OAuth 2.0 Client ID in the Google Cloud console (Web
   application).
2. Add the callback as an **Authorised redirect URI**:
   `https://api.aptly.fyi/api/auth/google/callback`. For local dev
   also add `http://localhost:8000/api/auth/google/callback`.
3. Copy the Client ID + Secret into the env vars above.
4. Add `https://aptly.fyi` (and `http://localhost:3000` for dev) to
   **Authorised JavaScript origins**.
5. Restrict the OAuth consent screen to the scopes Aptly uses:
   `openid`, `email`, `profile`. No Drive / Calendar access.

### Shared-parent-domain cookies (production)

Frontend on `https://aptly.fyi` and backend on
`https://api.aptly.fyi` share the parent domain `aptly.fyi`. The
session cookie is issued with `Domain=.aptly.fyi` so it works
first-party for BOTH subdomains — the user's browser sends it on
every `aptly.fyi → api.aptly.fyi` fetch, no proxy required. This
is the permanent fix for the "Safari can't sign in" /
"can't sign back in after sign-out" bugs: same-site cookies pass
ITP, and the deletion on logout (which mirrors the same `Domain`
attribute) clears the cookie cleanly so the next OAuth handshake
starts from scratch.

  * **Cookie attrs in prod**: `Domain=.aptly.fyi; Path=/;
    SameSite=Lax; HttpOnly; Secure`.
  * **Local dev**: leave `COOKIE_DOMAIN` empty. The host-only
    cookie is correct when there's only one origin
    (`http://localhost:3000` ↔ `http://localhost:8000`); the
    frontend's `next dev` rewrite still proxies `/api/*` to the
    local backend so the dev experience stays single-origin.
  * **Frontend API client** (`frontend/lib/api.ts`) calls
    `https://api.aptly.fyi/api/...` directly in production. The
    legacy Vercel rewrite is no longer required; remove
    `API_PROXY_TARGET` and `NEXT_PUBLIC_API_URL` from Vercel
    once the new client base URL is in place.
  * **OAuth flow**: `GOOGLE_REDIRECT_URI` is on the BACKEND
    origin. Google sends the user to
    `api.aptly.fyi/api/auth/google/callback` → backend exchanges the
    code, links/creates the user, sets `Set-Cookie: session=…;
    Domain=.aptly.fyi; …` → backend 302s to `${FRONTEND_URL}/<next>`
    (i.e. `aptly.fyi`) → browser sends the parent-domain cookie on
    the next `/api/auth/me` call → user lands signed-in.

Sanity check after a deploy: open the app in Safari OR in a Chrome
incognito window, sign in with Google. You should land at `/jobs`
with the session intact; the in-app "Sign out" button works; a
sign-out → sign-in loop completes cleanly. If sign-out doesn't
clear the cookie, double-check that `COOKIE_DOMAIN` is the
literal string `.aptly.fyi` (with the leading dot) and is set on
BOTH the live deploy AND any preview deploys you're testing
against.

## Current phase
**Phase 0 — Foundation.** Goal: clean monorepo, Postgres wired with one real
migration, local Docker Postgres, hello-world backend + frontend, CI, and an
early deploy. See ROADMAP.md for the full phase list. Do not jump ahead to
later phases unless the user asks.

## Open input needed (for Phase 1)
Target companies / sponsors to seed the job feed (which ATS board tokens to
ingest first). Ask the user when Phase 1 starts.
