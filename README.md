# CancelKit

Subscription Intelligence API — cancel, pause, downgrade, refund, billing, contact, and churn signals for any subscription service.

One request → full lifecycle intelligence (steps, URLs, selectors, phone numbers, difficulty ratings, confidence scores, retention offers, legal flags).

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run
uvicorn app.main:app --reload

# Open docs
open http://localhost:8000/docs
```

## API

### Get a cancellation path

```
GET /v1/cancel/netflix.com
X-API-Key: ck_your_key_here
```

```json
{
  "domain": "netflix.com",
  "service_name": "Netflix",
  "category": "streaming",
  "paths": [{
    "method": "web",
    "steps": [
      {"action": "navigate", "target": "https://www.netflix.com/account", "description": "Go to Account page"},
      {"action": "click", "target": "Manage Membership", "description": "Click Manage Membership"},
      {"action": "click", "target": "Cancel", "description": "Click Cancel"},
      {"action": "click", "target": "Finish Cancellation", "description": "Confirm"}
    ],
    "estimated_time_seconds": 120,
    "difficulty": "easy",
    "confidence": 0.97
  }]
}
```

### Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/keys` | Create a free API key (paid tiers require Stripe Checkout) |
| `GET` | `/v1/service/{domain}` | Full service intelligence (billing, contacts, all lifecycle paths) |
| `GET` | `/v1/service/{domain}/path?type=cancel` | Structured path by type (cancel/pause/downgrade/refund/account_delete) |
| `GET` | `/v1/cancel/{domain}` | Backwards-compatible cancel-only |
| `GET` | `/v1/billing/{domain}` | Billing model, trial policy, refund policy, legal flags |
| `GET` | `/v1/contact/{domain}` | Support channels, hold times, auth requirements |
| `GET` | `/v1/signals/{domain}` | Churn difficulty, retention offers, recommended actions |
| `GET` | `/v1/services` | Paginated service directory with search/filter |
| `POST` | `/v1/report` | Report a broken path |
| `POST` | `/v1/contribute` | Suggest a new path |
| `GET` | `/v1/usage` | Your API usage stats |

### Monitoring Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/stale` | List stale-flagged services/paths |
| `GET` | `/v1/stale/summary` | Confidence distribution and staleness stats |
| `GET` | `/v1/monitor/history/{domain}` | Check history for a service |
| `POST` | `/v1/monitor/run?domain=` | Trigger URL checks for a service |
| `POST` | `/v1/monitor/verify/{domain}` | Mark service as re-verified |
| `POST` | `/v1/monitor/decay` | Trigger confidence decay |
| `POST` | `/v1/monitor/report-check` | Run community report intelligence |
| `POST` | `/v1/webhooks` | Subscribe to path_stale/path_fixed events |
| `GET` | `/v1/webhooks` | List webhook subscriptions |
| `DELETE` | `/v1/webhooks/{id}` | Delete a webhook |
| `GET` | `/v1/webhooks/{id}/deliveries` | Delivery history |

### Billing Endpoints (Stripe self-serve)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/checkout/session` | Start a Stripe Checkout session to subscribe to `starter` or `growth` |
| `POST` | `/v1/checkout/portal` | Open the Stripe Billing Portal to manage or cancel |
| `POST` | `/v1/stripe/webhook` | Stripe webhook receiver (signature-verified) that drives tier changes |

Paid tiers are reachable only by completing payment — a verified
`checkout.session.completed` webhook upgrades the key; `customer.subscription.deleted`
downgrades it back to `free`. Internal provisioning can still set a tier directly via
`/v1/admin/keys/{id}/tier` or `?admin_token=`.

### Authentication

All endpoints except `/v1/keys`, `/v1/stripe/webhook`, `/health`, `/dashboard/*`, and `/`
require an `X-API-Key` header. (`/v1/stripe/webhook` is authenticated by its Stripe signature.)

```bash
curl -H "X-API-Key: ck_your_key" http://localhost:8000/v1/cancel/spotify.com
```

## Coverage

205 services across 22 categories: streaming, music, software, news, fitness, gaming, food delivery, VPN, education, dating, telecom, meal kits, finance, home, automotive, kids, lifestyle, and more.

## Production Configuration

```bash
# Database (default: SQLite; set for Postgres in production)
export DATABASE_URL=postgresql://user:pass@host:5432/cancelkit

# Log level
export LOG_LEVEL=INFO

# Disable background scheduler (for tests)
export CANCELKIT_SCHEDULER=false

# Stripe self-serve billing (test or live keys; never commit secrets)
export CANCELKIT_STRIPE_SECRET_KEY=sk_test_...
export CANCELKIT_STRIPE_WEBHOOK_SECRET=whsec_...
export CANCELKIT_STRIPE_PRICE_STARTER=price_...   # $199/mo recurring price
export CANCELKIT_STRIPE_PRICE_GROWTH=price_...    # $499/mo recurring price
export CANCELKIT_APP_BASE_URL=https://cancelkit.dev
```

### Docker

```bash
docker build -t cancelkit .
docker run -p 8000:8000 -e DATABASE_URL=postgresql://... cancelkit
```

## Background Scheduler

The API includes an automated monitoring scheduler (APScheduler):
- **Confidence decay**: daily at 03:00 UTC
- **Community report intelligence**: every 6 hours
- **High-priority URL checks** (top 50 services): daily at 04:00 UTC
- **Full URL checks** (all services): weekly on Sunday at 05:00 UTC

Scheduler status is reported in `GET /health`.

## Development

```bash
pip install -e ".[dev]"
CANCELKIT_SCHEDULER=false pytest tests/ -v
ruff check .
```

## Version

0.3.0 — 205 services, automated monitoring scheduler, Stripe self-serve billing.
