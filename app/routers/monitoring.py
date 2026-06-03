"""Monitoring & Webhooks API — staleness detection, webhook subscriptions, admin ops."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth import check_tier_gate, get_api_key, record_usage
from app.database import get_db
from app.models import (
    ApiKey,
    LifecyclePath,
    MonitorResult,
    Service,
    StaleFlag,
    WebhookDelivery,
    WebhookSubscription,
)
from app.monitor import (
    CONFIDENCE_DECAY_PER_DAY,
    CONFIDENCE_FLOOR,
    apply_confidence_decay,
    check_community_reports,
    deliver_webhooks,
    run_monitor_check,
)

router = APIRouter(prefix="/v1", tags=["Monitoring & Webhooks"])


# ── Staleness endpoints ──────────────────────────────────────────────


@router.get("/stale")
def get_stale_services(
    severity: str | None = Query(None, pattern=r"^(warning|critical)$"),
    resolved: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """List services/paths flagged as stale (changed or unreliable)."""
    record_usage(db, api_key, "/v1/stale")

    query = db.query(StaleFlag).filter(StaleFlag.resolved == resolved)
    if severity:
        query = query.filter(StaleFlag.severity == severity)

    total = query.count()
    flags = (
        query.order_by(StaleFlag.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    items = []
    for flag in flags:
        service = db.get(Service, flag.service_id)
        path = db.get(LifecyclePath, flag.path_id) if flag.path_id else None
        items.append(
            {
                "id": flag.id,
                "domain": service.domain if service else None,
                "service_name": service.name if service else None,
                "path_type": path.path_type if path else None,
                "reason": flag.reason,
                "severity": flag.severity,
                "resolved": flag.resolved,
                "resolved_at": flag.resolved_at,
                "flagged_at": flag.created_at,
            }
        )

    return {
        "stale_flags": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/stale/summary")
def stale_summary(
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Summary of staleness across all services."""
    record_usage(db, api_key, "/v1/stale/summary")

    total_flags = db.query(StaleFlag).filter(StaleFlag.resolved.is_(False)).count()
    critical = (
        db.query(StaleFlag)
        .filter(StaleFlag.resolved.is_(False), StaleFlag.severity == "critical")
        .count()
    )
    warning = (
        db.query(StaleFlag)
        .filter(StaleFlag.resolved.is_(False), StaleFlag.severity == "warning")
        .count()
    )

    # Confidence distribution
    paths = db.query(LifecyclePath).all()
    low_confidence = sum(1 for p in paths if p.confidence < 0.7)
    medium_confidence = sum(1 for p in paths if 0.7 <= p.confidence < 0.9)
    high_confidence = sum(1 for p in paths if p.confidence >= 0.9)

    # Age distribution — handle naive datetimes from SQLite
    now = datetime.now(timezone.utc)

    def days_since(dt: datetime | None) -> int:
        if not dt:
            return 999
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).days

    stale_30d = sum(1 for p in paths if days_since(p.last_verified_at) > 30)
    stale_7d = sum(1 for p in paths if days_since(p.last_verified_at) > 7)

    return {
        "open_flags": {"total": total_flags, "critical": critical, "warning": warning},
        "confidence_distribution": {
            "high": high_confidence,
            "medium": medium_confidence,
            "low": low_confidence,
        },
        "verification_age": {
            "stale_over_7d": stale_7d,
            "stale_over_30d": stale_30d,
            "total_paths": len(paths),
        },
        "decay_rate": {
            "per_day": CONFIDENCE_DECAY_PER_DAY,
            "floor": CONFIDENCE_FLOOR,
        },
    }


