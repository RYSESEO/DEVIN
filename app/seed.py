import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.models import CancellationPath, Service

logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).parent / "data" / "services.yaml"


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
        )
        db.add(service)
        db.flush()

        for path_data in svc_data.get("paths", []):
            path = CancellationPath(
                service_id=service.id,
                method=path_data["method"],
                steps=path_data["steps"],
                estimated_time_seconds=path_data["estimated_time_seconds"],
                difficulty=path_data["difficulty"],
                confidence=path_data.get("confidence", 0.9),
                notes=path_data.get("notes"),
                last_verified_at=parse_datetime(
                    path_data.get("last_verified_at", datetime.now(timezone.utc))
                ),
            )
            db.add(path)

        loaded += 1

    db.commit()
    logger.info("Seeded %d services into database.", loaded)
    return loaded
