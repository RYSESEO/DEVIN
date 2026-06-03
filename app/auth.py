import secrets
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiKey, UsageRecord

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Tier hierarchy for feature gating
TIER_RANK = {"free": 0, "starter": 1, "growth": 2, "enterprise": 3}

# Which tiers can access which endpoint groups
TIER_GATES: dict[str, str] = {
    "/v1/signals": "starter",
    "/v1/billing": "starter",
    "/v1/webhooks": "growth",
    "/v1/monitor/run": "growth",
    "/v1/monitor/verify": "growth",
    "/v1/monitor/decay": "enterprise",
    "/v1/monitor/report-check": "enterprise",
    "/v1/monitor/bot/run": "enterprise",
}


def generate_api_key() -> str:
    return f"ck_{secrets.token_urlsafe(32)}"


def get_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
    db: Session = Depends(get_db),
) -> ApiKey:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key. Include X-API-Key header.")

    key_record = (
        db.query(ApiKey).filter(ApiKey.key == api_key, ApiKey.is_active.is_(True)).first()
    )
    if not key_record:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    daily_count = (
        db.query(func.count(UsageRecord.id))
        .filter(
            UsageRecord.api_key_id == key_record.id,
            UsageRecord.timestamp >= today_start,
        )
        .scalar()
    ) or 0

    monthly_count = (
        db.query(func.count(UsageRecord.id))
        .filter(
            UsageRecord.api_key_id == key_record.id,
            UsageRecord.timestamp >= month_start,
        )
        .scalar()
    ) or 0

    # Attach rate-limit metadata so middleware can set headers
    request.state.rate_limit_daily = key_record.daily_limit
    request.state.rate_limit_monthly = key_record.monthly_limit
    request.state.rate_limit_daily_used = daily_count
    request.state.rate_limit_monthly_used = monthly_count

    if daily_count >= key_record.daily_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit of {key_record.daily_limit} requests exceeded.",
            headers={
                "X-RateLimit-Limit": str(key_record.daily_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "daily",
                "Retry-After": "3600",
            },
        )

    if monthly_count >= key_record.monthly_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly limit of {key_record.monthly_limit} requests exceeded.",
            headers={
                "X-RateLimit-Limit": str(key_record.monthly_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "monthly",
                "Retry-After": "86400",
            },
        )

    return key_record


def require_tier(min_tier: str) -> Callable:
    """Dependency that enforces a minimum pricing tier."""
    required_rank = TIER_RANK.get(min_tier, 0)

    def _check(api_key: ApiKey = Depends(get_api_key)) -> ApiKey:
        key_rank = TIER_RANK.get(api_key.tier, 0)
        if key_rank < required_rank:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This endpoint requires the '{min_tier}' tier or above. "
                    f"Your current tier: '{api_key.tier}'. "
                    "Upgrade at https://cancelkit.dev/pricing"
                ),
            )
        return api_key

    return _check


def check_tier_gate(request: Request, api_key: ApiKey) -> None:
    """Check if the current request path requires a higher tier."""
    path = request.url.path
    for gate_prefix, min_tier in TIER_GATES.items():
        if path.startswith(gate_prefix):
            required_rank = TIER_RANK.get(min_tier, 0)
            key_rank = TIER_RANK.get(api_key.tier, 0)
            if key_rank < required_rank:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"This endpoint requires the '{min_tier}' tier or above. "
                        f"Your current tier: '{api_key.tier}'. "
                        "Upgrade at https://cancelkit.dev/pricing"
                    ),
                )
            break


def record_usage(db: Session, api_key: ApiKey, endpoint: str, domain: str | None = None):
    record = UsageRecord(
        api_key_id=api_key.id,
        endpoint=endpoint,
        domain_queried=domain,
    )
    db.add(record)
    db.commit()
