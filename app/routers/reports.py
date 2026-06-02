from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import generate_api_key, get_api_key, record_usage
from app.config import settings
from app.database import get_db
from app.models import ApiKey, Report, UsageRecord
from app.schemas import (
    ApiKeyCreate,
    ApiKeyResponse,
    ContributeCreate,
    ReportCreate,
    ReportResponse,
    UsageStatsResponse,
)

router = APIRouter(prefix="/v1", tags=["Reports & Keys"])


@router.post("/report", response_model=ReportResponse, status_code=201)
def submit_report(
    body: ReportCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Report a broken or outdated cancellation path."""
    record_usage(db, api_key, "/v1/report", body.service_domain)

    report = Report(
        api_key_id=api_key.id,
        service_domain=body.service_domain,
        report_type=body.report_type,
        description=body.description,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.post("/contribute", status_code=202)
def contribute_path(
    body: ContributeCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Suggest a new lifecycle path for review."""
    record_usage(db, api_key, "/v1/contribute", body.domain)

    report = Report(
        api_key_id=api_key.id,
        service_domain=body.domain,
        report_type="new_service",
        description=(
            f"Contributed {body.path_type} path for "
            f"{body.service_name} ({body.domain}). "
            f"Method: {body.method}, Steps: {len(body.steps)}, "
            f"Difficulty: {body.difficulty}. Notes: {body.notes or 'None'}"
        ),
    )
    db.add(report)
    db.commit()

    return {"status": "accepted", "message": "Contribution submitted for review. Thank you."}


@router.post("/keys", response_model=ApiKeyResponse, status_code=201)
def create_api_key(
    body: ApiKeyCreate,
    db: Session = Depends(get_db),
):
    """Create a new free-tier API key."""
    existing = (
        db.query(ApiKey).filter(ApiKey.email == body.email, ApiKey.is_active.is_(True)).first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="An active API key already exists for this email.",
        )

    key = ApiKey(
        key=generate_api_key(),
        name=body.name,
        email=body.email,
        tier="free",
        daily_limit=settings.default_daily_limit,
        monthly_limit=settings.default_monthly_limit,
    )
    db.add(key)
    db.commit()
    db.refresh(key)

    return ApiKeyResponse(
        key=key.key,
        name=key.name,
        email=key.email,
        tier=key.tier,
        daily_limit=key.daily_limit,
        monthly_limit=key.monthly_limit,
    )


@router.get("/usage", response_model=UsageStatsResponse)
def get_usage_stats(
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Get API usage statistics for the authenticated key."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    today_count = (
        db.query(func.count(UsageRecord.id))
        .filter(UsageRecord.api_key_id == api_key.id, UsageRecord.timestamp >= today_start)
        .scalar()
    )

    month_count = (
        db.query(func.count(UsageRecord.id))
        .filter(UsageRecord.api_key_id == api_key.id, UsageRecord.timestamp >= month_start)
        .scalar()
    )

    return UsageStatsResponse(
        today=today_count or 0,
        this_month=month_count or 0,
        daily_limit=api_key.daily_limit,
        monthly_limit=api_key.monthly_limit,
        tier=api_key.tier,
    )
