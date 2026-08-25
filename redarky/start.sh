#!/usr/bin/env bash
set -e

# Default to 8000 if Render doesn't inject PORT
export PORT="${PORT:-8000}"

# Optional: Run Alembic migrations before spinning up services
cd /app/redarky/api
# python -m alembic upgrade head
alembic upgrade head || true

# Start Supervisor
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf