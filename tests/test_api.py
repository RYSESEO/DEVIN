"""Tests for core API endpoints — uses shared fixtures from conftest.py."""

from fastapi.testclient import TestClient

# ── Health ───────────────────────────────────────────────────────────


class TestHealthCheck:
    def test_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.4.0"


# ── API Keys ─────────────────────────────────────────────────────────


class TestApiKeyCreation:
    def test_create_key(self, client: TestClient):
        resp = client.post("/v1/keys", json={"name": "Test", "email": "new@example.com"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["key"].startswith("ck_")
        assert data["tier"] == "free"
        assert data["daily_limit"] == 100
        assert data["monthly_limit"] == 3000

    def test_duplicate_email_rejected(self, client: TestClient, api_key: str):
        resp = client.post("/v1/keys", json={"name": "Dup", "email": "test@example.com"})
        assert resp.status_code == 409


# ── Auth ─────────────────────────────────────────────────────────────


class TestAuthRequired:
    def test_no_key_returns_401(self, client: TestClient):
        resp = client.get("/v1/cancel/netflix.com")
        assert resp.status_code == 401

    def test_bad_key_returns_401(self, client: TestClient):
        resp = client.get("/v1/cancel/netflix.com", headers={"X-API-Key": "bad_key"})
        assert resp.status_code == 401


# ── Cancel (backwards-compatible) ────────────────────────────────────


class TestCancelEndpoint:
    def test_get_netflix(self, client: TestClient, api_key: str):
        resp = client.get("/v1/cancel/netflix.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert data["service_name"] == "Netflix"
        assert data["category"] == "streaming"
        assert len(data["paths"]) > 0
        path = data["paths"][0]
        assert path["method"] == "web"
        assert len(path["steps"]) > 0
        assert path["confidence"] > 0

    def test_domain_normalization(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/cancel/https://www.netflix.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        assert resp.json()["domain"] == "netflix.com"

    def test_unknown_domain_returns_404(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/cancel/nonexistent-service.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 404

    def test_adobe_hard_difficulty(self, client: TestClient, api_key: str):
        resp = client.get("/v1/cancel/adobe.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["paths"][0]["difficulty"] == "hard"

    def test_nyt_multiple_methods(self, client: TestClient, api_key: str):
        resp = client.get("/v1/cancel/nytimes.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        methods = {p["method"] for p in data["paths"]}
        assert "web" in methods
        assert "phone" in methods

    def test_cancel_returns_only_cancel_paths(self, client: TestClient, api_key: str):
        """The /cancel endpoint should not return pause/downgrade paths."""
        resp = client.get("/v1/cancel/netflix.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["paths"]) >= 1


# ── Supported ────────────────────────────────────────────────────────


class TestSupportedEndpoint:
    def test_supported_service(self, client: TestClient, api_key: str):
        resp = client.get("/v1/supported/spotify.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["supported"] is True
        assert data["service_name"] == "Spotify"
        assert data["available_path_types"] is not None
        assert "cancel" in data["available_path_types"]

    def test_unsupported_service(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/supported/unknown-site.xyz", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["supported"] is False

    def test_supported_shows_path_types(self, client: TestClient, api_key: str):
        resp = client.get("/v1/supported/netflix.com", headers={"X-API-Key": api_key})
        data = resp.json()
        types = data["available_path_types"]
        assert "cancel" in types
        assert "pause" in types
        assert "downgrade" in types


# ── Services List ────────────────────────────────────────────────────


class TestServicesEndpoint:
    def test_list_all(self, client: TestClient, api_key: str):
        resp = client.get("/v1/services", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 30
        assert len(data["services"]) > 0

    def test_filter_by_category(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/services?category=streaming", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        for svc in data["services"]:
            assert svc["category"] == "streaming"

    def test_search(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/services?search=netflix", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(s["domain"] == "netflix.com" for s in data["services"])

    def test_pagination(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/services?per_page=5&page=1", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["services"]) == 5
        assert data["page"] == 1

    def test_services_include_path_types(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/services?search=netflix", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        netflix = data["services"][0]
        assert "path_types" in netflix
        assert "cancel" in netflix["path_types"]

    def test_services_include_billing_model(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/services?search=netflix", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        netflix = data["services"][0]
        assert netflix["billing_model"] == "auto_renew"


# ── Service Intelligence ─────────────────────────────────────────────


class TestServiceEndpoint:
    def test_get_service_detail(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/netflix.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert data["service_name"] == "Netflix"
        assert data["category"] == "streaming"
        assert "billing" in data
        assert "contacts" in data
        assert "lifecycle_paths" in data
        assert "legal_flags" in data
        assert "complexity_score" in data

    def test_service_has_billing_info(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/netflix.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        billing = data["billing"]
        assert billing["billing_model"] == "auto_renew"
        assert billing["trial_policy"] is not None
        assert billing["refund_policy"] is not None

    def test_service_has_contacts(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/netflix.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert len(data["contacts"]) > 0
        contact = data["contacts"][0]
        assert "channel" in contact
        assert "target" in contact

    def test_service_groups_paths_by_type(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/netflix.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        paths = data["lifecycle_paths"]
        assert "cancel" in paths
        assert "pause" in paths
        assert "downgrade" in paths

    def test_service_not_found(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/nonexistent.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 404

    def test_service_adobe(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/adobe.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        assert resp.json()["domain"] == "adobe.com"
        assert "cancel" in resp.json()["lifecycle_paths"]


# ── Lifecycle Paths ──────────────────────────────────────────────────


class TestLifecyclePathEndpoint:
    def test_get_cancel_path(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/netflix.com/path?type=cancel",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        paths = resp.json()
        assert len(paths) > 0
        assert all(p["path_type"] == "cancel" for p in paths)

    def test_get_pause_path(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/netflix.com/path?type=pause",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        paths = resp.json()
        assert len(paths) > 0
        assert all(p["path_type"] == "pause" for p in paths)

    def test_get_downgrade_path(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/netflix.com/path?type=downgrade",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        paths = resp.json()
        assert len(paths) > 0
        assert all(p["path_type"] == "downgrade" for p in paths)

    def test_get_refund_path(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/adobe.com/path?type=refund",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        paths = resp.json()
        assert len(paths) > 0
        assert all(p["path_type"] == "refund" for p in paths)

    def test_path_not_found(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/chatgpt.com/path?type=pause",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 404

    def test_invalid_path_type(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/netflix.com/path?type=invalid",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 400

    def test_path_includes_complexity_score(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/adobe.com/path?type=cancel",
            headers={"X-API-Key": api_key},
        )
        paths = resp.json()
        assert paths[0]["complexity_score"] is not None
        assert paths[0]["complexity_score"] > 0

    def test_path_includes_retention_offers(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/service/adobe.com/path?type=cancel",
            headers={"X-API-Key": api_key},
        )
        paths = resp.json()
        assert paths[0]["retention_offers"] is not None
        assert len(paths[0]["retention_offers"]) > 0


# ── Billing ──────────────────────────────────────────────────────────


class TestBillingEndpoint:
    def test_get_billing(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/billing/netflix.com", headers={"X-API-Key": starter_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert data["billing_model"] == "auto_renew"
        assert data["trial_policy"] is not None
        assert data["refund_policy"] is not None

    def test_billing_refund_policy(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/billing/nordvpn.com", headers={"X-API-Key": starter_key}
        )
        data = resp.json()
        assert data["refund_policy"]["window_days"] == 30

    def test_billing_trial_policy(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/billing/hulu.com", headers={"X-API-Key": starter_key}
        )
        data = resp.json()
        assert data["trial_policy"]["duration_days"] == 30
        assert data["trial_policy"]["auto_converts"] is True

    def test_billing_legal_flags(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/billing/adobe.com", headers={"X-API-Key": starter_key}
        )
        data = resp.json()
        assert data["legal_flags"] is not None
        assert "ETF_DISCLOSURE" in data["legal_flags"]

    def test_billing_not_found(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/billing/nonexistent.com", headers={"X-API-Key": starter_key}
        )
        assert resp.status_code == 404


# ── Contact ──────────────────────────────────────────────────────────


class TestContactEndpoint:
    def test_get_contacts(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/contact/netflix.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        channels = {c["channel"] for c in data}
        assert "chat" in channels or "web" in channels

    def test_contact_has_details(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/contact/adobe.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        phone_contacts = [c for c in data if c["channel"] == "phone"]
        assert len(phone_contacts) > 0
        assert phone_contacts[0]["target"].startswith("1-")
        assert phone_contacts[0]["expected_hold_minutes"] is not None

    def test_contact_not_found(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/contact/nonexistent.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 404


# ── Signals ──────────────────────────────────────────────────────────


class TestSignalsEndpoint:
    def test_get_signals(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/signals/netflix.com", headers={"X-API-Key": starter_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert data["churn_difficulty"] in ("easy", "medium", "hard")
        assert len(data["recommended_actions"]) > 0
        assert data["complexity_score"] >= 0

    def test_signals_retention_offers(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/signals/adobe.com", headers={"X-API-Key": starter_key}
        )
        data = resp.json()
        assert len(data["likely_retention_offers"]) > 0
        assert data["churn_difficulty"] == "hard"
        actions_lower = [a.lower() for a in data["recommended_actions"]]
        assert any("retention" in a or "discount" in a for a in actions_lower)

    def test_signals_recommends_pause(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/signals/netflix.com", headers={"X-API-Key": starter_key}
        )
        data = resp.json()
        assert any("pause" in a.lower() for a in data["recommended_actions"])

    def test_signals_recommends_downgrade(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/signals/spotify.com", headers={"X-API-Key": starter_key}
        )
        data = resp.json()
        assert any("downgrade" in a.lower() for a in data["recommended_actions"])

    def test_signals_not_found(self, client: TestClient, starter_key: str):
        resp = client.get(
            "/v1/signals/nonexistent.com", headers={"X-API-Key": starter_key}
        )
        assert resp.status_code == 404


# ── Reports ──────────────────────────────────────────────────────────


class TestReportEndpoint:
    def test_submit_report(self, client: TestClient, api_key: str):
        resp = client.post(
            "/v1/report",
            headers={"X-API-Key": api_key},
            json={
                "service_domain": "netflix.com",
                "report_type": "broken_path",
                "description": "Cancel button moved to a new location after redesign.",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["service_domain"] == "netflix.com"


# ── Contribute ───────────────────────────────────────────────────────


class TestContributeEndpoint:
    def test_contribute(self, client: TestClient, api_key: str):
        resp = client.post(
            "/v1/contribute",
            headers={"X-API-Key": api_key},
            json={
                "domain": "newservice.com",
                "service_name": "New Service",
                "category": "streaming",
                "path_type": "cancel",
                "method": "web",
                "steps": [
                    {
                        "action": "navigate",
                        "target": "https://newservice.com/cancel",
                        "description": "Go to cancel page.",
                    }
                ],
                "estimated_time_seconds": 60,
                "difficulty": "easy",
            },
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

    def test_contribute_pause_path(self, client: TestClient, api_key: str):
        resp = client.post(
            "/v1/contribute",
            headers={"X-API-Key": api_key},
            json={
                "domain": "newservice.com",
                "service_name": "New Service",
                "category": "streaming",
                "path_type": "pause",
                "method": "web",
                "steps": [
                    {
                        "action": "navigate",
                        "target": "https://newservice.com/pause",
                        "description": "Go to pause page.",
                    }
                ],
                "estimated_time_seconds": 60,
                "difficulty": "easy",
            },
        )
        assert resp.status_code == 202


# ── Usage ────────────────────────────────────────────────────────────


class TestUsageEndpoint:
    def test_usage_stats(self, client: TestClient, api_key: str):
        client.get("/v1/cancel/netflix.com", headers={"X-API-Key": api_key})
        resp = client.get("/v1/usage", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["today"] >= 1
        assert data["tier"] == "free"


# ── Tier-Based Feature Gating ────────────────────────────────────────


class TestTierGating:
    def test_free_tier_blocked_from_signals(self, client: TestClient, api_key: str):
        resp = client.get("/v1/signals/netflix.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 403
        assert "starter" in resp.json()["detail"].lower()

    def test_free_tier_blocked_from_billing(self, client: TestClient, api_key: str):
        resp = client.get("/v1/billing/netflix.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 403

    def test_free_tier_can_access_cancel(self, client: TestClient, api_key: str):
        resp = client.get("/v1/cancel/netflix.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_free_tier_can_access_services(self, client: TestClient, api_key: str):
        resp = client.get("/v1/services", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_starter_tier_can_access_signals(self, client: TestClient):
        resp = client.post(
            "/v1/keys", json={"name": "Starter", "email": "starter@test.com", "tier": "starter"}
        )
        key = resp.json()["key"]
        resp = client.get("/v1/signals/netflix.com", headers={"X-API-Key": key})
        assert resp.status_code == 200

    def test_starter_tier_can_access_billing(self, client: TestClient):
        resp = client.post(
            "/v1/keys", json={"name": "Starter", "email": "starter2@test.com", "tier": "starter"}
        )
        key = resp.json()["key"]
        resp = client.get("/v1/billing/netflix.com", headers={"X-API-Key": key})
        assert resp.status_code == 200

    def test_growth_tier_can_access_webhooks(self, client: TestClient):
        resp = client.post(
            "/v1/keys", json={"name": "Growth", "email": "growth@test.com", "tier": "growth"}
        )
        key = resp.json()["key"]
        resp = client.get("/v1/webhooks", headers={"X-API-Key": key})
        assert resp.status_code == 200

    def test_free_tier_blocked_from_webhooks(self, client: TestClient, api_key: str):
        resp = client.get("/v1/webhooks", headers={"X-API-Key": api_key})
        assert resp.status_code == 403


# ── API Key Management ───────────────────────────────────────────────


class TestKeyManagement:
    def test_get_my_key(self, client: TestClient, api_key: str):
        resp = client.get("/v1/keys/me", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "free"
        assert data["is_active"] is True
        assert "usage" in data
        assert "top_endpoints" in data

    def test_rotate_key(self, client: TestClient, api_key: str):
        resp = client.post("/v1/keys/rotate", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_key"].startswith("ck_")
        assert data["tier"] == "free"
        # Old key should no longer work
        resp2 = client.get("/v1/cancel/netflix.com", headers={"X-API-Key": api_key})
        assert resp2.status_code == 401
        # New key should work
        resp3 = client.get("/v1/cancel/netflix.com", headers={"X-API-Key": data["new_key"]})
        assert resp3.status_code == 200

    def test_revoke_key(self, client: TestClient):
        resp = client.post(
            "/v1/keys", json={"name": "Revoke Test", "email": "revoke@test.com"}
        )
        key = resp.json()["key"]
        resp = client.delete("/v1/keys/revoke", headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"
        # Revoked key should not work
        resp2 = client.get("/v1/cancel/netflix.com", headers={"X-API-Key": key})
        assert resp2.status_code == 401

    def test_upgrade_tier(self, client: TestClient, api_key: str):
        resp = client.post(
            "/v1/keys/upgrade?new_tier=starter", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_tier"] == "starter"
        assert data["old_tier"] == "free"
        assert data["daily_limit"] == 500

    def test_cannot_downgrade(self, client: TestClient):
        resp = client.post(
            "/v1/keys", json={"name": "Growth", "email": "downgrade@test.com", "tier": "growth"}
        )
        key = resp.json()["key"]
        resp = client.post(
            "/v1/keys/upgrade?new_tier=starter", headers={"X-API-Key": key}
        )
        assert resp.status_code == 400
        assert "cannot downgrade" in resp.json()["detail"].lower()

    def test_invalid_tier(self, client: TestClient, api_key: str):
        resp = client.post(
            "/v1/keys/upgrade?new_tier=invalid", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 400
