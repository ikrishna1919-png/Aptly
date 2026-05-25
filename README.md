# Aptly

Aggregates real job postings, filters for what matters (visa sponsorship,
location, title, skills), uses Claude to tailor a resume per role, and
assists with applying. See `ROADMAP.md` for the phased plan and
`CLAUDE.md` for engineering rules.

This repo is a monorepo:

```
/backend     FastAPI + SQLAlchemy 2.x + Alembic
/frontend    Next.js (App Router) + TypeScript + Tailwind + shadcn/ui
/infra       Local Postgres via docker-compose
```

## Prerequisites

- Python 3.11+
- Node.js 20+ (and npm)
- Docker + Docker Compose

## 1. Start Postgres locally

From the repo root:

```bash
docker compose -f infra/docker-compose.yml up -d
```

That brings up Postgres on `localhost:5432` (db `aptly`, user `aptly`,
password `aptly`). Stop with `docker compose -f infra/docker-compose.yml down`.

## 2. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Health check: <http://localhost:8000/api/health>

Run tests + lint:

```bash
pytest
ruff check .
black --check .
```

## 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open <http://localhost:3000>. The landing page calls the backend's
`/api/health` and renders the status.

Run lint:

```bash
npm run lint
```

## Ingestion (Phase 1)

The job feed is a strict **48-hour rolling window** of postings pulled
from public ATS boards (Greenhouse + Lever). Each ingest pass:

1. Fetches every posting for every seeded company token.
2. Skips boards that don't resolve (404, network, malformed) so a dead
   token can never break the rest of the run.
3. Drops postings whose `source_updated_at` is older than `HOURS_WINDOW`.
4. Upserts the rest, deduped by `(source, external_id)` + a content hash.
5. **Deletes** any stored job whose `source_updated_at` is older than the
   window. The feed never shows stale rows.

The seed list lives in `backend/app/sources/companies.py` — edit it
freely; unreachable tokens are auto-skipped at ingest time. Validate the
current state with the CLI below before relying on it.

### Three ways to trigger ingest

1. **CLI** — fast loop for local dev / one-off runs:

   ```bash
   cd backend
   source .venv/bin/activate
   python -m app.cli ingest                # run ingest + cleanup
   python -m app.cli validate-companies    # probe every seeded token
   ```

   Both read `DATABASE_URL` and `HOURS_WINDOW` from `.env`. The validator
   exits non-zero if any token is unreachable.

2. **Admin endpoint** — the production trigger, protected by a shared
   token. The scheduled workflow uses this:

   ```bash
   curl -X POST "$APTLY_API_URL/api/admin/ingest" \
        -H "X-Admin-Token: $ADMIN_TOKEN"
   ```

   The endpoint returns a JSON `IngestStats` object (`inserted`,
   `updated`, `deleted_expired`, `boards_failed`, …). If `ADMIN_TOKEN` is
   unset on the backend, the endpoint returns 503 and refuses to run —
   it is never unprotected.

3. **Scheduled GitHub Actions** — `.github/workflows/ingest.yml` runs
   every 6 hours (`cron: "0 */6 * * *"`) and can also be triggered
   manually from the Actions tab. It requires two **GitHub Actions
   secrets** (Settings → Secrets and variables → Actions):

   | Secret              | Value                                                            |
   | ------------------- | ---------------------------------------------------------------- |
   | `APTLY_API_URL`     | Base URL of the deployed backend (e.g. `https://aptly-backend.onrender.com`) |
   | `APTLY_ADMIN_TOKEN` | Same string as the backend's `ADMIN_TOKEN` env var               |

   Generate the admin token with:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Set the same string in **both** Render (env var `ADMIN_TOKEN`) and
   GitHub Actions (secret `APTLY_ADMIN_TOKEN`).

### Public read API

`GET /api/jobs` — the frontend uses this. Always scoped to the rolling
window, sorted newest-first. Supported query params:

| Param             | Type     | Notes                                   |
| ----------------- | -------- | --------------------------------------- |
| `q`               | string   | Free-text over title + company          |
| `company`         | string   | Exact match (case-insensitive)          |
| `location`        | string   | Substring match (case-insensitive)      |
| `remote`          | bool     | `true` / `false`                        |
| `employment_type` | string   | e.g. `Full-time`                        |
| `sponsors_visa`   | bool     | Only true when the JD explicitly states it; defaults to "unknown" (null) |
| `limit`           | int      | 1..200, default 50                      |
| `offset`          | int      | default 0                               |

## Resume tailoring (Phase 4)

From any job detail page, the **Tailor my resume** panel runs the
single-user demo candidate (hardcoded in `backend/app/services/demo_candidate.py`)
against the live JD using **Claude Sonnet 4.6**:

1. `POST /api/tailor/analyze {job_id}` — match score, top skills, gaps, and
   three short tailoring questions. Cached per `(job, candidate)` so a
   second click on the same role doesn't hit the API.
