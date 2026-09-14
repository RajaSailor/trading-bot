# Deployment Guide

## System Requirements
- Python 3.9+
- Network access to DhanHQ + Telegram APIs
- Environment variables in `.env`

## Installation
```bash
pip install -r requirements.txt
python main.py
```

## Configuration
- Required credentials: Dhan client/token, Telegram bot/chat IDs.
- Optional monitoring credentials: Slack webhook, SMTP, service alert bot/channel IDs.
- For protected webhook admin routes, set `WEBHOOK_SECRET`.

## Backup and Restore
- Persist bot state files and deployment env config.
- Keep periodic backups of runtime JSON/state artifacts.
- Restore by redeploying code, restoring env vars, then state files.
