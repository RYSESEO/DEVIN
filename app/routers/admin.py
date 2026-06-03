"""Admin API — internal CRUD for services, paths, contacts, reports, and API keys.

Protected by CANCELKIT_ADMIN_TOKEN (env var or header).
"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import TIER_LIMITS
from app.database import get_db
from app.models import (
    ApiKey,
    ContactInfo,
    LifecyclePath,
    MonitorResult,
    Report,
    Service,
    StaleFlag,
    UsageRecord,
    WebhookDelivery,
    WebhookSubscription,
)

router = APIRouter(prefix="/v1/admin", tags=["Admin"])

ADMIN_TOKEN = os.getenv("CANCELKIT_ADMIN_TOKEN", "admin")


# ── Admin auth ────────────────────────────────────────────────────────


def require_admin(token: str = Query(None, alias="admin_token")):
    """Check admin token from query param or env. Returns True or raises 403."""
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token.")
    return True


# ── Request schemas ───────────────────────────────────────────────────


class ServiceCreate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(..., min_length=1, max_length=100)
    logo_url: str | None = None
    billing_model: str | None = None
    trial_policy: dict | None = None
    refund_policy: dict | None = None
    legal_flags: list[str] | None = None


class ServiceUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    logo_url: str | None = None
    billing_model: str | None = None
    trial_policy: dict | None = None
    refund_policy: dict | None = None
    legal_flags: list[str] | None = None


class PathCreate(BaseModel):
    path_type: str = Field(default="cancel")
    method: str = Field(...)
    steps: list[dict] = Field(...)
    estimated_time_seconds: int = Field(..., ge=10)
    difficulty: str = Field(...)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    complexity_score: int | None = None
    retention_offers: list[dict] | None = None
    legal_flags: list[str] | None = None
    notes: str | None = None


class PathUpdate(BaseModel):
    path_type: str | None = None
    method: str | None = None
    steps: list[dict] | None = None
    estimated_time_seconds: int | None = None
    difficulty: str | None = None
    confidence: float | None = None
    complexity_score: int | None = None
    retention_offers: list[dict] | None = None
    legal_flags: list[str] | None = None
    notes: str | None = None


class ContactCreate(BaseModel):
    channel: str = Field(...)
    target: str = Field(...)
    hours: str | None = None
    expected_hold_minutes: int | None = None
    auth_required: bool = False
    notes: str | None = None


class ContactUpdate(BaseModel):
    channel: str | None = None
    target: str | None = None
    hours: str | None = None
    expected_hold_minutes: int | None = None
    auth_required: bool | None = None
    notes: str | None = None


class ReportStatusUpdate(BaseModel):
    status: str = Field(..., pattern=r"^(pending|investigating|resolved|dismissed)$")


# ── Platform overview ─────────────────────────────────────────────────


@router.get("/overview")
def admin_overview(
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Full platform overview: totals, health, stale flags, low-confidence paths."""
    total_services = db.query(func.count(Service.id)).scalar() or 0
    total_paths = db.query(func.count(LifecyclePath.id)).scalar() or 0
    total_contacts = db.query(func.count(ContactInfo.id)).scalar() or 0
    total_reports = db.query(func.count(Report.id)).scalar() or 0
    pending_reports = (
        db.query(func.count(Report.id))
        .filter(Report.status == "pending")
        .scalar()
        or 0
    )
    total_keys = (
        db.query(func.count(ApiKey.id)).filter(ApiKey.is_active.is_(True)).scalar() or 0
    )
    total_usage = db.query(func.count(UsageRecord.id)).scalar() or 0
    total_checks = db.query(func.count(MonitorResult.id)).scalar() or 0
    open_stale = (
        db.query(func.count(StaleFlag.id))
        .filter(StaleFlag.resolved.is_(False))
        .scalar()
        or 0
    )
    avg_confidence = db.query(func.avg(LifecyclePath.confidence)).scalar()
    active_webhooks = (
        db.query(func.count(WebhookSubscription.id))
        .filter(WebhookSubscription.is_active.is_(True))
        .scalar()
        or 0
    )

    # Categories breakdown
    categories = dict(
        db.query(Service.category, func.count(Service.id))
        .group_by(Service.category)
        .all()
    )

    # Tier breakdown of active keys
    tiers = dict(
        db.query(ApiKey.tier, func.count(ApiKey.id))
        .filter(ApiKey.is_active.is_(True))
        .group_by(ApiKey.tier)
        .all()
    )

    return {
        "totals": {
            "services": total_services,
            "lifecycle_paths": total_paths,
            "contacts": total_contacts,
            "reports": total_reports,
            "pending_reports": pending_reports,
            "active_keys": total_keys,
            "total_api_calls": total_usage,
        },
        "monitoring": {
            "total_checks": total_checks,
            "open_stale_flags": open_stale,
            "active_webhooks": active_webhooks,
            "avg_confidence": round(avg_confidence, 3) if avg_confidence else None,
        },
        "categories": categories,
        "key_tiers": tiers,
    }


