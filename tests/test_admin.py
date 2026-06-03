"""Tests for admin API endpoints."""

import pytest
from fastapi.testclient import TestClient


class TestAdminAuth:
    """Admin token enforcement."""

    def test_no_token_returns_403(self, client: TestClient):
        resp = client.get("/v1/admin/overview")
        assert resp.status_code == 403

    def test_wrong_token_returns_403(self, client: TestClient):
        resp = client.get("/v1/admin/overview", params={"admin_token": "bad"})
        assert resp.status_code == 403

    def test_correct_token_returns_200(self, client: TestClient):
        resp = client.get("/v1/admin/overview", params={"admin_token": "admin"})
        assert resp.status_code == 200


class TestAdminOverview:
    """Platform overview endpoint."""

    def test_overview_returns_totals(self, client: TestClient):
        resp = client.get("/v1/admin/overview", params={"admin_token": "admin"})
        data = resp.json()
        assert "totals" in data
        assert "monitoring" in data
        assert "categories" in data
        assert data["totals"]["services"] > 0

    def test_overview_has_monitoring_stats(self, client: TestClient):
        resp = client.get("/v1/admin/overview", params={"admin_token": "admin"})
        data = resp.json()
        assert "total_checks" in data["monitoring"]
        assert "open_stale_flags" in data["monitoring"]
        assert "avg_confidence" in data["monitoring"]


