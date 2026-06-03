from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False, index=True)
    logo_url = Column(String, nullable=True)
    billing_model = Column(String, nullable=True)
    trial_policy = Column(JSON, nullable=True)
    refund_policy = Column(JSON, nullable=True)
    legal_flags = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    paths = relationship(
        "LifecyclePath", back_populates="service", cascade="all, delete-orphan"
    )
    contacts = relationship(
        "ContactInfo", back_populates="service", cascade="all, delete-orphan"
    )


class LifecyclePath(Base):
    __tablename__ = "lifecycle_paths"

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    path_type = Column(String, nullable=False, default="cancel", index=True)
    method = Column(String, nullable=False)
    steps = Column(JSON, nullable=False)
    estimated_time_seconds = Column(Integer, nullable=False)
    difficulty = Column(String, nullable=False)
    confidence = Column(Float, nullable=False, default=0.9)
    complexity_score = Column(Integer, nullable=True)
    retention_offers = Column(JSON, nullable=True)
    legal_flags = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    last_verified_at = Column(DateTime, default=utcnow)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    service = relationship("Service", back_populates="paths")


class ContactInfo(Base):
    __tablename__ = "contact_info"

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    channel = Column(String, nullable=False)
    target = Column(String, nullable=False)
    hours = Column(String, nullable=True)
    expected_hold_minutes = Column(Integer, nullable=True)
    auth_required = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    service = relationship("Service", back_populates="contacts")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    tier = Column(String, nullable=False, default="free")
    is_active = Column(Boolean, default=True)
    daily_limit = Column(Integer, nullable=False)
    monthly_limit = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    usage_records = relationship("UsageRecord", back_populates="api_key")


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id = Column(Integer, primary_key=True, index=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=False)
    endpoint = Column(String, nullable=False)
    domain_queried = Column(String, nullable=True)
    timestamp = Column(DateTime, default=utcnow)

    api_key = relationship("ApiKey", back_populates="usage_records")


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=True)
    service_domain = Column(String, nullable=False)
    report_type = Column(String, nullable=False, default="broken_path")
    description = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class MonitorResult(Base):
    """Tracks automated health checks for each service's URLs."""

    __tablename__ = "monitor_results"

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    path_id = Column(Integer, ForeignKey("lifecycle_paths.id"), nullable=True)
    url_checked = Column(String, nullable=False)
    http_status = Column(Integer, nullable=True)
    dom_hash = Column(String, nullable=True)
    previous_dom_hash = Column(String, nullable=True)
    changed = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
    check_type = Column(String, nullable=False, default="http")
    created_at = Column(DateTime, default=utcnow)

    service = relationship("Service")
    path = relationship("LifecyclePath")


class StaleFlag(Base):
    """Flags services/paths detected as potentially stale."""

    __tablename__ = "stale_flags"

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    path_id = Column(Integer, ForeignKey("lifecycle_paths.id"), nullable=True)
    reason = Column(String, nullable=False)
    severity = Column(String, nullable=False, default="warning")
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    service = relationship("Service")
    path = relationship("LifecyclePath")


class WebhookSubscription(Base):
    """Webhook subscriptions for path change notifications."""

    __tablename__ = "webhook_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=False)
    url = Column(String, nullable=False)
    events = Column(JSON, nullable=False)
    secret = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    api_key = relationship("ApiKey")


class WebhookDelivery(Base):
    """Log of webhook delivery attempts."""

    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(
        Integer, ForeignKey("webhook_subscriptions.id"), nullable=False
    )
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    http_status = Column(Integer, nullable=True)
    success = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, default=1)
    created_at = Column(DateTime, default=utcnow)

    subscription = relationship("WebhookSubscription")


class BotRun(Base):
    """Tracks each automated monitoring bot run."""

    __tablename__ = "bot_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_type = Column(String, nullable=False)  # "high_priority", "full", "on_demand"
    status = Column(String, nullable=False, default="running")  # running, completed, failed
    services_checked = Column(Integer, default=0)
    urls_checked = Column(Integer, default=0)
    changes_detected = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    stale_flags_created = Column(Integer, default=0)
    webhooks_fired = Column(Integer, default=0)
    duration_seconds = Column(Float, nullable=True)
    error_detail = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)
