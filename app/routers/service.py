from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_api_key, record_usage
from app.database import get_db
from app.models import ApiKey, ContactInfo, LifecyclePath, Service
from app.routers.cancel import normalize_domain
from app.schemas import (
    BillingResponse,
    ContactResponse,
    LifecyclePathResponse,
    ServiceDetailResponse,
    SignalsResponse,
)

router = APIRouter(prefix="/v1", tags=["Subscription Intelligence"])

VALID_PATH_TYPES = {"cancel", "pause", "downgrade", "refund", "account_delete"}

DIFFICULTY_ORDER = {"easy": 1, "medium": 2, "hard": 3}


def _get_service_or_404(domain: str, db: Session) -> Service:
    service = db.query(Service).filter(Service.domain == domain).first()
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{domain}' not found.")
    return service


def _overall_complexity(paths: list[LifecyclePath]) -> int:
    if not paths:
        return 0
    scores = [p.complexity_score for p in paths if p.complexity_score is not None]
    return max(scores) if scores else len(paths[0].steps)


@router.get("/service/{domain}", response_model=ServiceDetailResponse)
def get_service_detail(
    domain: str,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Full subscription intelligence for a service: billing, contacts, all lifecycle paths."""
    domain = normalize_domain(domain)
    record_usage(db, api_key, "/v1/service", domain)

    service = _get_service_or_404(domain, db)
    paths = (
        db.query(LifecyclePath).filter(LifecyclePath.service_id == service.id).all()
    )
    contacts = (
        db.query(ContactInfo).filter(ContactInfo.service_id == service.id).all()
    )

    grouped: dict[str, list[LifecyclePathResponse]] = defaultdict(list)
    for p in paths:
        grouped[p.path_type].append(
            LifecyclePathResponse(
                path_type=p.path_type,
                method=p.method,
                steps=p.steps,
                estimated_time_seconds=p.estimated_time_seconds,
                difficulty=p.difficulty,
                confidence=p.confidence,
                complexity_score=p.complexity_score,
                retention_offers=p.retention_offers,
                legal_flags=p.legal_flags,
                notes=p.notes,
                last_verified_at=p.last_verified_at,
            )
        )

    return ServiceDetailResponse(
        domain=service.domain,
        service_name=service.name,
        category=service.category,
        billing=BillingResponse(
            domain=service.domain,
            service_name=service.name,
            billing_model=service.billing_model,
            trial_policy=service.trial_policy,
            refund_policy=service.refund_policy,
            legal_flags=service.legal_flags,
        ),
        contacts=[
            ContactResponse(
                channel=c.channel,
                target=c.target,
                hours=c.hours,
                expected_hold_minutes=c.expected_hold_minutes,
                auth_required=c.auth_required,
                notes=c.notes,
            )
            for c in contacts
        ],
        lifecycle_paths=dict(grouped),
        legal_flags=service.legal_flags,
        complexity_score=_overall_complexity(paths),
    )


@router.get(
    "/service/{domain}/path",
    response_model=list[LifecyclePathResponse],
)
def get_lifecycle_path(
    domain: str,
    type: str = Query(
        "cancel",
        description="Path type: cancel, pause, downgrade, refund, account_delete",
    ),
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Get specific lifecycle path type for a service."""
    domain = normalize_domain(domain)
    record_usage(db, api_key, f"/v1/service/path?type={type}", domain)

    if type not in VALID_PATH_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path type '{type}'. Valid: {sorted(VALID_PATH_TYPES)}",
        )

    service = _get_service_or_404(domain, db)
    paths = (
        db.query(LifecyclePath)
        .filter(
            LifecyclePath.service_id == service.id,
            LifecyclePath.path_type == type,
        )
        .all()
    )

    if not paths:
        raise HTTPException(
            status_code=404,
            detail=f"No '{type}' path found for '{domain}'.",
        )

    return [
        LifecyclePathResponse(
            path_type=p.path_type,
            method=p.method,
            steps=p.steps,
            estimated_time_seconds=p.estimated_time_seconds,
            difficulty=p.difficulty,
            confidence=p.confidence,
            complexity_score=p.complexity_score,
            retention_offers=p.retention_offers,
            legal_flags=p.legal_flags,
            notes=p.notes,
            last_verified_at=p.last_verified_at,
        )
        for p in paths
    ]


@router.get("/billing/{domain}", response_model=BillingResponse)
def get_billing_info(
    domain: str,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Billing model, trial policy, and refund policy for a service."""
    domain = normalize_domain(domain)
    record_usage(db, api_key, "/v1/billing", domain)

    service = _get_service_or_404(domain, db)

    return BillingResponse(
        domain=service.domain,
        service_name=service.name,
        billing_model=service.billing_model,
        trial_policy=service.trial_policy,
        refund_policy=service.refund_policy,
        legal_flags=service.legal_flags,
    )


@router.get("/contact/{domain}", response_model=list[ContactResponse])
def get_contact_info(
    domain: str,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Support channels, hold times, and contact details for a service."""
    domain = normalize_domain(domain)
    record_usage(db, api_key, "/v1/contact", domain)

    service = _get_service_or_404(domain, db)
    contacts = (
        db.query(ContactInfo).filter(ContactInfo.service_id == service.id).all()
    )

    return [
        ContactResponse(
            channel=c.channel,
            target=c.target,
            hours=c.hours,
            expected_hold_minutes=c.expected_hold_minutes,
            auth_required=c.auth_required,
            notes=c.notes,
        )
        for c in contacts
    ]


@router.get("/signals/{domain}", response_model=SignalsResponse)
def get_signals(
    domain: str,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Churn intelligence: retention offers, difficulty signals, recommended actions."""
    domain = normalize_domain(domain)
    record_usage(db, api_key, "/v1/signals", domain)

    service = _get_service_or_404(domain, db)
    paths = (
        db.query(LifecyclePath).filter(LifecyclePath.service_id == service.id).all()
    )

    all_retention: list[dict] = []
    for p in paths:
        if p.retention_offers:
            all_retention.extend(p.retention_offers)

    difficulties = [p.difficulty for p in paths if p.path_type == "cancel"]
    worst = max(
        difficulties, key=lambda d: DIFFICULTY_ORDER.get(d, 0), default="unknown"
    )

    actions: list[str] = []
    has_pause = any(p.path_type == "pause" for p in paths)
    has_downgrade = any(p.path_type == "downgrade" for p in paths)
    has_refund = any(p.path_type == "refund" for p in paths)

    if has_pause:
        actions.append("Consider a pause instead of canceling to retain option value.")
    if has_downgrade:
        actions.append("Downgrade to a cheaper tier before full cancellation.")
    if all_retention:
        actions.append("Expect retention offers — negotiate for best discount.")
    if has_refund:
        actions.append("Check refund eligibility before canceling.")
    if worst == "hard":
        actions.append("Prepare for an aggressive retention flow; be firm and direct.")
    if not actions:
        actions.append("Straightforward cancellation; proceed via the cancel path.")

    return SignalsResponse(
        domain=service.domain,
        service_name=service.name,
        likely_retention_offers=all_retention,
        churn_difficulty=worst,
        recommended_actions=actions,
        complexity_score=_overall_complexity(paths),
        legal_flags=service.legal_flags,
    )
