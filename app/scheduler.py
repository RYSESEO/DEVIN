"""Background scheduler for automated monitoring cycles.

Schedules:
 - Confidence decay: daily at 03:00 UTC
 - Community report intelligence: every 6 hours
 - High-priority URL checks (top 50 services): daily at 04:00 UTC
 - Full URL checks (all services): weekly on Sunday at 05:00 UTC
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import SessionLocal
from app.models import LifecyclePath, Service
from app.monitor import (
    apply_confidence_decay,
    check_community_reports,
    extract_urls_from_steps,
    run_monitor_check,
)

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# Top services by subscriber volume — checked daily
HIGH_PRIORITY_DOMAINS = [
    "netflix.com", "spotify.com", "amazon.com", "hulu.com", "disneyplus.com",
    "apple.com", "youtube.com", "hbomax.com", "paramountplus.com", "peacocktv.com",
    "adobe.com", "microsoft.com", "nordvpn.com", "expressvpn.com", "mcafee.com",
    "norton.com", "att.com", "verizon.com", "xfinity.com", "spectrum.com",
    "t-mobile.com", "planetfitness.com", "nytimes.com", "washingtonpost.com",
    "linkedin.com", "dropbox.com", "evernote.com", "grammarly.com", "canva.com",
    "zoom.us", "slack.com", "notion.so", "github.com", "1password.com",
    "chegg.com", "coursera.org", "duolingo.com", "masterclass.com",
    "hellofresh.com", "blueapron.com", "doordash.com", "instacart.com",
    "peloton.com", "noom.com", "betterhelp.com", "headspace.com",
    "tinder.com", "bumble.com", "match.com", "audible.com",
]


@contextmanager
def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _job_confidence_decay():
    """Scheduled: decay all path confidence scores."""
    logger.info("Scheduler: running confidence decay.")
    try:
        with _get_db() as db:
            updated = apply_confidence_decay(db)
            logger.info("Scheduler: decayed %d paths.", updated)
    except Exception:
        logger.exception("Scheduler: confidence decay failed.")


def _job_report_intelligence():
    """Scheduled: check community reports for auto-flagging."""
    logger.info("Scheduler: running report intelligence.")
    try:
        with _get_db() as db:
            flagged = check_community_reports(db)
            logger.info("Scheduler: auto-flagged %d services.", flagged)
    except Exception:
        logger.exception("Scheduler: report intelligence failed.")


def _job_url_checks(domains: list[str] | None = None):
    """Scheduled: run URL health checks for specified or all services."""
    label = f"{len(domains)} priority" if domains else "all"
    logger.info("Scheduler: running URL checks for %s services.", label)
    try:
        with _get_db() as db:
            query = db.query(Service)
            if domains:
                query = query.filter(Service.domain.in_(domains))
            services = query.all()

            total_checks = 0
            changes = 0
            for service in services:
                paths = (
                    db.query(LifecyclePath)
                    .filter(LifecyclePath.service_id == service.id)
                    .all()
                )
                for path in paths:
                    if not extract_urls_from_steps(path.steps):
                        continue
                    results = run_monitor_check(db, service, path)
                    total_checks += len(results)
                    changes += sum(1 for r in results if r.changed)

            db.commit()
            logger.info(
                "Scheduler: URL checks complete — %d checked, %d changes.",
                total_checks,
                changes,
            )
    except Exception:
        logger.exception("Scheduler: URL checks failed.")


def _job_high_priority_checks():
    _job_url_checks(HIGH_PRIORITY_DOMAINS)


def _job_full_checks():
    _job_url_checks(None)


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler with all monitoring jobs."""
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.info("Scheduler already running.")
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")

    # Confidence decay: daily at 03:00 UTC
    _scheduler.add_job(
        _job_confidence_decay,
        "cron",
        hour=3,
        minute=0,
        id="confidence_decay",
        replace_existing=True,
    )

    # Report intelligence: every 6 hours
    _scheduler.add_job(
        _job_report_intelligence,
        "interval",
        hours=6,
        id="report_intelligence",
        replace_existing=True,
    )

    # High-priority URL checks: daily at 04:00 UTC
    _scheduler.add_job(
        _job_high_priority_checks,
        "cron",
        hour=4,
        minute=0,
        id="high_priority_url_checks",
        replace_existing=True,
    )

    # Full URL checks: weekly on Sunday at 05:00 UTC
    _scheduler.add_job(
        _job_full_checks,
        "cron",
        day_of_week="sun",
        hour=5,
        minute=0,
        id="full_url_checks",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started with %d jobs at %s.",
        len(_scheduler.get_jobs()),
        datetime.now(timezone.utc).isoformat(),
    )
    return _scheduler


def stop_scheduler():
    """Gracefully stop the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
        _scheduler = None


def get_scheduler_status() -> dict:
    """Return current scheduler status for the dashboard/health endpoint."""
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        })

    return {"running": True, "jobs": jobs}
