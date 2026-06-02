# CancelKit

REST API returning structured cancellation paths for any subscription service.

One request → full cancellation instructions (steps, URLs, selectors, phone numbers, difficulty ratings, confidence scores).

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

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/keys` | Create a free API key |
| `GET` | `/v1/cancel/{domain}` | Get cancellation paths for a service |
| `GET` | `/v1/supported/{domain}` | Check if a service is supported |
| `GET` | `/v1/services` | List all supported services (paginated, searchable) |
| `POST` | `/v1/report` | Report a broken path |
| `POST` | `/v1/contribute` | Suggest a new cancellation path |
| `GET` | `/v1/usage` | Get your API usage stats |
| `GET` | `/health` | Health check |

### Authentication

All endpoints except `/v1/keys`, `/health`, and `/` require an `X-API-Key` header.

```bash
curl -H "X-API-Key: ck_your_key" https://your-api.fly.dev/v1/cancel/spotify.com
```

## Coverage

40+ services across streaming, music, software, news, fitness, gaming, food delivery, VPN, education, dating, and more.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```
