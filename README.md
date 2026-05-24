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

## Phase

We are in **Phase 0 — Foundation** (see `ROADMAP.md`). Do not jump ahead
to later phases unless the user asks.
