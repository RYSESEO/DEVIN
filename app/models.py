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
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import relationship

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
