#!/bin/sh
set -eu

PORT="${PORT:-8080}"

# Production Cloud Run entrypoint. Migrations are intentionally opt-in so
# horizontally scaled revisions do not race each other during startup.
if [ "${RUN_DB_MIGRATIONS:-false}" = "true" ]; then
  alembic upgrade head
fi

exec uvicorn main:app --host 0.0.0.0 --port "${PORT}" --workers "${WEB_CONCURRENCY:-1}"