# ── Service CRUD ──────────────────────────────────────────────────────


@router.get("/services")
def list_services(
    category: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """List all services with optional filters."""
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
        path_count = (
            db.query(func.count(LifecyclePath.id))
            .filter(LifecyclePath.service_id == svc.id)
            .scalar()
            or 0
        )
        contact_count = (
            db.query(func.count(ContactInfo.id))
            .filter(ContactInfo.service_id == svc.id)
            .scalar()
            or 0
        )
        results.append({
            "id": svc.id,
            "domain": svc.domain,
            "name": svc.name,
            "category": svc.category,
            "billing_model": svc.billing_model,
            "legal_flags": svc.legal_flags or [],
            "path_count": path_count,
            "contact_count": contact_count,
            "created_at": svc.created_at.isoformat() if svc.created_at else None,
            "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
        })

    return {"services": results, "total": total, "page": page, "per_page": per_page}


@router.post("/services", status_code=201)
def create_service(
    body: ServiceCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Create a new service."""
    existing = db.query(Service).filter(Service.domain == body.domain).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Service '{body.domain}' already exists.")

    svc = Service(
        domain=body.domain,
        name=body.name,
        category=body.category.lower(),
        logo_url=body.logo_url,
        billing_model=body.billing_model,
        trial_policy=body.trial_policy,
        refund_policy=body.refund_policy,
        legal_flags=body.legal_flags,
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return {"id": svc.id, "domain": svc.domain, "name": svc.name, "status": "created"}


@router.get("/services/{domain}")
def get_service_detail(
    domain: str,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Get full service detail including all paths and contacts."""
    svc = db.query(Service).filter(Service.domain == domain).first()
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")

    paths = db.query(LifecyclePath).filter(LifecyclePath.service_id == svc.id).all()
    contacts = db.query(ContactInfo).filter(ContactInfo.service_id == svc.id).all()
    stale_flags = (
        db.query(StaleFlag)
        .filter(StaleFlag.service_id == svc.id, StaleFlag.resolved.is_(False))
        .all()
    )

    return {
        "id": svc.id,
        "domain": svc.domain,
        "name": svc.name,
        "category": svc.category,
        "billing_model": svc.billing_model,
        "trial_policy": svc.trial_policy,
        "refund_policy": svc.refund_policy,
        "legal_flags": svc.legal_flags or [],
        "created_at": svc.created_at.isoformat() if svc.created_at else None,
        "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
        "paths": [
            {
                "id": p.id,
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
                "last_verified_at": (
                    p.last_verified_at.isoformat() if p.last_verified_at else None
                ),
            }
            for p in paths
        ],
        "contacts": [
            {
                "id": c.id,
                "channel": c.channel,
                "target": c.target,
                "hours": c.hours,
                "expected_hold_minutes": c.expected_hold_minutes,
                "auth_required": c.auth_required,
                "notes": c.notes,
            }
            for c in contacts
        ],
        "stale_flags": [
            {
                "id": f.id,
                "reason": f.reason,
                "severity": f.severity,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in stale_flags
        ],
    }


@router.patch("/services/{domain}")
def update_service(
    domain: str,
    body: ServiceUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Update service metadata."""
    svc = db.query(Service).filter(Service.domain == domain).first()
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(svc, field, value)
    svc.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(svc)

    return {
        "domain": svc.domain,
        "name": svc.name,
        "status": "updated",
        "fields": list(update_data.keys()),
    }


@router.delete("/services/{domain}")
def delete_service(
    domain: str,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Delete a service and all associated paths, contacts, and monitor results."""
    svc = db.query(Service).filter(Service.domain == domain).first()
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")

    db.query(MonitorResult).filter(MonitorResult.service_id == svc.id).delete()
    db.query(StaleFlag).filter(StaleFlag.service_id == svc.id).delete()
    db.delete(svc)
    db.commit()

    return {"domain": domain, "status": "deleted"}


# ── Path CRUD ─────────────────────────────────────────────────────────


@router.post("/services/{domain}/paths", status_code=201)
def create_path(
    domain: str,
    body: PathCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Add a lifecycle path to a service."""
    svc = db.query(Service).filter(Service.domain == domain).first()
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")

    path = LifecyclePath(
        service_id=svc.id,
        path_type=body.path_type,
        method=body.method,
        steps=body.steps,
        estimated_time_seconds=body.estimated_time_seconds,
        difficulty=body.difficulty,
        confidence=body.confidence,
        complexity_score=body.complexity_score,
        retention_offers=body.retention_offers,
        legal_flags=body.legal_flags,
        notes=body.notes,
    )
    db.add(path)
    db.commit()
    db.refresh(path)

    return {"id": path.id, "path_type": path.path_type, "method": path.method, "status": "created"}


@router.patch("/paths/{path_id}")
def update_path(
    path_id: int,
    body: PathUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Update a lifecycle path."""
    path = db.get(LifecyclePath, path_id)
    if not path:
        raise HTTPException(status_code=404, detail="Path not found.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(path, field, value)
    path.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(path)

    return {
        "id": path.id,
        "path_type": path.path_type,
        "status": "updated",
        "fields": list(update_data.keys()),
    }


@router.delete("/paths/{path_id}")
def delete_path(
    path_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Delete a lifecycle path."""
    path = db.get(LifecyclePath, path_id)
    if not path:
        raise HTTPException(status_code=404, detail="Path not found.")

    db.query(MonitorResult).filter(MonitorResult.path_id == path_id).delete()
    db.query(StaleFlag).filter(StaleFlag.path_id == path_id).delete()
    db.delete(path)
    db.commit()

    return {"id": path_id, "status": "deleted"}


@router.post("/paths/{path_id}/verify")
def verify_path(
    path_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Mark a path as manually verified (resets confidence to 0.95)."""
    path = db.get(LifecyclePath, path_id)
    if not path:
        raise HTTPException(status_code=404, detail="Path not found.")

    path.confidence = 0.95
    path.last_verified_at = datetime.now(timezone.utc)
    path.updated_at = datetime.now(timezone.utc)

    # Resolve related stale flags
    db.query(StaleFlag).filter(
        StaleFlag.path_id == path_id, StaleFlag.resolved.is_(False)
    ).update({"resolved": True, "resolved_at": datetime.now(timezone.utc)})

    db.commit()

    return {"id": path_id, "confidence": 0.95, "status": "verified"}


# ── Contact CRUD ──────────────────────────────────────────────────────


@router.post("/services/{domain}/contacts", status_code=201)
def create_contact(
    domain: str,
    body: ContactCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Add a contact to a service."""
    svc = db.query(Service).filter(Service.domain == domain).first()
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")

    contact = ContactInfo(
        service_id=svc.id,
        channel=body.channel,
        target=body.target,
        hours=body.hours,
        expected_hold_minutes=body.expected_hold_minutes,
        auth_required=body.auth_required,
        notes=body.notes,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)

    return {"id": contact.id, "channel": contact.channel, "status": "created"}


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: int,
    body: ContactUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Update a contact."""
    contact = db.get(ContactInfo, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)

    return {"id": contact.id, "channel": contact.channel, "status": "updated"}


@router.delete("/contacts/{contact_id}")
def delete_contact(
    contact_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Delete a contact."""
    contact = db.get(ContactInfo, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found.")

    db.delete(contact)
    db.commit()

    return {"id": contact_id, "status": "deleted"}


# ── Report triage ─────────────────────────────────────────────────────


@router.get("/reports")
def list_reports(
    status: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """List reports with optional status filter."""
    query = db.query(Report)
    if status:
        query = query.filter(Report.status == status)

    total = query.count()
    reports = (
        query.order_by(Report.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "reports": [
            {
                "id": r.id,
                "service_domain": r.service_domain,
                "report_type": r.report_type,
                "description": r.description,
                "status": r.status,
                "api_key_id": r.api_key_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in reports
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.patch("/reports/{report_id}")
def update_report_status(
    report_id: int,
    body: ReportStatusUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Update a report's status (triage)."""
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    report.status = body.status
    report.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"id": report.id, "status": report.status, "message": "Report updated."}


@router.delete("/reports/{report_id}")
def delete_report(
    report_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Delete a report."""
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    db.delete(report)
    db.commit()

    return {"id": report_id, "status": "deleted"}


# ── API Key management ────────────────────────────────────────────────


@router.get("/keys")
def list_api_keys(
    tier: str | None = None,
    active_only: bool = True,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """List all API keys with usage stats."""
    query = db.query(ApiKey)
    if active_only:
        query = query.filter(ApiKey.is_active.is_(True))
    if tier:
        query = query.filter(ApiKey.tier == tier)

    total = query.count()
    keys = (
        query.order_by(ApiKey.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    results = []
    for k in keys:
        total_usage = (
            db.query(func.count(UsageRecord.id))
            .filter(UsageRecord.api_key_id == k.id)
            .scalar()
            or 0
        )
        results.append({
            "id": k.id,
            "key_prefix": k.key[:10] + "...",
            "name": k.name,
            "email": k.email,
            "tier": k.tier,
            "is_active": k.is_active,
            "daily_limit": k.daily_limit,
            "monthly_limit": k.monthly_limit,
            "total_usage": total_usage,
            "created_at": k.created_at.isoformat() if k.created_at else None,
        })

    return {"keys": results, "total": total, "page": page, "per_page": per_page}


@router.patch("/keys/{key_id}/tier")
def admin_change_tier(
    key_id: int,
    new_tier: str = Query(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Admin: change a key's tier (up or down)."""
    key = db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="API key not found.")

    valid_tiers = list(TIER_LIMITS.keys())
    if new_tier not in valid_tiers:
        raise HTTPException(
            status_code=400, detail=f"Invalid tier '{new_tier}'. Valid: {valid_tiers}"
        )

    old_tier = key.tier
    limits = TIER_LIMITS[new_tier]
    key.tier = new_tier
    key.daily_limit = limits["daily"]
    key.monthly_limit = limits["monthly"]
    key.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "id": key.id,
        "old_tier": old_tier,
        "new_tier": new_tier,
        "daily_limit": key.daily_limit,
        "monthly_limit": key.monthly_limit,
    }


@router.patch("/keys/{key_id}/deactivate")
def admin_deactivate_key(
    key_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Admin: deactivate an API key."""
    key = db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="API key not found.")

    key.is_active = False
    key.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"id": key.id, "key_prefix": key.key[:10] + "...", "status": "deactivated"}


# ── Stale flag management ─────────────────────────────────────────────


@router.get("/stale-flags")
def list_stale_flags(
    resolved: bool = False,
    severity: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """List stale flags."""
    query = db.query(StaleFlag).filter(StaleFlag.resolved.is_(resolved))
    if severity:
        query = query.filter(StaleFlag.severity == severity)

    total = query.count()
    flags = (
        query.order_by(StaleFlag.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    results = []
    for f in flags:
        svc = db.get(Service, f.service_id)
        path = db.get(LifecyclePath, f.path_id) if f.path_id else None
        results.append({
            "id": f.id,
            "domain": svc.domain if svc else None,
            "service_name": svc.name if svc else None,
            "path_type": path.path_type if path else "all",
            "path_id": f.path_id,
            "reason": f.reason,
            "severity": f.severity,
            "resolved": f.resolved,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        })

    return {"flags": results, "total": total, "page": page, "per_page": per_page}


@router.post("/stale-flags/{flag_id}/resolve")
def resolve_stale_flag(
    flag_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Mark a stale flag as resolved."""
    flag = db.get(StaleFlag, flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Stale flag not found.")

    flag.resolved = True
    flag.resolved_at = datetime.now(timezone.utc)
    db.commit()

    return {"id": flag_id, "status": "resolved"}


# ── Bulk operations ───────────────────────────────────────────────────


@router.post("/bulk/verify-all")
def bulk_verify_all(
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Mark all paths as verified (resets confidence to 0.95 for all)."""
    now = datetime.now(timezone.utc)
    count = (
        db.query(LifecyclePath)
        .update({"confidence": 0.95, "last_verified_at": now, "updated_at": now})
    )
    db.query(StaleFlag).filter(StaleFlag.resolved.is_(False)).update(
        {"resolved": True, "resolved_at": now}
    )
    db.commit()

    return {"paths_verified": count, "status": "all_verified"}


@router.post("/bulk/resolve-all-flags")
def bulk_resolve_flags(
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """Resolve all open stale flags."""
    now = datetime.now(timezone.utc)
    count = (
        db.query(StaleFlag)
        .filter(StaleFlag.resolved.is_(False))
        .update({"resolved": True, "resolved_at": now})
    )
    db.commit()

    return {"flags_resolved": count, "status": "all_resolved"}


# ── Webhook management ────────────────────────────────────────────────


@router.get("/webhooks")
def list_all_webhooks(
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """List all webhook subscriptions across all keys."""
    subs = db.query(WebhookSubscription).all()
    results = []
    for s in subs:
        delivery_count = (
            db.query(func.count(WebhookDelivery.id))
            .filter(WebhookDelivery.subscription_id == s.id)
            .scalar()
            or 0
        )
        failed_count = (
            db.query(func.count(WebhookDelivery.id))
            .filter(
                WebhookDelivery.subscription_id == s.id,
                WebhookDelivery.success.is_(False),
            )
            .scalar()
            or 0
        )
        results.append({
            "id": s.id,
            "api_key_id": s.api_key_id,
            "url": s.url,
            "events": s.events,
            "is_active": s.is_active,
            "delivery_count": delivery_count,
            "failed_count": failed_count,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    return {"webhooks": results, "total": len(results)}
