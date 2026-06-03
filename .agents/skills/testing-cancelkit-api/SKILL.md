---
name: testing-cancelkit-api
description: Test the CancelKit Subscription Intelligence API end-to-end. Use when verifying API endpoints, admin dashboard, security headers, input validation, or monitoring features.
---

# Testing CancelKit API

## Quick Start

### 1. Start the local server
```bash
cd /home/ubuntu/repos/DEVIN
CANCELKIT_SCHEDULER=false uvicorn app.main:app --host 0.0.0.0 --port 8000
```
- `CANCELKIT_SCHEDULER=false` disables background jobs (APScheduler) which aren't needed for testing
- Server auto-seeds 205 services on startup via Alembic migrations + seed_database

### 2. Create an API key
```bash
curl -s -X POST http://localhost:8000/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"name":"tester","email":"t@test.com"}'
```
Returns a `ck_...` key. Use it in `X-API-Key` header for all `/v1/` endpoints.

Tier options: `free` (default), `starter`, `growth`, `enterprise`. Higher tiers unlock more endpoints.

### 3. Admin access
- **Admin API**: All `/v1/admin/*` endpoints require `?admin_token=admin` query param
- **Admin Dashboard UI**: Available at `http://localhost:8000/admin`
- Token is configurable via `CANCELKIT_ADMIN_TOKEN` env var (default: `admin`)

## Key Testing Patterns

### Security Headers
Check any response for production security headers:
```bash
curl -s -D - -o /dev/null http://localhost:8000/health | grep -iE "x-content-type|x-frame|x-xss|referrer-policy|permissions-policy|x-api-version"
```
Expected: `nosniff`, `DENY`, `1; mode=block`, `strict-origin-when-cross-origin`, `camera=()...`

HSTS (`Strict-Transport-Security`) only appears when `CANCELKIT_ENV=production`.

### Health & Readiness
- `GET /health` — returns `status`, `version`, `database` ("connected" or error), `uptime_seconds`
- `GET /ready` — K8s-style readiness probe (200 if DB up, 503 if down)

### Input Validation (Admin)
Admin CRUD endpoints enforce strict validation:
- Domain format: `^[a-zA-Z0-9]([a-zA-Z0-9\-]*\.)+[a-zA-Z]{2,}$`
- Method enum: `web|phone|email|chat|app|mail|in_person`
- Difficulty enum: `easy|medium|hard`
- Channel enum: `phone|email|chat|web|app|social|mail`
- Steps require `min_length=1`

Test by sending invalid data and expecting HTTP 422.

### Tier Gating
- Free tier: `/v1/cancel`, `/v1/services`, `/v1/service/{domain}`, `/v1/contact/{domain}`
- Starter+: `/v1/signals`, `/v1/billing`
- Growth+: `/v1/webhooks`, `/v1/monitor/run`, `/v1/monitor/verify`
- Enterprise: `/v1/monitor/decay`, `/v1/monitor/report-check`

Free tier gets 403 on gated endpoints with upgrade URL.

## Running Tests

### Unit tests
```bash
cd /home/ubuntu/repos/DEVIN && python -m pytest tests/ -v
```
Expected: 155+ tests passing. Three test files: `test_api.py`, `test_admin.py`, `test_monitoring.py`.

### Lint
```bash
cd /home/ubuntu/repos/DEVIN && ruff check .
```

## Architecture Notes
- **Backend**: FastAPI (Python), SQLAlchemy ORM, SQLite (dev) / Postgres (prod)
- **Database**: Alembic migrations at `alembic/versions/`. Startup runs `alembic upgrade head` then falls back to `create_all`
- **Seed data**: `app/data/services.yaml` → `app/seed.py` loads 205 services with paths + contacts
- **Admin UI**: Single-page HTML at `web/admin.html` (OLED dark theme + emerald accent)
- **Developer Dashboard**: `web/dashboard.html` at `/dashboard`
- **Landing Page**: `web/index.html` at `/`

## Devin Secrets Needed
None required for local testing. Admin token defaults to `admin`.

For production testing, you would need:
- `CANCELKIT_ADMIN_TOKEN` — admin API access token
- `DATABASE_URL` — Postgres connection string (if testing against Postgres)
