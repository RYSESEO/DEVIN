import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.seed import seed_database

TEST_DB_URL = "sqlite:///./test_cancelkit.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestSessionLocal()
    seed_database(db)
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def api_key(client: TestClient):
    resp = client.post("/v1/keys", json={"name": "Test User", "email": "test@example.com"})
    assert resp.status_code == 201
    return resp.json()["key"]


# ── Health ───────────────────────────────────────────────────────────


class TestHealthCheck:
    def test_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.2.0"


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
    def test_get_billing(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/billing/netflix.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert data["billing_model"] == "auto_renew"
        assert data["trial_policy"] is not None
        assert data["refund_policy"] is not None

    def test_billing_refund_policy(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/billing/nordvpn.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert data["refund_policy"]["window_days"] == 30

    def test_billing_trial_policy(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/billing/hulu.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert data["trial_policy"]["duration_days"] == 30
        assert data["trial_policy"]["auto_converts"] is True

    def test_billing_legal_flags(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/billing/adobe.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert data["legal_flags"] is not None
        assert "ETF_DISCLOSURE" in data["legal_flags"]

    def test_billing_not_found(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/billing/nonexistent.com", headers={"X-API-Key": api_key}
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
    def test_get_signals(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/signals/netflix.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert data["churn_difficulty"] in ("easy", "medium", "hard")
        assert len(data["recommended_actions"]) > 0
        assert data["complexity_score"] >= 0

    def test_signals_retention_offers(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/signals/adobe.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert len(data["likely_retention_offers"]) > 0
        assert data["churn_difficulty"] == "hard"
        actions_lower = [a.lower() for a in data["recommended_actions"]]
        assert any("retention" in a or "discount" in a for a in actions_lower)

    def test_signals_recommends_pause(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/signals/netflix.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert any("pause" in a.lower() for a in data["recommended_actions"])

    def test_signals_recommends_downgrade(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/signals/spotify.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert any("downgrade" in a.lower() for a in data["recommended_actions"])

    def test_signals_not_found(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/signals/nonexistent.com", headers={"X-API-Key": api_key}
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