2. `POST /api/tailor/generate {job_id, answers}` — ATS-optimized rewritten
   resume (summary, skills, experience bullets, education, plus `atsNotes`
   explaining the tailoring choices). Reframes only — never fabricates.
3. `POST /api/tailor/docx {resume}` — streams a clean ATS-formatted `.docx`
   file built with `python-docx`.

Set `ANTHROPIC_API_KEY` on the backend to enable real Claude calls. If the
key is unset the endpoints return deterministic mock data clearly labeled
`demo mode` — the whole UI still works, you just don't get a real rewrite.
Phase 2 will replace the hardcoded candidate with user accounts + resume
parsing.

## CI

`.github/workflows/ci.yml` lints and tests both apps on every push and
pull request.

---

## Deploy

We host on three free-tier services:

| Piece    | Host                | Lives in        |
| -------- | ------------------- | --------------- |
| Database | **Neon** (Postgres) | (managed)       |
| Backend  | **Render**          | `/backend`      |
| Frontend | **Vercel**          | `/frontend`     |

The flow: provision Neon → deploy Render (with Neon's URL) → deploy Vercel
(with Render's URL) → backfill the Vercel URL into Render's `CORS_ORIGINS`.

### Step 1 — Neon (Postgres)

1. Sign up at <https://neon.tech> and create a new project (any region).
2. In **Dashboard → Connection Details**, copy the **Pooled connection**
   string. It looks like:
   ```
   postgresql://USER:PASSWORD@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```
3. **Important:** SQLAlchemy needs the `+psycopg` driver. Edit the scheme:
   ```
   postgresql+psycopg://USER:PASSWORD@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```
   Keep that final string handy — it's your `DATABASE_URL`.

### Step 2 — Render (Backend)

1. Push this repo to GitHub if you haven't already.
2. Go to <https://render.com> → **New → Blueprint** → connect this repo.
3. Render auto-detects `render.yaml` at the repo root and proposes the
   `aptly-backend` web service. Click **Apply**.
4. When prompted for the secret env vars, set:
   - `DATABASE_URL` → the Neon string from Step 1.
   - `CORS_ORIGINS` → leave as `http://localhost:3000` for now; we'll
     update it after Vercel deploys.
5. First deploy will:
   - Run `pip install -e .` (build).
   - Run `alembic upgrade head` (migrations — creates the `jobs` table on
     Neon).
   - Start `uvicorn` on Render's `$PORT`.
6. Once live, copy the public URL — e.g. `https://aptly-backend.onrender.com`.
   Hit `/api/health` to confirm: `database` should read `ok`.

> Note: Render free-tier web services sleep after ~15 min idle; the first
> request after sleep takes ~30s to cold-start. Fine for Phase 0.

### Step 3 — Vercel (Frontend)

1. Go to <https://vercel.com> → **Add New → Project** → import this repo.
2. In the import screen:
   - **Root Directory:** `frontend`
   - **Framework Preset:** Next.js (auto-detected).
3. Under **Environment Variables**, add:
   - `NEXT_PUBLIC_API_URL` = your Render URL from Step 2
     (e.g. `https://aptly-backend.onrender.com`).
   Apply it to **Production**, **Preview**, and **Development**.
4. Click **Deploy**. When it finishes, copy the Vercel URL —
   e.g. `https://aptly.vercel.app`.

### Step 4 — Close the CORS loop

The browser will block the frontend's calls to the backend until CORS
allows it.

1. Back in Render → `aptly-backend` → **Environment**.
2. Edit `CORS_ORIGINS` to your Vercel URL:
   ```
   https://aptly.vercel.app
   ```
   (Comma-separate multiple, e.g. include `https://aptly-git-main.vercel.app`
   for preview deploys.)
3. Save → Render redeploys automatically.
4. Visit the Vercel URL. The landing page should show **online** and the
   health fields populated.

### Where each variable goes — cheat sheet

| Variable              | Local             | Render (backend) | Vercel (frontend) |
| --------------------- | ----------------- | ---------------- | ----------------- |
| `DATABASE_URL`        | `backend/.env`    | ✅ secret         | —                 |
| `CORS_ORIGINS`        | `backend/.env`    | ✅                | —                 |
| `ENVIRONMENT`         | `backend/.env`    | ✅ (= production) | —                 |
| `NEXT_PUBLIC_API_URL` | `frontend/.env.local` | —            | ✅                 |

Secrets are **never** committed. `render.yaml` declares the *names* of
the env vars (`sync: false` for secrets); the *values* are entered in the
Render dashboard.

## Phase

We are in **Phase 4 — AI tailoring** (see `ROADMAP.md`). The Phase 2
work (accounts + resume upload) is still pending; tailoring runs against
a hardcoded demo candidate for now.
