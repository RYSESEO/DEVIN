from datetime import datetime

from pydantic import BaseModel, Field

# ── Request schemas ──────────────────────────────────────────────────


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5, max_length=255)


class ReportCreate(BaseModel):
    service_domain: str = Field(..., min_length=1, max_length=255)
    report_type: str = Field(
        default="broken_path",
        pattern=r"^(broken_path|outdated|new_service)$",
    )
    description: str = Field(..., min_length=10, max_length=2000)


class ContributeCreate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=255)
    service_name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(..., min_length=1, max_length=100)
    path_type: str = Field(
        default="cancel",
        pattern=r"^(cancel|pause|downgrade|refund|account_delete)$",
    )
    method: str = Field(..., pattern=r"^(web|phone|email|chat|app|mail|in_person)$")
    steps: list[dict]
    estimated_time_seconds: int = Field(..., ge=10, le=3600)
    difficulty: str = Field(..., pattern=r"^(easy|medium|hard)$")
    notes: str | None = None


# ── Lifecycle path responses ─────────────────────────────────────────


class LifecyclePathResponse(BaseModel):
    path_type: str
    method: str
    steps: list[dict]
    estimated_time_seconds: int
    difficulty: str
    confidence: float
    complexity_score: int | None
    retention_offers: list[dict] | None
    legal_flags: list[str] | None
    notes: str | None
    last_verified_at: datetime

    model_config = {"from_attributes": True}


class CancellationPathResponse(BaseModel):
    """Backwards-compatible response for /v1/cancel endpoint."""

    method: str
    steps: list[dict]
    estimated_time_seconds: int
    difficulty: str
    confidence: float
    complexity_score: int | None = None
    retention_offers: list[dict] | None = None
    notes: str | None
    last_verified_at: datetime

    model_config = {"from_attributes": True}


class CancelResponse(BaseModel):
    domain: str
    service_name: str
    category: str
    paths: list[CancellationPathResponse]


# ── Service intelligence responses ───────────────────────────────────


class ContactResponse(BaseModel):
    channel: str
    target: str
    hours: str | None
    expected_hold_minutes: int | None
    auth_required: bool
    notes: str | None

    model_config = {"from_attributes": True}


class BillingResponse(BaseModel):
    domain: str
    service_name: str
    billing_model: str | None
    trial_policy: dict | None
    refund_policy: dict | None
    legal_flags: list[str] | None


class ServiceDetailResponse(BaseModel):
    domain: str
    service_name: str
    category: str
    billing: BillingResponse
    contacts: list[ContactResponse]
    lifecycle_paths: dict[str, list[LifecyclePathResponse]]
    legal_flags: list[str] | None
    complexity_score: int


class SignalsResponse(BaseModel):
    domain: str
    service_name: str
    likely_retention_offers: list[dict]
    churn_difficulty: str
    recommended_actions: list[str]
    complexity_score: int
    legal_flags: list[str] | None


# ── Listing / utility responses ──────────────────────────────────────


class ServiceListItem(BaseModel):
    domain: str
    name: str
    category: str
    difficulty: str
    methods: list[str]
    path_types: list[str]
    billing_model: str | None = None

    model_config = {"from_attributes": True}


class SupportedResponse(BaseModel):
    domain: str
    supported: bool
    service_name: str | None = None
    category: str | None = None
    available_path_types: list[str] | None = None


class ApiKeyResponse(BaseModel):
    key: str
    name: str
    email: str
    tier: str
    daily_limit: int
    monthly_limit: int


class UsageStatsResponse(BaseModel):
    today: int
    this_month: int
    daily_limit: int
    monthly_limit: int
    tier: str


class ReportResponse(BaseModel):
    id: int
    service_domain: str
    report_type: str
    description: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedServices(BaseModel):
    services: list[ServiceListItem]
    total: int
    page: int
    per_page: int
    total_pages: int