class TestAdminServiceCRUD:
    """Service create / read / update / delete."""

    def test_list_services(self, client: TestClient):
        resp = client.get("/v1/admin/services", params={"admin_token": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert data["total"] > 0

    def test_list_services_with_search(self, client: TestClient):
        resp = client.get(
            "/v1/admin/services",
            params={"admin_token": "admin", "search": "netflix"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_list_services_with_category(self, client: TestClient):
        resp = client.get(
            "/v1/admin/services",
            params={"admin_token": "admin", "category": "streaming"},
        )
        assert resp.status_code == 200

    def test_create_service(self, client: TestClient):
        resp = client.post(
            "/v1/admin/services",
            params={"admin_token": "admin"},
            json={
                "domain": "newservice.com",
                "name": "New Service",
                "category": "testing",
                "billing_model": "auto_renew",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["domain"] == "newservice.com"

    def test_create_duplicate_service_returns_409(self, client: TestClient):
        client.post(
            "/v1/admin/services",
            params={"admin_token": "admin"},
            json={"domain": "dup.com", "name": "Dup", "category": "test"},
        )
        resp = client.post(
            "/v1/admin/services",
            params={"admin_token": "admin"},
            json={"domain": "dup.com", "name": "Dup2", "category": "test"},
        )
        assert resp.status_code == 409

    def test_get_service_detail(self, client: TestClient):
        resp = client.get(
            "/v1/admin/services/netflix.com",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert "paths" in data
        assert "contacts" in data

    def test_get_nonexistent_service_returns_404(self, client: TestClient):
        resp = client.get(
            "/v1/admin/services/nonexistent.xyz",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 404

    def test_update_service(self, client: TestClient):
        resp = client.patch(
            "/v1/admin/services/netflix.com",
            params={"admin_token": "admin"},
            json={"billing_model": "contract"},
        )
        assert resp.status_code == 200
        assert "billing_model" in resp.json()["fields"]

    def test_delete_service(self, client: TestClient):
        # Create then delete
        client.post(
            "/v1/admin/services",
            params={"admin_token": "admin"},
            json={"domain": "todelete.com", "name": "ToDelete", "category": "test"},
        )
        resp = client.delete(
            "/v1/admin/services/todelete.com",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify deleted
        resp = client.get(
            "/v1/admin/services/todelete.com",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 404


class TestAdminPathCRUD:
    """Lifecycle path create / update / delete / verify."""

    def test_create_path(self, client: TestClient):
        resp = client.post(
            "/v1/admin/services/netflix.com/paths",
            params={"admin_token": "admin"},
            json={
                "path_type": "pause",
                "method": "web",
                "steps": [{"action": "navigate", "description": "Go to settings"}],
                "estimated_time_seconds": 60,
                "difficulty": "easy",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["path_type"] == "pause"

    def test_create_path_for_nonexistent_service(self, client: TestClient):
        resp = client.post(
            "/v1/admin/services/nonexistent.xyz/paths",
            params={"admin_token": "admin"},
            json={
                "path_type": "cancel",
                "method": "web",
                "steps": [{"action": "test"}],
                "estimated_time_seconds": 30,
                "difficulty": "easy",
            },
        )
        assert resp.status_code == 404

    def test_update_path(self, client: TestClient):
        # Get a path ID
        svc = client.get(
            "/v1/admin/services/netflix.com",
            params={"admin_token": "admin"},
        ).json()
        path_id = svc["paths"][0]["id"]

        resp = client.patch(
            f"/v1/admin/paths/{path_id}",
            params={"admin_token": "admin"},
            json={"difficulty": "hard"},
        )
        assert resp.status_code == 200
        assert "difficulty" in resp.json()["fields"]

    def test_delete_path(self, client: TestClient):
        # Create then delete
        create_resp = client.post(
            "/v1/admin/services/netflix.com/paths",
            params={"admin_token": "admin"},
            json={
                "path_type": "refund",
                "method": "phone",
                "steps": [{"action": "call"}],
                "estimated_time_seconds": 300,
                "difficulty": "hard",
            },
        )
        path_id = create_resp.json()["id"]

        resp = client.delete(
            f"/v1/admin/paths/{path_id}",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_verify_path(self, client: TestClient):
        svc = client.get(
            "/v1/admin/services/netflix.com",
            params={"admin_token": "admin"},
        ).json()
        path_id = svc["paths"][0]["id"]

        resp = client.post(
            f"/v1/admin/paths/{path_id}/verify",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["confidence"] == 0.95


class TestAdminContactCRUD:
    """Contact create / update / delete."""

    def test_create_contact(self, client: TestClient):
        resp = client.post(
            "/v1/admin/services/netflix.com/contacts",
            params={"admin_token": "admin"},
            json={
                "channel": "phone",
                "target": "1-800-123-4567",
                "hours": "24/7",
                "expected_hold_minutes": 10,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["channel"] == "phone"

    def test_update_contact(self, client: TestClient):
        svc = client.get(
            "/v1/admin/services/netflix.com",
            params={"admin_token": "admin"},
        ).json()
        if not svc["contacts"]:
            pytest.skip("No contacts to update")
        contact_id = svc["contacts"][0]["id"]

        resp = client.patch(
            f"/v1/admin/contacts/{contact_id}",
            params={"admin_token": "admin"},
            json={"hours": "9am-5pm EST"},
        )
        assert resp.status_code == 200

    def test_delete_contact(self, client: TestClient):
        # Create then delete
        create_resp = client.post(
            "/v1/admin/services/netflix.com/contacts",
            params={"admin_token": "admin"},
            json={"channel": "email", "target": "test@netflix.com"},
        )
        contact_id = create_resp.json()["id"]

        resp = client.delete(
            f"/v1/admin/contacts/{contact_id}",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"


class TestAdminReports:
    """Report listing and triage."""

    def test_list_reports(self, client: TestClient):
        resp = client.get(
            "/v1/admin/reports",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert "reports" in resp.json()

    def test_list_reports_with_status_filter(self, client: TestClient):
        resp = client.get(
            "/v1/admin/reports",
            params={"admin_token": "admin", "status": "pending"},
        )
        assert resp.status_code == 200

    def test_update_report_status(self, client: TestClient, api_key):
        # Create a report
        client.post(
            "/v1/report",
            headers={"X-API-Key": api_key},
            json={
                "service_domain": "netflix.com",
                "report_type": "broken_path",
                "description": "Cancel button is broken and needs fixing.",
            },
        )
        reports = client.get(
            "/v1/admin/reports",
            params={"admin_token": "admin"},
        ).json()
        report_id = reports["reports"][0]["id"]

        resp = client.patch(
            f"/v1/admin/reports/{report_id}",
            params={"admin_token": "admin"},
            json={"status": "investigating"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "investigating"

    def test_delete_report(self, client: TestClient, api_key):
        client.post(
            "/v1/report",
            headers={"X-API-Key": api_key},
            json={
                "service_domain": "netflix.com",
                "report_type": "broken_path",
                "description": "This is a test report to delete later.",
            },
        )
        reports = client.get(
            "/v1/admin/reports",
            params={"admin_token": "admin"},
        ).json()
        report_id = reports["reports"][0]["id"]

        resp = client.delete(
            f"/v1/admin/reports/{report_id}",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200


class TestAdminKeys:
    """API key listing and management."""

    def test_list_keys(self, client: TestClient, api_key):
        resp = client.get(
            "/v1/admin/keys",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_change_tier(self, client: TestClient, api_key):
        keys = client.get(
            "/v1/admin/keys",
            params={"admin_token": "admin"},
        ).json()
        key_id = keys["keys"][0]["id"]

        resp = client.patch(
            f"/v1/admin/keys/{key_id}/tier",
            params={"admin_token": "admin", "new_tier": "growth"},
        )
        assert resp.status_code == 200
        assert resp.json()["new_tier"] == "growth"

    def test_change_tier_invalid(self, client: TestClient, api_key):
        keys = client.get(
            "/v1/admin/keys",
            params={"admin_token": "admin"},
        ).json()
        key_id = keys["keys"][0]["id"]

        resp = client.patch(
            f"/v1/admin/keys/{key_id}/tier",
            params={"admin_token": "admin", "new_tier": "invalid"},
        )
        assert resp.status_code == 400

    def test_deactivate_key(self, client: TestClient, api_key):
        keys = client.get(
            "/v1/admin/keys",
            params={"admin_token": "admin"},
        ).json()
        key_id = keys["keys"][0]["id"]

        resp = client.patch(
            f"/v1/admin/keys/{key_id}/deactivate",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deactivated"


class TestAdminStaleFlags:
    """Stale flag management."""

    def test_list_stale_flags(self, client: TestClient):
        resp = client.get(
            "/v1/admin/stale-flags",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert "flags" in resp.json()

    def test_bulk_resolve_flags(self, client: TestClient):
        resp = client.post(
            "/v1/admin/bulk/resolve-all-flags",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert "flags_resolved" in resp.json()


class TestAdminBulkOps:
    """Bulk operations."""

    def test_bulk_verify_all(self, client: TestClient):
        resp = client.post(
            "/v1/admin/bulk/verify-all",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["paths_verified"] > 0


class TestAdminWebhooks:
    """Webhook listing."""

    def test_list_webhooks(self, client: TestClient):
        resp = client.get(
            "/v1/admin/webhooks",
            params={"admin_token": "admin"},
        )
        assert resp.status_code == 200
        assert "webhooks" in resp.json()


class TestAdminDashboardPage:
    """Admin dashboard HTML page."""

    def test_admin_page_returns_html(self, client: TestClient):
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
