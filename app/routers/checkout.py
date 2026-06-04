"""Stripe self-serve billing — Checkout, billing portal, and webhook receiver.

Stripe is the only way to reach a paid tier. Tier changes are driven by verified
webhook events (not client trust); `app.auth.apply_tier` is the single source of truth
for syncing tier + rate limits.
"""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import apply_tier, get_api_key
from app.config import settings
from app.database import get_db
from app.models import ApiKey
from app.schemas import CheckoutCreate

logger = logging.getLogger("cancelkit.billing")

router = APIRouter(prefix="/v1", tags=["Billing"])


def _require_stripe() -> None:
    """Ensure Stripe is configured before attempting an API call."""
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=503,
            detail="Billing is not configured. Set CANCELKIT_STRIPE_SECRET_KEY.",
        )
    stripe.api_key = settings.stripe_secret_key


def _ensure_customer(api_key: ApiKey, db: Session) -> str:
    """Return the key's Stripe customer id, creating one on first use."""
    if api_key.stripe_customer_id:
        return api_key.stripe_customer_id
    customer = stripe.Customer.create(
        email=api_key.email,
        name=api_key.name,
        metadata={"api_key_id": str(api_key.id)},
    )
    api_key.stripe_customer_id = customer["id"]
    db.commit()
    return customer["id"]


@router.post("/checkout/session")
def create_checkout_session(
    body: CheckoutCreate,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Start a Stripe Checkout session to subscribe the authenticated key to a paid tier."""
    _require_stripe()

    price_id = settings.tier_price_map.get(body.tier)
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=f"No Stripe price configured for tier '{body.tier}'.",
        )

    customer_id = _ensure_customer(api_key, db)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(api_key.id),
        metadata={"api_key_id": str(api_key.id), "tier": body.tier},
        subscription_data={"metadata": {"api_key_id": str(api_key.id), "tier": body.tier}},
        success_url=f"{settings.app_base_url}/dashboard?checkout=success",
        cancel_url=f"{settings.app_base_url}/dashboard?checkout=cancelled",
    )
    return {"checkout_url": session["url"], "tier": body.tier}


@router.post("/checkout/portal")
def create_billing_portal(
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Create a Stripe Billing Portal session so the customer can manage or cancel."""
    _require_stripe()

    if not api_key.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="No billing account yet. Subscribe via POST /v1/checkout/session first.",
        )

    session = stripe.billing_portal.Session.create(
        customer=api_key.stripe_customer_id,
        return_url=f"{settings.app_base_url}/dashboard",
    )
    return {"portal_url": session["url"]}


def _key_by_subscription(db: Session, subscription_id: str | None) -> ApiKey | None:
    if not subscription_id:
        return None
    return (
        db.query(ApiKey)
        .filter(ApiKey.stripe_subscription_id == subscription_id)
        .first()
    )


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive and verify Stripe webhook events that drive tier changes."""
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured.")

    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        api_key_id = obj.get("client_reference_id") or meta.get("api_key_id")
        tier = meta.get("tier")
        key = db.get(ApiKey, int(api_key_id)) if api_key_id else None
        if key and tier:
            if obj.get("customer"):
                key.stripe_customer_id = obj["customer"]
            key.stripe_subscription_id = obj.get("subscription")
            apply_tier(key, tier, db)
            logger.info("Key %s upgraded to %s via Stripe.", key.id, tier)

    elif event_type == "customer.subscription.updated":
        key = _key_by_subscription(db, obj.get("id"))
        if key:
            items = (obj.get("items") or {}).get("data") or []
            price_id = items[0]["price"]["id"] if items else None
            tier = settings.price_tier_map.get(price_id)
            if tier:
                apply_tier(key, tier, db)
                logger.info("Key %s tier synced to %s.", key.id, tier)

    elif event_type == "customer.subscription.deleted":
        key = _key_by_subscription(db, obj.get("id"))
        if key:
            key.stripe_subscription_id = None
            apply_tier(key, "free", db)
            logger.info("Key %s downgraded to free (subscription cancelled).", key.id)

    return {"received": True}
