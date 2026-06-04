import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import TIER_RANK, apply_tier, generate_api_key, get_api_key, record_usage
from app.config import TIER_LIMITS
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

ADMIN_TOKEN = os.getenv("CANCELKIT_ADMIN_TOKEN", "admin")


def _is_admin(token: str | None) -> bool:
    """True if a valid admin token was supplied. Used to gate paid-tier provisioning."""
    return bool(token) and token == ADMIN_TOKEN


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
    admin_token: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Create a new API key.

    Public callers always receive a `free` key — paid tiers are reachable only by
    paying through Stripe Checkout (`POST /v1/checkout/session`). Internal callers
    may provision a paid tier directly by supplying a valid `admin_token`.
    """
    existing = (
        db.query(ApiKey).filter(ApiKey.email == body.email, ApiKey.is_active.is_(True)).first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="An active API key already exists for this email.",
        )

    # Paid tiers require payment; only an admin token can mint one directly.
    tier = body.tier
    if tier != "free" and not _is_admin(admin_token):
        raise HTTPException(
            status_code=403,
            detail=(
                "Paid tiers require payment. Create a free key, then upgrade via "
                "POST /v1/checkout/session."
            ),
        )

    limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])

    key = ApiKey(
        key=generate_api_key(),
        name=body.name,
        email=body.email,
        tier=tier,
        daily_limit=limits["daily"],
        monthly_limit=limits["monthly"],
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


# ── API Key Management ───────────────────────────────────────────────


@router.get("/keys/me")
def get_my_key(
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Get details about the authenticated API key."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    today_count = (
        db.query(func.count(UsageRecord.id))
        .filter(UsageRecord.api_key_id == api_key.id, UsageRecord.timestamp >= today_start)
        .scalar()
    ) or 0

    month_count = (
        db.query(func.count(UsageRecord.id))
        .filter(UsageRecord.api_key_id == api_key.id, UsageRecord.timestamp >= month_start)
        .scalar()
    ) or 0

    total_count = (
        db.query(func.count(UsageRecord.id))
        .filter(UsageRecord.api_key_id == api_key.id)
        .scalar()
    ) or 0

    # Top endpoints
    top_endpoints = (
        db.query(UsageRecord.endpoint, func.count(UsageRecord.id).label("count"))
        .filter(UsageRecord.api_key_id == api_key.id)
        .group_by(UsageRecord.endpoint)
        .order_by(func.count(UsageRecord.id).desc())
        .limit(10)
        .all()
    )

    # Top domains queried
    top_domains = (
        db.query(UsageRecord.domain_queried, func.count(UsageRecord.id).label("count"))
        .filter(
            UsageRecord.api_key_id == api_key.id,
            UsageRecord.domain_queried.isnot(None),
        )
        .group_by(UsageRecord.domain_queried)
        .order_by(func.count(UsageRecord.id).desc())
        .limit(10)
        .all()
    )

    return {
        "key_prefix": api_key.key[:10] + "...",
        "name": api_key.name,
        "email": api_key.email,
        "tier": api_key.tier,
        "is_active": api_key.is_active,
        "daily_limit": api_key.daily_limit,
        "monthly_limit": api_key.monthly_limit,
        "created_at": api_key.created_at,
        "usage": {
            "today": today_count,
            "this_month": month_count,
            "all_time": total_count,
        },
        "top_endpoints": {ep: cnt for ep, cnt in top_endpoints},
        "top_domains": {dom: cnt for dom, cnt in top_domains},
    }


@router.post("/keys/rotate")
def rotate_api_key(
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Rotate the current API key. Returns a new key; the old one is immediately revoked."""
    old_prefix = api_key.key[:10]

    # Deactivate old key
    api_key.is_active = False
    api_key.updated_at = datetime.now(timezone.utc)

    # Create replacement with same config
    new_key = ApiKey(
        key=generate_api_key(),
        name=api_key.name,
        email=api_key.email,
        tier=api_key.tier,
        daily_limit=api_key.daily_limit,
        monthly_limit=api_key.monthly_limit,
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    return {
        "new_key": new_key.key,
        "old_key_prefix": old_prefix + "...",
        "tier": new_key.tier,
        "daily_limit": new_key.daily_limit,
        "monthly_limit": new_key.monthly_limit,
        "message": "Key rotated. Old key is now inactive. Update your integrations.",
    }


@router.delete("/keys/revoke")
def revoke_api_key(
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Revoke (deactivate) the current API key. Cannot be undone."""
    api_key.is_active = False
    api_key.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "key_prefix": api_key.key[:10] + "...",
        "status": "revoked",
        "message": "API key has been permanently revoked.",
    }


@router.post("/keys/upgrade")
def upgrade_key_tier(
    new_tier: str,
    admin_token: str | None = Query(None),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Internal/admin instant tier change. Cannot downgrade.

    Self-serve customers must upgrade by paying through `POST /v1/checkout/session`;
    this endpoint exists for internal provisioning and requires a valid `admin_token`.
    """
    if not _is_admin(admin_token):
        raise HTTPException(
            status_code=403,
            detail="Self-serve upgrades go through POST /v1/checkout/session (Stripe).",
        )

    valid_tiers = list(TIER_RANK.keys())
    if new_tier not in TIER_RANK:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier '{new_tier}'. Valid tiers: {valid_tiers}",
        )

    current_rank = TIER_RANK.get(api_key.tier, 0)
    new_rank = TIER_RANK.get(new_tier, 0)

    if new_rank <= current_rank:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot downgrade from '{api_key.tier}' to '{new_tier}'. "
            "Contact support for downgrades.",
        )

    old_tier = api_key.tier
    apply_tier(api_key, new_tier, db)

    return {
        "key_prefix": api_key.key[:10] + "...",
        "old_tier": old_tier,
        "new_tier": new_tier,
        "daily_limit": api_key.daily_limit,
        "monthly_limit": api_key.monthly_limit,
        "message": f"Upgraded from {old_tier} to {new_tier}.",
    }
