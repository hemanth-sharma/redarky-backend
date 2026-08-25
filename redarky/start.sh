#!/usr/bin/env bash
set -e

# Default to 8000 if host environment doesn't inject PORT
export PORT="${PORT:-8000}"
export GO_SCRAPER_PORT="${GO_SCRAPER_PORT:-8081}"

# Run Alembic migrations
cd /app/redarky/api
alembic upgrade head || true

# Start Supervisor
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf