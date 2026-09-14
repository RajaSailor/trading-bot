#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ROOT_DIR}/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/postgres_${TIMESTAMP}.sql"

mkdir -p "${BACKUP_DIR}"

POSTGRES_USER="${POSTGRES_USER:-trading_bot}"
POSTGRES_DB="${POSTGRES_DB:-trading_bot}"

cd "${ROOT_DIR}"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" > "${BACKUP_FILE}"

echo "Backup created: ${BACKUP_FILE}"
