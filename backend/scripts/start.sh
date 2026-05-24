#!/usr/bin/env bash
# Render startup script: apply migrations, then boot the API.
set -euo pipefail

echo "→ Running Alembic migrations..."
alembic upgrade head

echo "→ Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
