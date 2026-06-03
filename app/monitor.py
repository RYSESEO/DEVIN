"""Path monitoring engine — HTTP checks, DOM fingerprinting, staleness detection."""

import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    BotRun,
    LifecyclePath,
    MonitorResult,
    Report,
    Service,
    StaleFlag,
    WebhookDelivery,
    WebhookSubscription,
)

logger = logging.getLogger(__name__)

# Confidence decay: lose 0.03 per week since last_verified_at (caps at 0.3 floor)
CONFIDENCE_DECAY_PER_DAY = 0.03 / 7
CONFIDENCE_FLOOR = 0.30

# Community report thresholds
REPORT_AUTO_STALE_THRESHOLD = 3  # 3+ reports in 24h → auto-flag
REPORT_WINDOW_HOURS = 24

# HTTP check settings
CHECK_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BACKOFF = [2, 5]  # seconds between retries
USER_AGENT = (
    "Mozilla/5.0 (compatible; CancelKit-Monitor/1.0; +https://cancelkit.dev/monitor)"
)


def extract_urls_from_steps(steps: list[dict]) -> list[str]:
    """Pull all URLs from a path's step list."""
    urls = []
    for step in steps:
        target = step.get("target", "")
        if isinstance(target, str) and re.match(r"https?://", target):
            urls.append(target)
    return urls


