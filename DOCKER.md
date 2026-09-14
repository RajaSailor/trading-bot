# Docker Usage

## Build image
```bash
docker build -t trading-bot:latest .
```

## Run container directly
```bash
docker run --rm -p 5000:5000 --env-file config/.env.development trading-bot:latest
```

## Run full stack with Compose
```bash
docker compose up -d --build
```

### Optional Redis service
```bash
docker compose --profile cache up -d
```

## Manage containers
```bash
docker compose ps
docker compose logs -f trading-bot
docker compose down
```

## Volumes
- `postgres_data`: PostgreSQL data
- `redis_data`: Redis persistence
- `trading_state`: bot runtime state
- `./logs`: app logs
- `./backups`: SQL backups

## Networking
All services run in `trading-network` (bridge). Bot talks to DB and Redis using hostnames `postgres` and `redis`.
