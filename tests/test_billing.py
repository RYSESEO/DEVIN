"""Tests for Stripe self-serve billing — checkout, portal, webhook, and tier gating.

All Stripe calls are monkeypatched; no network or real keys are used.
"""

import stripe
from fastapi.testclient import TestClient

from app.config import settings
from app.models import ApiKey


def _configure_stripe(monkeypatch):
    """Point settings at fake Stripe credentials/prices for the duration of a test."""
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_fake")
    monkeypatch.setattr(settings, "stripe_price_starter", "price_starter_123")
    monkeypatch.setattr(settings, "stripe_price_growth", "price_growth_456")


def _key_id(db, key: str) -> int:
    return db.query(ApiKey).filter(ApiKey.key == key).first().id


# ── Free-tier holes are closed ───────────────────────────────────────


def test_public_cannot_mint_paid_tier(client: TestClient):
    resp = client.post(
        "/v1/keys", json={"name": "Sneaky", "email": "sneaky@test.com", "tier": "growth"}
    )
    assert resp.status_code == 403
    assert "paid tiers require payment" in resp.json()["detail"].lower()


def test_public_can_still_create_free_key(client: TestClient):
    resp = client.post("/v1/keys", json={"name": "Free", "email": "free@test.com"})
    assert resp.status_code == 201
    assert resp.json()["tier"] == "free"


# ── Checkout session ─────────────────────────────────────────────────


def test_checkout_session_returns_url_and_persists_customer(client, db, api_key, monkeypatch):
    _configure_stripe(monkeypatch)
    monkeypatch.setattr(
        stripe.Customer, "create", staticmethod(lambda **kw: {"id": "cus_test_1"})
    )
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        staticmethod(lambda **kw: {"url": "https://checkout.stripe.com/c/test"}),
    )

    resp = client.post(
        "/v1/checkout/session", json={"tier": "starter"}, headers={"X-API-Key": api_key}
    )
    assert resp.status_code == 200
    assert resp.json()["checkout_url"].startswith("https://checkout.stripe.com")

    key = db.query(ApiKey).filter(ApiKey.key == api_key).first()
    assert key.stripe_customer_id == "cus_test_1"
    # Tier is unchanged until the webhook confirms payment.
    assert key.tier == "free"


def test_checkout_unconfigured_returns_503(client, api_key, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    resp = client.post(
        "/v1/checkout/session", json={"tier": "starter"}, headers={"X-API-Key": api_key}
    )
    assert resp.status_code == 503


def test_checkout_rejects_invalid_tier(client, api_key, monkeypatch):
    _configure_stripe(monkeypatch)
    resp = client.post(
        "/v1/checkout/session", json={"tier": "enterprise"}, headers={"X-API-Key": api_key}
    )
    # enterprise is not a self-serve Checkout tier → schema validation 422
    assert resp.status_code == 422


# ── Webhook signature verification ───────────────────────────────────


def test_webhook_rejects_bad_signature(client, monkeypatch):
    _configure_stripe(monkeypatch)

    def _raise(*args, **kwargs):
        raise stripe.error.SignatureVerificationError("bad", "sig")

    monkeypatch.setattr(stripe.Webhook, "construct_event", staticmethod(_raise))
    resp = client.post(
        "/v1/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=bad"}
    )
    assert resp.status_code == 400


# ── Webhook-driven tier changes ──────────────────────────────────────


def test_webhook_completed_upgrades_tier(client, db, api_key, monkeypatch):
    _configure_stripe(monkeypatch)
    key_id = _key_id(db, api_key)

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": str(key_id),
                "metadata": {"api_key_id": str(key_id), "tier": "growth"},
                "customer": "cus_test_9",
                "subscription": "sub_test_9",
            }
        },
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", staticmethod(lambda *a, **k: event))

    resp = client.post(
        "/v1/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "sig"}
    )
    assert resp.status_code == 200

    db.expire_all()
    key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    assert key.tier == "growth"
    assert key.daily_limit == 5000
    assert key.stripe_subscription_id == "sub_test_9"


def test_webhook_subscription_deleted_downgrades(client, db, monkeypatch):
    _configure_stripe(monkeypatch)
    # Provision a paid key linked to a subscription via the admin path.
    resp = client.post(
        "/v1/keys?admin_token=admin",
        json={"name": "Paid", "email": "paid@test.com", "tier": "growth"},
    )
    key_str = resp.json()["key"]
    key = db.query(ApiKey).filter(ApiKey.key == key_str).first()
    key.stripe_subscription_id = "sub_cancel_1"
    db.commit()

    event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_cancel_1"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", staticmethod(lambda *a, **k: event))

    resp = client.post(
        "/v1/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "sig"}
    )
    assert resp.status_code == 200

    db.expire_all()
    refreshed = db.query(ApiKey).filter(ApiKey.id == key.id).first()
    assert refreshed.tier == "free"
    assert refreshed.daily_limit == 100
    assert refreshed.stripe_subscription_id is None
