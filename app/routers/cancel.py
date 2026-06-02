import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_api_key, record_usage
from app.database import get_db
from app.models import ApiKey, CancellationPath, Service
from app.schemas import (
    CancellationPathResponse,
    CancelResponse,
    PaginatedServices,
    ServiceListItem,
    SupportedResponse,
)

router = APIRouter(prefix="/v1", tags=["Cancellation Paths"])


def normalize_domain(domain: str) -> str:
    domain = domain.lower().strip()
    for prefix in ("https://", "http://", "www."):
        if domain.startswith(prefix):
            domain = domain[len(prefix) :]
    return domain.rstrip("/")


@router.get("/cancel/{domain:path}", response_model=CancelResponse)
def get_cancellation_path(
    domain: str,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Return structured cancellation steps for a subscription service."""
    domain = normalize_domain(domain)
    record_usage(db, api_key, "/v1/cancel", domain)

    service = db.query(Service).filter(Service.domain == domain).first()
    if not service:
        raise HTTPException(
            status_code=404,
            detail=f"No cancellation path found for '{domain}'. Use POST /v1/contribute to add it.",
        )

    paths = db.query(CancellationPath).filter(CancellationPath.service_id == service.id).all()

    return CancelResponse(
        domain=service.domain,
        service_name=service.name,
        category=service.category,
        paths=[
            CancellationPathResponse(
                method=p.method,
                steps=p.steps,
                estimated_time_seconds=p.estimated_time_seconds,
                difficulty=p.difficulty,
                confidence=p.confidence,
                notes=p.notes,
                last_verified_at=p.last_verified_at,
            )
            for p in paths
        ],
    )


@router.get("/supported/{domain:path}", response_model=SupportedResponse)
def check_supported(
    domain: str,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Check whether a domain is in the CancelKit database."""
    domain = normalize_domain(domain)
    record_usage(db, api_key, "/v1/supported", domain)

    service = db.query(Service).filter(Service.domain == domain).first()
    if service:
        return SupportedResponse(
            domain=domain,
            supported=True,
            service_name=service.name,
            category=service.category,
        )
    return SupportedResponse(domain=domain, supported=False)


@router.get("/services", response_model=PaginatedServices)
def list_services(
    category: str | None = Query(None, description="Filter by category"),
    search: str | None = Query(None, description="Search by name or domain"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """List all supported services with pagination, search, and category filter."""
    record_usage(db, api_key, "/v1/services")

    query = db.query(Service)

    if category:
        query = query.filter(Service.category == category.lower())
    if search:
        pattern = f"%{search.lower()}%"
        query = query.filter(
            (Service.name.ilike(pattern)) | (Service.domain.ilike(pattern))
        )

    total = query.count()
    total_pages = max(1, math.ceil(total / per_page))
    services = query.order_by(Service.name).offset((page - 1) * per_page).limit(per_page).all()

    items: list[ServiceListItem] = []
    for svc in services:
        paths = db.query(CancellationPath).filter(CancellationPath.service_id == svc.id).all()
        difficulty = paths[0].difficulty if paths else "unknown"
        methods = sorted({p.method for p in paths})
        items.append(
            ServiceListItem(
                domain=svc.domain,
                name=svc.name,
                category=svc.category,
                difficulty=difficulty,
                methods=methods,
            )
        )

    return PaginatedServices(
        services=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )
