import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.models import ContactInfo, LifecyclePath, Service

logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).parent / "data" / "services.yaml"


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compute_complexity(path_data: dict) -> int:
    score = len(path_data.get("steps", []))
    if path_data.get("method") in ("phone", "mail", "in_person"):
        score += 3
    if path_data.get("difficulty") == "hard":
        score += 2
    elif path_data.get("difficulty") == "medium":
        score += 1
    return min(score, 10)


def seed_database(db: Session) -> int:
    existing_count = db.query(Service).count()
    if existing_count > 0:
        logger.info("Database already seeded with %d services, skipping.", existing_count)
        return existing_count

    if not SEED_FILE.exists():
        logger.warning("Seed file not found at %s", SEED_FILE)
        return 0

    with open(SEED_FILE) as f:
        data = yaml.safe_load(f)

    services_data = data.get("services", [])
    loaded = 0

    for svc_data in services_data:
        service = Service(
            domain=svc_data["domain"],
            name=svc_data["name"],
            category=svc_data["category"],
            logo_url=svc_data.get("logo_url"),
            billing_model=svc_data.get("billing_model"),
            trial_policy=svc_data.get("trial_policy"),
            refund_policy=svc_data.get("refund_policy"),
            legal_flags=svc_data.get("legal_flags"),
        )
        db.add(service)
        db.flush()

        for path_data in svc_data.get("paths", []):
            path = LifecyclePath(
                service_id=service.id,
                path_type=path_data.get("path_type", "cancel"),
                method=path_data["method"],
                steps=path_data["steps"],
                estimated_time_seconds=path_data["estimated_time_seconds"],
                difficulty=path_data["difficulty"],
                confidence=path_data.get("confidence", 0.9),
                complexity_score=path_data.get(
                    "complexity_score", compute_complexity(path_data)
                ),
                retention_offers=path_data.get("retention_offers"),
                legal_flags=path_data.get("legal_flags"),
                notes=path_data.get("notes"),
                last_verified_at=parse_datetime(
                    path_data.get("last_verified_at", datetime.now(timezone.utc))
                ),
            )
            db.add(path)

        for contact_data in svc_data.get("contacts", []):
            contact = ContactInfo(
                service_id=service.id,
                channel=contact_data["channel"],
                target=contact_data["target"],
                hours=contact_data.get("hours"),
                expected_hold_minutes=contact_data.get("expected_hold_minutes"),
                auth_required=contact_data.get("auth_required", False),
                notes=contact_data.get("notes"),
            )
            db.add(contact)

        loaded += 1

    db.commit()
    logger.info("Seeded %d services into database.", loaded)
    return loaded
