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

cp "${ENV_FILE}" .env

docker compose down --remove-orphans
docker compose up -d --build
python deployment/health_check.py

echo "Deployment complete."
