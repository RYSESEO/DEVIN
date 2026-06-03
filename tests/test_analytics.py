"""Tests for API Analytics + Monitoring Bot features."""

from fastapi.testclient import TestClient

from app.config import settings
from app.models import BotRun


class TestAnalyticsUsage:
    """Test /v1/analytics/usage/* endpoints."""

    def test_usage_overview(self, client: TestClient):
        resp = client.get("/v1/analytics/usage/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "period_days" in data
        assert data["period_days"] == 30
        assert "total_requests" in data
        assert "daily_breakdown" in data
        assert "top_endpoints" in data
        assert "top_domains_queried" in data
        assert "avg_daily_requests" in data

    def test_usage_overview_custom_period(self, client: TestClient):
        resp = client.get("/v1/analytics/usage/overview?days=7")
        assert resp.status_code == 200
        assert resp.json()["period_days"] == 7

    def test_endpoint_analytics(self, client: TestClient):
        resp = client.get("/v1/analytics/usage/endpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert "endpoints" in data
        assert isinstance(data["endpoints"], list)

    def test_per_key_analytics(self, client: TestClient, api_key: str):
        # Make some requests to generate usage data
        client.get("/v1/services", headers={"X-API-Key": api_key})
        client.get("/v1/service/netflix.com", headers={"X-API-Key": api_key})

        resp = client.get("/v1/analytics/usage/keys")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert isinstance(data["keys"], list)

    def test_usage_tracks_requests(self, client: TestClient, api_key: str):
        # Make a few requests first
        for _ in range(3):
            client.get("/v1/services", headers={"X-API-Key": api_key})

        resp = client.get("/v1/analytics/usage/overview")
        data = resp.json()
        # At least our 3 requests should be counted
        assert data["total_requests"] >= 3


class TestAnalyticsBotRuns:
    """Test /v1/analytics/bot/* endpoints."""

    def test_bot_run_history_empty(self, client: TestClient):
        resp = client.get("/v1/analytics/bot/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert isinstance(data["runs"], list)

    def test_bot_stats_empty(self, client: TestClient):
        resp = client.get("/v1/analytics/bot/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 0
        assert data["total_urls_checked"] == 0

    def test_bot_run_history_with_data(self, client: TestClient, db):
        # Insert a test bot run
        run = BotRun(
            run_type="test",
            status="completed",
            services_checked=10,
            urls_checked=25,
            changes_detected=2,
            errors=1,
            stale_flags_created=1,
            webhooks_fired=1,
            duration_seconds=5.5,
        )
        db.add(run)
        db.commit()

        resp = client.get("/v1/analytics/bot/runs")
        data = resp.json()
        assert len(data["runs"]) >= 1
        latest = data["runs"][0]
        assert latest["run_type"] == "test"
        assert latest["status"] == "completed"
        assert latest["services_checked"] == 10
        assert latest["urls_checked"] == 25
        assert latest["duration_seconds"] == 5.5

    def test_bot_stats_with_data(self, client: TestClient, db):
        for i in range(3):
            run = BotRun(
                run_type="full" if i < 2 else "high_priority",
                status="completed" if i < 2 else "failed",
                services_checked=10,
                urls_checked=20,
                changes_detected=i,
                errors=0,
                duration_seconds=3.0 + i,
            )
            db.add(run)
        db.commit()

        resp = client.get("/v1/analytics/bot/stats")
        data = resp.json()
        assert data["total_runs"] == 3
        assert data["completed"] == 2
        assert data["failed"] == 1
        assert data["total_urls_checked"] == 60
        assert "runs_by_type" in data

    def test_bot_runs_filter_by_status(self, client: TestClient, db):
        run1 = BotRun(run_type="full", status="completed", duration_seconds=1.0)
        run2 = BotRun(run_type="full", status="failed", error_detail="test error")
        db.add_all([run1, run2])
        db.commit()

        resp = client.get("/v1/analytics/bot/runs?status=failed")
        data = resp.json()
        assert all(r["status"] == "failed" for r in data["runs"])


class TestServiceHealth:
    """Test /v1/analytics/health/* endpoints."""

    def test_service_health_overview(self, client: TestClient):
        resp = client.get("/v1/analytics/health/services")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "total" in data
        assert "grade_distribution" in data
        assert "avg_health_score" in data
        assert data["total"] > 0

    def test_service_health_filter_category(self, client: TestClient):
        resp = client.get("/v1/analytics/health/services?category=streaming")
        assert resp.status_code == 200
        data = resp.json()
        for svc in data["services"]:
            assert svc["category"] == "streaming"

    def test_single_service_health(self, client: TestClient):
        resp = client.get("/v1/analytics/health/service/netflix.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert "health_score" in data
        assert "grade" in data
        assert "avg_confidence" in data
        assert "paths" in data
        assert "recent_checks" in data
        assert "stale_flag_history" in data

    def test_single_service_health_not_found(self, client: TestClient):
        resp = client.get("/v1/analytics/health/service/nonexistent.xyz")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_health_score_range(self, client: TestClient):
        resp = client.get("/v1/analytics/health/services?min_score=80&max_score=100")
        assert resp.status_code == 200
        data = resp.json()
        for svc in data["services"]:
            assert 80 <= svc["health_score"] <= 100


class TestPlatformSummary:
    """Test /v1/analytics/summary endpoint."""

    def test_summary(self, client: TestClient):
        resp = client.get("/v1/analytics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "platform" in data
        assert "api" in data
        assert "monitoring_bot" in data
        assert data["platform"]["total_services"] > 0
        assert data["platform"]["total_paths"] > 0

    def test_summary_includes_version_header(self, client: TestClient):
        resp = client.get("/v1/analytics/summary")
        assert resp.headers.get("X-API-Version") == settings.api_version


class TestMonitorBotEndpoints:
    """Test /v1/monitor/bot/* endpoints."""

    def test_bot_status(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/monitor/bot/status",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "last_run" in data
        assert "scheduler" in data

    def test_bot_run_requires_enterprise(self, client: TestClient, api_key: str):
        resp = client.post(
            "/v1/monitor/bot/run",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403

    def test_service_health_endpoint(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/monitor/health/netflix.com",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert "health_score" in data
        assert "grade" in data

    def test_service_health_not_found(self, client: TestClient, api_key: str):
        resp = client.get(
            "/v1/monitor/health/nonexistent.xyz",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 404
