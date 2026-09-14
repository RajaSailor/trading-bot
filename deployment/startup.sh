#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/logs /app/backups /app/state

FLASK_ENV_VALUE="${FLASK_ENV:-production}"
PORT_VALUE="${PORT:-5000}"

if [[ "${FLASK_ENV_VALUE}" == "development" ]]; then
  exec python main.py
fi

exec gunicorn --bind "0.0.0.0:${PORT_VALUE}" --workers "${GUNICORN_WORKERS:-2}" --timeout "${GUNICORN_TIMEOUT:-120}" main:app