def compute_dom_hash(html: str) -> str:
    """Lightweight DOM fingerprint — hash the structural skeleton of the page."""
    # Strip whitespace and script/style content for a structural fingerprint
    cleaned = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    cleaned = re.sub(r"<style[^>]*>.*?</style>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    # Keep only tag structure
    tags_only = re.sub(r">[^<]+<", "><", cleaned)
    tags_only = re.sub(r"\s+", " ", tags_only).strip()
    return hashlib.sha256(tags_only.encode()).hexdigest()[:16]


def check_url(url: str, retries: int = MAX_RETRIES) -> dict:
    """Check a single URL with retry logic — return status, DOM hash, errors, and timing."""
    result = {
        "url": url, "http_status": None, "dom_hash": None,
        "error": None, "attempts": 1, "response_time_ms": None,
    }
    for attempt in range(1 + retries):
        result["attempts"] = attempt + 1
        start = time.time()
        try:
            with httpx.Client(
                timeout=CHECK_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = client.get(url)
                result["response_time_ms"] = round((time.time() - start) * 1000)
                result["http_status"] = resp.status_code
                result["error"] = None
                if resp.status_code == 200 and "text/html" in resp.headers.get(
                    "content-type", ""
                ):
                    result["dom_hash"] = compute_dom_hash(resp.text)
                # Success — no retry needed
                return result
        except httpx.TimeoutException:
            result["error"] = "timeout"
        except httpx.ConnectError:
            result["error"] = "connection_failed"
        except Exception as e:
            result["error"] = str(e)[:500]
        # Retry with backoff
        if attempt < retries:
            backoff = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            time.sleep(backoff)
    return result


def run_monitor_check(db: Session, service: Service, path: LifecyclePath) -> list[MonitorResult]:
    """Run URL checks for all URLs in a path's steps. Returns MonitorResult records."""
    urls = extract_urls_from_steps(path.steps)
    if not urls:
        return []

    results = []
    for url in urls:
        # Get the previous check for this URL+path to compare DOM hashes
        previous = (
            db.query(MonitorResult)
            .filter(
                MonitorResult.service_id == service.id,
                MonitorResult.path_id == path.id,
                MonitorResult.url_checked == url,
            )
            .order_by(MonitorResult.created_at.desc())
            .first()
        )

        check = check_url(url)
        changed = False
        previous_hash = previous.dom_hash if previous else None

        # Detect change: HTTP error, or DOM structure changed
        if check["error"]:
            changed = True
        elif check["http_status"] and check["http_status"] >= 400:
            changed = True
        elif previous_hash and check["dom_hash"] and previous_hash != check["dom_hash"]:
            changed = True

        record = MonitorResult(
            service_id=service.id,
            path_id=path.id,
            url_checked=url,
            http_status=check["http_status"],
            dom_hash=check["dom_hash"],
            previous_dom_hash=previous_hash,
            changed=changed,
            error=check["error"],
            check_type="http_dom",
        )
        db.add(record)
        results.append(record)

        # If changed, create a stale flag
        if changed:
            reason = _build_change_reason(check, previous_hash)
            _create_stale_flag(db, service, path, reason)

    return results


def _build_change_reason(check: dict, previous_hash: str | None) -> str:
    if check["error"]:
        return f"URL check failed: {check['error']}"
    if check["http_status"] and check["http_status"] >= 400:
        return f"URL returned HTTP {check['http_status']}"
    if previous_hash and check["dom_hash"] != previous_hash:
        return f"DOM structure changed (hash {previous_hash[:8]}→{check['dom_hash'][:8]})"
    return "Unknown change detected"


def _create_stale_flag(
    db: Session, service: Service, path: LifecyclePath, reason: str
):
    """Create a stale flag if one doesn't already exist (unresolved) for this path."""
    existing = (
        db.query(StaleFlag)
        .filter(
            StaleFlag.service_id == service.id,
            StaleFlag.path_id == path.id,
            StaleFlag.resolved.is_(False),
        )
        .first()
    )
    if existing:
        return  # Already flagged

    severity = "critical" if "HTTP 4" in reason or "HTTP 5" in reason else "warning"
    flag = StaleFlag(
        service_id=service.id,
        path_id=path.id,
        reason=reason,
        severity=severity,
    )
    db.add(flag)


def apply_confidence_decay(db: Session):
    """Decay confidence scores based on time since last verification."""
    now = datetime.now(timezone.utc)
    paths = db.query(LifecyclePath).all()
    updated = 0

    for path in paths:
        if not path.last_verified_at:
            continue
        last_verified = path.last_verified_at
        if last_verified.tzinfo is None:
            last_verified = last_verified.replace(tzinfo=timezone.utc)
        days_since = (now - last_verified).total_seconds() / 86400
        if days_since <= 7:
            continue  # No decay in the first week

        decay = CONFIDENCE_DECAY_PER_DAY * days_since
        new_confidence = max(CONFIDENCE_FLOOR, path.confidence - decay)

        # Only update if meaningfully different
        if abs(new_confidence - path.confidence) >= 0.01:
            path.confidence = round(new_confidence, 3)
            updated += 1

    if updated:
        db.commit()
        logger.info("Decayed confidence for %d paths.", updated)
    return updated


def check_community_reports(db: Session):
    """Flag services as stale when 3+ reports arrive within 24 hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=REPORT_WINDOW_HOURS)

    # Group recent reports by domain
    report_counts = (
        db.query(Report.service_domain, func.count(Report.id))
        .filter(
            Report.created_at >= cutoff,
            Report.status == "pending",
        )
        .group_by(Report.service_domain)
        .having(func.count(Report.id) >= REPORT_AUTO_STALE_THRESHOLD)
        .all()
    )

    flagged = 0
    for domain, count in report_counts:
        service = db.query(Service).filter(Service.domain == domain).first()
        if not service:
            continue

        # Check if already flagged (unresolved)
        existing = (
            db.query(StaleFlag)
            .filter(
                StaleFlag.service_id == service.id,
                StaleFlag.path_id.is_(None),
                StaleFlag.resolved.is_(False),
            )
            .first()
        )
        if existing:
            continue

        flag = StaleFlag(
            service_id=service.id,
            path_id=None,
            reason=f"Community reports: {count} reports in {REPORT_WINDOW_HOURS}h",
            severity="critical",
        )
        db.add(flag)
        flagged += 1

    if flagged:
        db.commit()
        logger.info("Auto-flagged %d services from community reports.", flagged)
    return flagged


def deliver_webhooks(db: Session, event_type: str, payload: dict):
    """Deliver webhook notifications to all active subscribers for this event type."""
    subs = (
        db.query(WebhookSubscription)
        .filter(WebhookSubscription.is_active.is_(True))
        .all()
    )

    for sub in subs:
        if event_type not in (sub.events or []):
            continue

        delivery = WebhookDelivery(
            subscription_id=sub.id,
            event_type=event_type,
            payload=payload,
        )

        body = json.dumps(payload, default=str)
        headers = {"Content-Type": "application/json", "X-CancelKit-Event": event_type}

        # Sign the payload if a secret is configured
        if sub.secret:
            sig = hmac.new(sub.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-CancelKit-Signature"] = f"sha256={sig}"

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(sub.url, content=body, headers=headers)
                delivery.http_status = resp.status_code
                delivery.success = 200 <= resp.status_code < 300
        except Exception as e:
            delivery.error = str(e)[:500]
            delivery.success = False

        db.add(delivery)

    db.commit()


def run_full_monitor_cycle(priority: str = "all"):
    """Run a complete monitoring cycle: URL checks, confidence decay, report intel, webhooks.

    Args:
        priority: "high" for high-traffic services only, "all" for everything.
    """
    db = SessionLocal()
    try:
        # 1. Confidence decay
        apply_confidence_decay(db)

        # 2. Community report intelligence
        check_community_reports(db)

        # 3. URL monitoring for all services
        services = db.query(Service).all()
        total_checks = 0
        changes_detected = 0

        for service in services:
            paths = (
                db.query(LifecyclePath)
                .filter(LifecyclePath.service_id == service.id)
                .all()
            )

            for path in paths:
                # Skip paths with no URLs in steps
                if not extract_urls_from_steps(path.steps):
                    continue

                results = run_monitor_check(db, service, path)
                total_checks += len(results)
                changes_detected += sum(1 for r in results if r.changed)

        db.commit()

        # 4. Deliver webhooks for any new stale flags
        new_flags = (
            db.query(StaleFlag)
            .filter(
                StaleFlag.resolved.is_(False),
                StaleFlag.created_at >= datetime.now(timezone.utc) - timedelta(minutes=5),
            )
            .all()
        )

        for flag in new_flags:
            service = db.get(Service, flag.service_id)
            payload = {
                "event": "path_stale",
                "domain": service.domain if service else "unknown",
                "service_name": service.name if service else "unknown",
                "reason": flag.reason,
                "severity": flag.severity,
                "path_id": flag.path_id,
                "flagged_at": flag.created_at.isoformat(),
            }
            deliver_webhooks(db, "path_stale", payload)

        logger.info(
            "Monitor cycle complete: %d checks, %d changes, %d new stale flags.",
            total_checks,
            changes_detected,
            len(new_flags),
        )
        return {
            "total_checks": total_checks,
            "changes_detected": changes_detected,
            "new_stale_flags": len(new_flags),
        }
    finally:
        db.close()


def compute_service_health(db: Session, service: Service) -> dict:
    """Compute a health score for a service based on monitoring data."""
    paths = (
        db.query(LifecyclePath)
        .filter(LifecyclePath.service_id == service.id)
        .all()
    )
    if not paths:
        return {"health_score": 0, "grade": "unknown", "details": "no_paths"}

    avg_confidence = sum(p.confidence for p in paths) / len(paths)

    # Check recent monitor results (last 30 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_results = (
        db.query(MonitorResult)
        .filter(
            MonitorResult.service_id == service.id,
            MonitorResult.created_at >= cutoff,
        )
        .all()
    )

    total_checks = len(recent_results)
    errors = sum(1 for r in recent_results if r.error or (r.http_status and r.http_status >= 400))
    changes = sum(1 for r in recent_results if r.changed)

    # Open stale flags
    open_flags = (
        db.query(StaleFlag)
        .filter(
            StaleFlag.service_id == service.id,
            StaleFlag.resolved.is_(False),
        )
        .count()
    )

    # Health score: 100 base, penalized by issues
    score = 100.0
    score -= (1.0 - avg_confidence) * 30  # confidence penalty (up to 30)
    if total_checks > 0:
        error_rate = errors / total_checks
        score -= error_rate * 25  # error rate penalty (up to 25)
        change_rate = changes / total_checks
        score -= change_rate * 15  # change rate penalty (up to 15)
    score -= open_flags * 10  # stale flag penalty (10 each)
    score = max(0, min(100, round(score, 1)))

    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"

    return {
        "health_score": score,
        "grade": grade,
        "avg_confidence": round(avg_confidence, 3),
        "total_paths": len(paths),
        "recent_checks": total_checks,
        "recent_errors": errors,
        "recent_changes": changes,
        "open_stale_flags": open_flags,
    }


def run_bot_cycle(db: Session, run_type: str, domains: list[str] | None = None) -> BotRun:
    """Execute a full bot monitoring cycle with tracking."""
    bot_run = BotRun(run_type=run_type, status="running")
    db.add(bot_run)
    db.commit()
    db.refresh(bot_run)

    start_time = time.time()
    total_services = 0
    total_urls = 0
    total_changes = 0
    total_errors = 0
    total_stale = 0
    total_webhooks = 0

    try:
        # 1. Confidence decay
        apply_confidence_decay(db)

        # 2. Community report intelligence
        check_community_reports(db)

        # 3. URL monitoring
        query = db.query(Service)
        if domains:
            query = query.filter(Service.domain.in_(domains))
        services = query.all()

        for service in services:
            total_services += 1
            paths = (
                db.query(LifecyclePath)
                .filter(LifecyclePath.service_id == service.id)
                .all()
            )
            for path in paths:
                if not extract_urls_from_steps(path.steps):
                    continue
                results = run_monitor_check(db, service, path)
                total_urls += len(results)
                total_changes += sum(1 for r in results if r.changed)
                total_errors += sum(1 for r in results if r.error)

        db.commit()

        # 4. Count new stale flags and deliver webhooks
        new_flags = (
            db.query(StaleFlag)
            .filter(
                StaleFlag.resolved.is_(False),
                StaleFlag.created_at >= datetime.now(timezone.utc) - timedelta(minutes=5),
            )
            .all()
        )
        total_stale = len(new_flags)

        for flag in new_flags:
            svc = db.get(Service, flag.service_id)
            payload = {
                "event": "path_stale",
                "domain": svc.domain if svc else "unknown",
                "service_name": svc.name if svc else "unknown",
                "reason": flag.reason,
                "severity": flag.severity,
                "path_id": flag.path_id,
                "flagged_at": flag.created_at.isoformat() if flag.created_at else None,
            }
            deliver_webhooks(db, "path_stale", payload)
            total_webhooks += 1

        bot_run.status = "completed"
    except Exception as exc:
        bot_run.status = "failed"
        bot_run.error_detail = str(exc)[:1000]
        logger.exception("Bot run failed: %s", exc)

    bot_run.services_checked = total_services
    bot_run.urls_checked = total_urls
    bot_run.changes_detected = total_changes
    bot_run.errors = total_errors
    bot_run.stale_flags_created = total_stale
    bot_run.webhooks_fired = total_webhooks
    bot_run.duration_seconds = round(time.time() - start_time, 2)
    bot_run.completed_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        "Bot run %s (%s): %d services, %d URLs, %d changes, %d errors in %.1fs",
        bot_run.id, run_type, total_services, total_urls,
        total_changes, total_errors, bot_run.duration_seconds,
    )
    return bot_run
