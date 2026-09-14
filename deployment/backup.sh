#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ROOT_DIR}/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/postgres_${TIMESTAMP}.sql"
TMP_BACKUP_FILE="${BACKUP_FILE}.tmp"

mkdir -p "${BACKUP_DIR}"

POSTGRES_USER="${POSTGRES_USER:-trading_bot}"
POSTGRES_DB="${POSTGRES_DB:-trading_bot}"

cd "${ROOT_DIR}"
trap 'rm -f "${TMP_BACKUP_FILE}"' EXIT
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" > "${TMP_BACKUP_FILE}"
mv "${TMP_BACKUP_FILE}" "${BACKUP_FILE}"
trap - EXIT

echo "Backup created: ${BACKUP_FILE}"
