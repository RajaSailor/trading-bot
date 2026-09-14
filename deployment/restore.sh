#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_FILE="${1:-}"

if [[ -z "${BACKUP_FILE}" ]]; then
  echo "Usage: deployment/restore.sh <backup-file>"
  exit 1
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "Backup file not found: ${BACKUP_FILE}"
  exit 1
fi

POSTGRES_USER="${POSTGRES_USER:-trading_bot}"
POSTGRES_DB="${POSTGRES_DB:-trading_bot}"

cd "${ROOT_DIR}"
cat "${BACKUP_FILE}" | docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"

echo "Restore complete from: ${BACKUP_FILE}"
