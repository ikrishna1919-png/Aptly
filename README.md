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

We are in **Phase 0 — Foundation** (see `ROADMAP.md`). Do not jump ahead
to later phases unless the user asks.
