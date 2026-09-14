# Deployment Guide

## Prerequisites
- Docker Engine 24+
- Docker Compose v2
- Linux/macOS shell

## 1) Configure environment
1. Copy production template values into your private environment file:
   - `config/.env.production`
2. Set real DhanHQ and Telegram credentials.

## 2) Deploy
```bash
bash deployment/deploy.sh config/.env.production
```

This command builds containers, starts services, and runs health checks.

## 3) Verify health
```bash
python deployment/health_check.py
```

## 4) Backup database
```bash
bash deployment/backup.sh
```
Backups are written to `backups/`.

## 5) Restore database
```bash
bash deployment/restore.sh backups/<backup-file>.sql
```

## Troubleshooting
- Service logs:
  ```bash
  docker compose logs -f trading-bot
  docker compose logs -f postgres
  ```
- Restart stack:
  ```bash
  docker compose down
  docker compose up -d --build
  ```
- If health check fails, test endpoints directly:
  ```bash
  curl -f http://localhost:5000/health
  curl -f http://localhost:5000/dhan/health
  ```
