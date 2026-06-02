import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiKey, UsageRecord

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def generate_api_key() -> str:
    return f"ck_{secrets.token_urlsafe(32)}"


def get_api_key(
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
    )

    monthly_count = (
        db.query(func.count(UsageRecord.id))
        .filter(
            UsageRecord.api_key_id == key_record.id,
            UsageRecord.timestamp >= month_start,
        )
        .scalar()
    )

    if daily_count >= key_record.daily_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit of {key_record.daily_limit} requests exceeded.",
        )

    if monthly_count >= key_record.monthly_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly limit of {key_record.monthly_limit} requests exceeded.",
        )

    return key_record


def record_usage(db: Session, api_key: ApiKey, endpoint: str, domain: str | None = None):
    record = UsageRecord(
        api_key_id=api_key.id,
        endpoint=endpoint,
        domain_queried=domain,
    )
    db.add(record)
    db.commit()
