from datetime import datetime

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5, max_length=255)


class ReportCreate(BaseModel):
    service_domain: str = Field(..., min_length=1, max_length=255)
    report_type: str = Field(default="broken_path", pattern=r"^(broken_path|outdated|new_service)$")
    description: str = Field(..., min_length=10, max_length=2000)


class ContributeCreate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=255)
    service_name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(..., min_length=1, max_length=100)
    method: str = Field(..., pattern=r"^(web|phone|email|chat|app)$")
    steps: list[dict]
    estimated_time_seconds: int = Field(..., ge=10, le=3600)
    difficulty: str = Field(..., pattern=r"^(easy|medium|hard)$")
    notes: str | None = None


class CancellationPathResponse(BaseModel):
    method: str
    steps: list[dict]
    estimated_time_seconds: int
    difficulty: str
    confidence: float
    notes: str | None
    last_verified_at: datetime

    model_config = {"from_attributes": True}


class CancelResponse(BaseModel):
    domain: str
    service_name: str
    category: str
    paths: list[CancellationPathResponse]


class ServiceListItem(BaseModel):
    domain: str
    name: str
    category: str
    difficulty: str
    methods: list[str]

    model_config = {"from_attributes": True}


class SupportedResponse(BaseModel):
    domain: str
    supported: bool
    service_name: str | None = None
    category: str | None = None


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
