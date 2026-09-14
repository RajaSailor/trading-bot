#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-${ROOT_DIR}/config/.env.production}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}"
  exit 1
fi

cd "${ROOT_DIR}"

export COMPOSE_DOCKER_CLI_BUILD=1
export DOCKER_BUILDKIT=1
export ENV_FILE_PATH="${ENV_FILE}"

docker compose --env-file "${ENV_FILE}" down --remove-orphans
REDIS_URL_VALUE="$(grep -E '^[[:space:]]*REDIS_URL[[:space:]]*=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2- || true)"
REDIS_URL_VALUE="$(echo "${REDIS_URL_VALUE}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
if [[ "${REDIS_URL_VALUE}" =~ ^redis://redis(:6379)?(/.*)?$ ]]; then
  docker compose --env-file "${ENV_FILE}" --profile cache up -d --build
else
  docker compose --env-file "${ENV_FILE}" up -d --build
fi

echo "Waiting for application health checks..."
MAX_ATTEMPTS="${DEPLOY_HEALTH_RETRIES:-20}"
SLEEP_SECONDS="${DEPLOY_HEALTH_INTERVAL:-5}"
ATTEMPT=1

until docker compose --env-file "${ENV_FILE}" exec -T trading-bot python deployment/health_check.py; do
  if [[ "${ATTEMPT}" -ge "${MAX_ATTEMPTS}" ]]; then
    echo "Deployment failed: health checks did not pass after ${MAX_ATTEMPTS} attempts."
    exit 1
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep "${SLEEP_SECONDS}"
done

echo "Deployment complete."
