"""Dashboard API endpoints — aggregate stats and admin data for the CancelKit dashboard."""

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiKey, ContactInfo, LifecyclePath, Report, Service, UsageRecord

router = APIRouter(prefix="/v1/dashboard", tags=["Dashboard"])


@router.get("/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """Aggregate platform statistics — no auth required."""
    total_services = db.query(func.count(Service.id)).scalar() or 0
    total_paths = db.query(func.count(LifecyclePath.id)).scalar() or 0
    total_contacts = db.query(func.count(ContactInfo.id)).scalar() or 0
    total_reports = db.query(func.count(Report.id)).scalar() or 0
    total_keys = db.query(func.count(ApiKey.id)).filter(ApiKey.is_active.is_(True)).scalar() or 0
    total_usage = db.query(func.count(UsageRecord.id)).scalar() or 0

    categories = (
        db.query(Service.category, func.count(Service.id))
        .group_by(Service.category)
        .all()
    )

    path_types = (
        db.query(LifecyclePath.path_type, func.count(LifecyclePath.id))
        .group_by(LifecyclePath.path_type)
        .all()
    )

    difficulties = (
        db.query(LifecyclePath.difficulty, func.count(LifecyclePath.id))
        .group_by(LifecyclePath.difficulty)
        .all()
    )

    methods = (
        db.query(LifecyclePath.method, func.count(LifecyclePath.id))
        .group_by(LifecyclePath.method)
        .all()
    )

    return {
        "totals": {
            "services": total_services,
            "lifecycle_paths": total_paths,
            "contacts": total_contacts,
            "reports": total_reports,
            "active_keys": total_keys,
            "total_api_calls": total_usage,
        },
        "categories": {cat: count for cat, count in categories},
        "path_types": {pt: count for pt, count in path_types},
        "difficulties": {d: count for d, count in difficulties},
        "methods": {m: count for m, count in methods},
    }


@router.get("/services/browse")
def browse_services(
    category: str | None = Query(None),
    search: str | None = Query(None),
    difficulty: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Browse services with filters — no auth required (dashboard view)."""
    query = db.query(Service)

    if category:
        query = query.filter(Service.category == category.lower())
    if search:
        pattern = f"%{search.lower()}%"
        query = query.filter(
            (Service.name.ilike(pattern)) | (Service.domain.ilike(pattern))
        )

    total = query.count()
    services = (
        query.order_by(Service.name)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    results = []
    for svc in services:
        paths = (
            db.query(LifecyclePath)
            .filter(LifecyclePath.service_id == svc.id)
            .all()
        )
        cancel_paths = [p for p in paths if p.path_type == "cancel"]
        svc_difficulty = cancel_paths[0].difficulty if cancel_paths else "unknown"

        if difficulty and svc_difficulty != difficulty:
            continue

        results.append({
            "domain": svc.domain,
            "name": svc.name,
            "category": svc.category,
            "billing_model": svc.billing_model,
            "difficulty": svc_difficulty,
            "methods": sorted({p.method for p in paths}),
            "path_types": sorted({p.path_type for p in paths}),
            "legal_flags": svc.legal_flags or [],
            "complexity_score": max(
                (p.complexity_score for p in paths if p.complexity_score), default=0
            ),
        })

    return {
        "services": results,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/service/{domain}")
def get_service_preview(domain: str, db: Session = Depends(get_db)):
    """Get full service detail for dashboard preview — no auth required."""
    service = db.query(Service).filter(Service.domain == domain).first()
    if not service:
        return {"error": f"Service '{domain}' not found"}

    paths = (
        db.query(LifecyclePath).filter(LifecyclePath.service_id == service.id).all()
    )
    contacts = (
        db.query(ContactInfo).filter(ContactInfo.service_id == service.id).all()
    )

    return {
        "domain": service.domain,
        "name": service.name,
        "category": service.category,
        "billing_model": service.billing_model,
        "trial_policy": service.trial_policy,
        "refund_policy": service.refund_policy,
        "legal_flags": service.legal_flags or [],
        "contacts": [
            {
                "channel": c.channel,
                "target": c.target,
                "hours": c.hours,
                "expected_hold_minutes": c.expected_hold_minutes,
                "auth_required": c.auth_required,
                "notes": c.notes,
            }
            for c in contacts
        ],
        "paths": [
            {
                "path_type": p.path_type,
                "method": p.method,
                "difficulty": p.difficulty,
                "estimated_time_seconds": p.estimated_time_seconds,
                "confidence": p.confidence,
                "complexity_score": p.complexity_score,
                "retention_offers": p.retention_offers,
                "legal_flags": p.legal_flags,
                "notes": p.notes,
                "steps": p.steps,
                "last_verified_at": p.last_verified_at.isoformat() if p.last_verified_at else None,
            }
            for p in paths
        ],
    }


@router.get("/reports/recent")
def get_recent_reports(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Recent reports — no auth required for dashboard."""
    reports = (
        db.query(Report)
        .order_by(Report.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "service_domain": r.service_domain,
            "report_type": r.report_type,
            "description": r.description,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]


@router.get("/usage/chart")
def get_usage_chart(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Usage data aggregated by day for charting — no auth required."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    records = (
        db.query(UsageRecord)
        .filter(UsageRecord.timestamp >= start.replace(day=max(1, start.day - days)))
        .all()
    )

    daily: Counter[str] = Counter()
    endpoints: Counter[str] = Counter()
    for r in records:
        if r.timestamp:
            day_key = r.timestamp.strftime("%Y-%m-%d")
            daily[day_key] += 1
        endpoints[r.endpoint] += 1

    return {
        "daily": dict(sorted(daily.items())),
        "endpoints": dict(endpoints.most_common(10)),
        "total": len(records),
    }
