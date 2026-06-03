"""Path monitoring engine — HTTP checks, DOM fingerprinting, staleness detection."""

import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
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


def check_url(url: str) -> dict:
    """Check a single URL — return status, DOM hash, and any errors."""
    result = {"url": url, "http_status": None, "dom_hash": None, "error": None}
    try:
        with httpx.Client(
            timeout=CHECK_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = client.get(url)
            result["http_status"] = resp.status_code
            if resp.status_code == 200 and "text/html" in resp.headers.get(
                "content-type", ""
            ):
                result["dom_hash"] = compute_dom_hash(resp.text)
    except httpx.TimeoutException:
        result["error"] = "timeout"
    except httpx.ConnectError:
        result["error"] = "connection_failed"
    except Exception as e:
        result["error"] = str(e)[:500]
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
