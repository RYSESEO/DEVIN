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


class TestHealthCheck:
    def test_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


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


class TestAuthRequired:
    def test_no_key_returns_401(self, client: TestClient):
        resp = client.get("/v1/cancel/netflix.com")
        assert resp.status_code == 401

    def test_bad_key_returns_401(self, client: TestClient):
        resp = client.get("/v1/cancel/netflix.com", headers={"X-API-Key": "bad_key"})
        assert resp.status_code == 401


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


class TestSupportedEndpoint:
    def test_supported_service(self, client: TestClient, api_key: str):
        resp = client.get("/v1/supported/spotify.com", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["supported"] is True
        assert data["service_name"] == "Spotify"

    def test_unsupported_service(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/supported/unknown-site.xyz", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["supported"] is False


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


class TestContributeEndpoint:
    def test_contribute(self, client: TestClient, api_key: str):
        resp = client.post(
            "/v1/contribute",
            headers={"X-API-Key": api_key},
            json={
                "domain": "newservice.com",
                "service_name": "New Service",
                "category": "streaming",
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


class TestUsageEndpoint:
    def test_usage_stats(self, client: TestClient, api_key: str):
        # Make a request first to have some usage
        client.get("/v1/cancel/netflix.com", headers={"X-API-Key": api_key})
        resp = client.get("/v1/usage", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["today"] >= 1
        assert data["tier"] == "free"
