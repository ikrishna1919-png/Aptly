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

## CI

`.github/workflows/ci.yml` lints and tests both apps on every push and
pull request.

## Phase

We are in **Phase 1 — Real data** (see `ROADMAP.md`). Do not jump ahead
to later phases unless the user asks.