@router.get("/monitor/history/{domain}")
def monitor_history(
    domain: str,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Get monitoring check history for a specific service."""
    record_usage(db, api_key, "/v1/monitor/history", domain)

    service = db.query(Service).filter(Service.domain == domain).first()
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")

    results = (
        db.query(MonitorResult)
        .filter(MonitorResult.service_id == service.id)
        .order_by(MonitorResult.created_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "domain": domain,
        "checks": [
            {
                "url": r.url_checked,
                "http_status": r.http_status,
                "dom_hash": r.dom_hash,
                "changed": r.changed,
                "error": r.error,
                "check_type": r.check_type,
                "checked_at": r.created_at,
            }
            for r in results
        ],
    }


# ── Admin / verification endpoints ──────────────────────────────────


@router.post("/monitor/run")
def trigger_monitor_check(
    request: Request,
    domain: str = Query(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Manually trigger a monitor check for a specific service. Requires growth tier."""
    check_tier_gate(request, api_key)
    record_usage(db, api_key, "/v1/monitor/run", domain)

    service = db.query(Service).filter(Service.domain == domain).first()
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")

    paths = (
        db.query(LifecyclePath)
        .filter(LifecyclePath.service_id == service.id)
        .all()
    )

    total_checks = 0
    changes = 0
    for path in paths:
        results = run_monitor_check(db, service, path)
        total_checks += len(results)
        changes += sum(1 for r in results if r.changed)

    db.commit()

    # Fire webhooks for any changes
    if changes > 0:
        payload = {
            "event": "path_stale",
            "domain": service.domain,
            "service_name": service.name,
            "reason": f"Manual check detected {changes} URL change(s)",
            "severity": "warning",
        }
        deliver_webhooks(db, "path_stale", payload)

    return {
        "domain": domain,
        "urls_checked": total_checks,
        "changes_detected": changes,
        "status": "changes_detected" if changes else "all_ok",
    }


@router.post("/monitor/verify/{domain}")
def mark_verified(
    domain: str,
    request: Request,
    path_type: str | None = Query(None),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Mark a service/path as manually re-verified. Requires growth tier."""
    check_tier_gate(request, api_key)
    record_usage(db, api_key, "/v1/monitor/verify", domain)

    service = db.query(Service).filter(Service.domain == domain).first()
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")

    now = datetime.now(timezone.utc)
    query = db.query(LifecyclePath).filter(LifecyclePath.service_id == service.id)
    if path_type:
        query = query.filter(LifecyclePath.path_type == path_type)

    paths = query.all()
    if not paths:
        raise HTTPException(status_code=404, detail="No matching paths found.")

    for path in paths:
        path.confidence = 0.95
        path.last_verified_at = now

    # Resolve any stale flags
    flag_query = db.query(StaleFlag).filter(
        StaleFlag.service_id == service.id,
        StaleFlag.resolved.is_(False),
    )
    if path_type:
        path_ids = [p.id for p in paths]
        flag_query = flag_query.filter(
            (StaleFlag.path_id.in_(path_ids)) | (StaleFlag.path_id.is_(None))
        )

    resolved_count = 0
    for flag in flag_query.all():
        flag.resolved = True
        flag.resolved_at = now
        resolved_count += 1

    db.commit()

    # Fire path_fixed webhook
    payload = {
        "event": "path_fixed",
        "domain": service.domain,
        "service_name": service.name,
        "path_type": path_type or "all",
        "verified_at": now.isoformat(),
        "stale_flags_resolved": resolved_count,
    }
    deliver_webhooks(db, "path_fixed", payload)

    return {
        "domain": domain,
        "paths_updated": len(paths),
        "stale_flags_resolved": resolved_count,
        "new_confidence": 0.95,
        "verified_at": now.isoformat(),
    }


@router.post("/monitor/decay")
def trigger_confidence_decay(
    request: Request,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Manually trigger confidence decay across all paths. Requires enterprise tier."""
    check_tier_gate(request, api_key)
    record_usage(db, api_key, "/v1/monitor/decay")
    updated = apply_confidence_decay(db)
    return {"paths_decayed": updated}


@router.post("/monitor/report-check")
def trigger_report_check(
    request: Request,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Manually trigger community report intelligence check. Requires enterprise tier."""
    check_tier_gate(request, api_key)
    record_usage(db, api_key, "/v1/monitor/report-check")
    flagged = check_community_reports(db)
    return {"services_auto_flagged": flagged}


# ── Webhook subscription endpoints ──────────────────────────────────


@router.post("/webhooks", status_code=201)
def create_webhook(
    request: Request,
    url: str = Query(..., min_length=10),
    events: str = Query(
        "path_stale,path_fixed",
        description="Comma-separated event types: path_stale, path_fixed",
    ),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Subscribe to webhook notifications. Requires growth tier."""
    check_tier_gate(request, api_key)
    record_usage(db, api_key, "/v1/webhooks")

    valid_events = {"path_stale", "path_fixed"}
    event_list = [e.strip() for e in events.split(",")]
    invalid = set(event_list) - valid_events
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid events: {invalid}. Valid: {sorted(valid_events)}",
        )

    # Generate a signing secret
    signing_secret = secrets.token_urlsafe(32)

    sub = WebhookSubscription(
        api_key_id=api_key.id,
        url=url,
        events=event_list,
        secret=signing_secret,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    return {
        "id": sub.id,
        "url": sub.url,
        "events": sub.events,
        "signing_secret": signing_secret,
        "is_active": sub.is_active,
        "created_at": sub.created_at,
    }


@router.get("/webhooks")
def list_webhooks(
    request: Request,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """List your webhook subscriptions. Requires growth tier."""
    check_tier_gate(request, api_key)
    record_usage(db, api_key, "/v1/webhooks")

    subs = (
        db.query(WebhookSubscription)
        .filter(WebhookSubscription.api_key_id == api_key.id)
        .all()
    )

    return {
        "webhooks": [
            {
                "id": s.id,
                "url": s.url,
                "events": s.events,
                "is_active": s.is_active,
                "created_at": s.created_at,
            }
            for s in subs
        ]
    }


@router.delete("/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: int,
    request: Request,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Delete a webhook subscription. Requires growth tier."""
    check_tier_gate(request, api_key)
    record_usage(db, api_key, "/v1/webhooks")

    sub = (
        db.query(WebhookSubscription)
        .filter(
            WebhookSubscription.id == webhook_id,
            WebhookSubscription.api_key_id == api_key.id,
        )
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found.")

    db.delete(sub)
    db.commit()
    return {"status": "deleted", "id": webhook_id}


@router.get("/webhooks/{webhook_id}/deliveries")
def webhook_deliveries(
    webhook_id: int,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """View delivery history for a webhook subscription. Requires growth tier."""
    check_tier_gate(request, api_key)
    record_usage(db, api_key, "/v1/webhooks/deliveries")

    sub = (
        db.query(WebhookSubscription)
        .filter(
            WebhookSubscription.id == webhook_id,
            WebhookSubscription.api_key_id == api_key.id,
        )
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found.")

    deliveries = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.subscription_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "webhook_id": webhook_id,
        "deliveries": [
            {
                "id": d.id,
                "event_type": d.event_type,
                "payload": d.payload,
                "http_status": d.http_status,
                "success": d.success,
                "error": d.error,
                "attempts": d.attempts,
                "delivered_at": d.created_at,
            }
            for d in deliveries
        ],
    }
