"""API Analytics — usage trends, error rates, per-key metrics, service health."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    ApiKey,
    BotRun,
    LifecyclePath,
    MonitorResult,
    Service,
    StaleFlag,
    UsageRecord,
)
from app.monitor import compute_service_health

router = APIRouter(prefix="/v1/analytics", tags=["Analytics"])


# ── Usage Analytics ──────────────────────────────────────────────────


@router.get("/usage/overview")
def usage_overview(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Platform-wide usage overview — totals, trends, top endpoints."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    records = (
        db.query(UsageRecord)
        .filter(UsageRecord.timestamp >= cutoff)
        .all()
    )
    total = len(records)

    # Daily breakdown
    daily: Counter[str] = Counter()
    hourly: Counter[int] = Counter()
    endpoints: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    for r in records:
        if r.timestamp:
            daily[r.timestamp.strftime("%Y-%m-%d")] += 1
            hourly[r.timestamp.hour] += 1
        endpoints[r.endpoint] += 1
        if r.domain_queried:
            domains[r.domain_queried] += 1

    # Compute daily average
    days_with_data = len(daily) or 1
    avg_daily = round(total / days_with_data, 1)

    return {
        "period_days": days,
        "total_requests": total,
        "avg_daily_requests": avg_daily,
        "daily_breakdown": dict(sorted(daily.items())),
        "peak_hours": dict(hourly.most_common(5)),
        "top_endpoints": dict(endpoints.most_common(10)),
        "top_domains_queried": dict(domains.most_common(15)),
    }


@router.get("/usage/endpoints")
def endpoint_analytics(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Per-endpoint breakdown — call counts, avg response per day, trends."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    records = (
        db.query(UsageRecord)
        .filter(UsageRecord.timestamp >= cutoff)
        .all()
    )

    # Group by endpoint
    ep_data: dict[str, list[datetime]] = {}
    for r in records:
        ep_data.setdefault(r.endpoint, []).append(r.timestamp)

    results = []
    for endpoint, timestamps in sorted(ep_data.items(), key=lambda x: -len(x[1])):
        # Daily trend for this endpoint
        daily: Counter[str] = Counter()
        for ts in timestamps:
            if ts:
                daily[ts.strftime("%Y-%m-%d")] += 1

        days_active = len(daily) or 1
        results.append({
            "endpoint": endpoint,
            "total_calls": len(timestamps),
            "avg_daily": round(len(timestamps) / days_active, 1),
            "days_active": days_active,
            "daily_trend": dict(sorted(daily.items())[-7:]),  # last 7 days
        })

    return {"period_days": days, "endpoints": results}


@router.get("/usage/keys")
def per_key_analytics(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Per-API-key usage breakdown — who's using what, how much."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    keys = db.query(ApiKey).filter(ApiKey.is_active.is_(True)).all()
    results = []

    for key in keys:
        records = (
            db.query(UsageRecord)
            .filter(
                UsageRecord.api_key_id == key.id,
                UsageRecord.timestamp >= cutoff,
            )
            .all()
        )
        if not records:
            continue

        endpoints: Counter[str] = Counter()
        domains: Counter[str] = Counter()
        daily: Counter[str] = Counter()
        for r in records:
            endpoints[r.endpoint] += 1
            if r.domain_queried:
                domains[r.domain_queried] += 1
            if r.timestamp:
                daily[r.timestamp.strftime("%Y-%m-%d")] += 1

        results.append({
            "key_name": key.name,
            "email": key.email,
            "tier": key.tier,
            "total_calls": len(records),
            "daily_limit": key.daily_limit,
            "monthly_limit": key.monthly_limit,
            "utilization_pct": round(len(records) / (key.monthly_limit or 1) * 100, 1),
            "top_endpoints": dict(endpoints.most_common(5)),
            "top_domains": dict(domains.most_common(5)),
            "daily_trend": dict(sorted(daily.items())[-7:]),
        })

    results.sort(key=lambda x: -x["total_calls"])
    return {"period_days": days, "keys": results}


# ── Monitoring Bot Analytics ─────────────────────────────────────────


@router.get("/bot/runs")
def bot_run_history(
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, pattern=r"^(running|completed|failed)$"),
    db: Session = Depends(get_db),
):
    """List recent monitoring bot runs with stats."""
    query = db.query(BotRun)
    if status:
        query = query.filter(BotRun.status == status)

    runs = (
        query.order_by(BotRun.started_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "runs": [
            {
                "id": r.id,
                "run_type": r.run_type,
                "status": r.status,
                "services_checked": r.services_checked,
                "urls_checked": r.urls_checked,
                "changes_detected": r.changes_detected,
                "errors": r.errors,
                "stale_flags_created": r.stale_flags_created,
                "webhooks_fired": r.webhooks_fired,
                "duration_seconds": r.duration_seconds,
                "error_detail": r.error_detail,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in runs
        ]
    }


@router.get("/bot/stats")
def bot_stats(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Aggregate bot performance stats over a period."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    runs = (
        db.query(BotRun)
        .filter(BotRun.started_at >= cutoff)
        .all()
    )

    if not runs:
        return {
            "period_days": days,
            "total_runs": 0,
            "completed": 0,
            "failed": 0,
            "total_urls_checked": 0,
            "total_changes_detected": 0,
            "total_errors": 0,
            "avg_duration_seconds": 0,
            "success_rate_pct": 0,
        }

    completed = [r for r in runs if r.status == "completed"]
    failed = [r for r in runs if r.status == "failed"]
    durations = [r.duration_seconds for r in completed if r.duration_seconds]

    return {
        "period_days": days,
        "total_runs": len(runs),
        "completed": len(completed),
        "failed": len(failed),
        "total_urls_checked": sum(r.urls_checked or 0 for r in runs),
        "total_changes_detected": sum(r.changes_detected or 0 for r in runs),
        "total_errors": sum(r.errors or 0 for r in runs),
        "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else 0,
        "success_rate_pct": round(len(completed) / len(runs) * 100, 1),
        "runs_by_type": dict(Counter(r.run_type for r in runs)),
    }


# ── Service Health ───────────────────────────────────────────────────


@router.get("/health/services")
def service_health_overview(
    category: str | None = Query(None),
    min_score: float | None = Query(None, ge=0, le=100),
    max_score: float | None = Query(None, ge=0, le=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Health scores for all services — filterable by category and score range."""
    query = db.query(Service)
    if category:
        query = query.filter(Service.category == category.lower())

    services = query.order_by(Service.name).all()

    results = []
    for svc in services:
        health = compute_service_health(db, svc)
        if min_score is not None and health["health_score"] < min_score:
            continue
        if max_score is not None and health["health_score"] > max_score:
            continue
        results.append({
            "domain": svc.domain,
            "name": svc.name,
            "category": svc.category,
            **health,
        })

    # Sort by health score ascending (worst first)
    results.sort(key=lambda x: x["health_score"])

    total = len(results)
    start = (page - 1) * per_page
    page_results = results[start:start + per_page]

    # Grade distribution
    grades = Counter(r["grade"] for r in results)

    return {
        "services": page_results,
        "total": total,
        "page": page,
        "per_page": per_page,
        "grade_distribution": dict(sorted(grades.items())),
        "avg_health_score": round(
            sum(r["health_score"] for r in results) / total, 1
        ) if total else 0,
    }


@router.get("/health/service/{domain}")
def single_service_health(domain: str, db: Session = Depends(get_db)):
    """Detailed health report for a single service."""
    service = db.query(Service).filter(Service.domain == domain).first()
    if not service:
        return {"error": f"Service '{domain}' not found"}

    health = compute_service_health(db, service)

    # Recent monitor history
    recent_checks = (
        db.query(MonitorResult)
        .filter(MonitorResult.service_id == service.id)
        .order_by(MonitorResult.created_at.desc())
        .limit(10)
        .all()
    )

    # Stale flag history
    flags = (
        db.query(StaleFlag)
        .filter(StaleFlag.service_id == service.id)
        .order_by(StaleFlag.created_at.desc())
        .limit(10)
        .all()
    )

    # Path-level confidence
    paths = (
        db.query(LifecyclePath)
        .filter(LifecyclePath.service_id == service.id)
        .all()
    )

    return {
        "domain": service.domain,
        "name": service.name,
        "category": service.category,
        **health,
        "paths": [
            {
                "path_type": p.path_type,
                "method": p.method,
                "confidence": p.confidence,
                "last_verified_at": p.last_verified_at.isoformat()
                if p.last_verified_at else None,
            }
            for p in paths
        ],
        "recent_checks": [
            {
                "url": c.url_checked,
                "http_status": c.http_status,
                "changed": c.changed,
                "error": c.error,
                "checked_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in recent_checks
        ],
        "stale_flag_history": [
            {
                "reason": f.reason,
                "severity": f.severity,
                "resolved": f.resolved,
                "flagged_at": f.created_at.isoformat() if f.created_at else None,
                "resolved_at": f.resolved_at.isoformat()
                if f.resolved_at else None,
            }
            for f in flags
        ],
    }


# ── Platform Summary ─────────────────────────────────────────────────


@router.get("/summary")
def platform_summary(db: Session = Depends(get_db)):
    """Executive summary — one endpoint for platform health at a glance."""
    total_services = db.query(func.count(Service.id)).scalar() or 0
    total_paths = db.query(func.count(LifecyclePath.id)).scalar() or 0

    avg_confidence = db.query(func.avg(LifecyclePath.confidence)).scalar()

    open_flags = (
        db.query(func.count(StaleFlag.id))
        .filter(StaleFlag.resolved.is_(False))
        .scalar() or 0
    )

    # Recent bot runs
    recent_runs = (
        db.query(BotRun)
        .order_by(BotRun.started_at.desc())
        .limit(5)
        .all()
    )
    last_run = recent_runs[0] if recent_runs else None

    # Usage last 24h
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    requests_24h = (
        db.query(func.count(UsageRecord.id))
        .filter(UsageRecord.timestamp >= cutoff_24h)
        .scalar() or 0
    )

    active_keys = (
        db.query(func.count(ApiKey.id))
        .filter(ApiKey.is_active.is_(True))
        .scalar() or 0
    )

    return {
        "platform": {
            "total_services": total_services,
            "total_paths": total_paths,
            "avg_confidence": round(avg_confidence, 3) if avg_confidence else None,
            "open_stale_flags": open_flags,
        },
        "api": {
            "requests_last_24h": requests_24h,
            "active_keys": active_keys,
        },
        "monitoring_bot": {
            "last_run": {
                "id": last_run.id,
                "status": last_run.status,
                "urls_checked": last_run.urls_checked,
                "changes_detected": last_run.changes_detected,
                "duration_seconds": last_run.duration_seconds,
                "completed_at": last_run.completed_at.isoformat()
                if last_run and last_run.completed_at else None,
            } if last_run else None,
            "total_runs_last_5": len(recent_runs),
        },
    }
