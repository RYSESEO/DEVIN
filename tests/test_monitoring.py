"""Tests for monitoring endpoints, staleness detection, webhooks, and scheduler."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models import (
    LifecyclePath,
    MonitorResult,
    Report,
    Service,
    StaleFlag,
)
from app.monitor import (
    apply_confidence_decay,
    check_community_reports,
    compute_dom_hash,
    extract_urls_from_steps,
)

# ── Unit tests: monitor engine ───────────────────────────────────────


class TestDomHash:
    def test_compute_dom_hash_basic(self):
        html = "<html><body><div><p>Hello</p></div></body></html>"
        h = compute_dom_hash(html)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_dom_hash_ignores_text(self):
        h1 = compute_dom_hash("<html><body><p>Version 1</p></body></html>")
        h2 = compute_dom_hash("<html><body><p>Version 2</p></body></html>")
        assert h1 == h2

    def test_dom_hash_detects_structure_change(self):
        h1 = compute_dom_hash("<html><body><p>Text</p></body></html>")
        h2 = compute_dom_hash("<html><body><div><p>Text</p></div></body></html>")
        assert h1 != h2

    def test_dom_hash_strips_scripts(self):
        h1 = compute_dom_hash("<html><body><p>Text</p></body></html>")
        h2 = compute_dom_hash(
            "<html><body><script>alert('x')</script><p>Text</p></body></html>"
        )
        assert h1 == h2

    def test_dom_hash_strips_styles(self):
        h1 = compute_dom_hash("<html><body><p>Text</p></body></html>")
        h2 = compute_dom_hash(
            "<html><body><style>.x{color:red}</style><p>Text</p></body></html>"
        )
        assert h1 == h2


class TestExtractUrls:
    def test_extracts_http_urls(self):
        steps = [
            {"action": "navigate", "target": "https://netflix.com/cancel"},
            {"action": "click", "target": "Cancel Membership button"},
            {"action": "navigate", "target": "http://example.com/done"},
        ]
        urls = extract_urls_from_steps(steps)
        assert urls == ["https://netflix.com/cancel", "http://example.com/done"]

    def test_empty_steps(self):
        assert extract_urls_from_steps([]) == []

    def test_no_urls(self):
        steps = [{"action": "click", "target": "Some button"}]
        assert extract_urls_from_steps(steps) == []


class TestConfidenceDecay:
    def test_no_decay_within_week(self, db):
        now = datetime.now(timezone.utc)
        path = db.query(LifecyclePath).first()
        path.last_verified_at = now - timedelta(days=3)
        path.confidence = 0.95
        db.commit()

        apply_confidence_decay(db)
        db.refresh(path)
        assert path.confidence == 0.95

    def test_decay_after_week(self, db):
        now = datetime.now(timezone.utc)
        path = db.query(LifecyclePath).first()
        path.last_verified_at = now - timedelta(days=30)
        path.confidence = 0.95
        db.commit()

        apply_confidence_decay(db)
        db.refresh(path)
        assert path.confidence < 0.95
        assert path.confidence >= 0.30

    def test_decay_floor(self, db):
        path = db.query(LifecyclePath).first()
        path.last_verified_at = datetime.now(timezone.utc) - timedelta(days=365)
        path.confidence = 0.35
        db.commit()

        apply_confidence_decay(db)
        db.refresh(path)
        assert path.confidence >= 0.30


class TestCommunityReports:
    def test_no_flag_below_threshold(self, db):
        now = datetime.now(timezone.utc)
        for _ in range(2):
            db.add(Report(
                service_domain="netflix.com",
                report_type="broken_path",
                description="Test report",
                status="pending",
                created_at=now,
            ))
        db.commit()

        flagged = check_community_reports(db)
        assert flagged == 0

    def test_auto_flag_on_threshold(self, db):
        now = datetime.now(timezone.utc)
        for _ in range(3):
            db.add(Report(
                service_domain="netflix.com",
                report_type="broken_path",
                description="Test report",
                status="pending",
                created_at=now,
            ))
        db.commit()

        flagged = check_community_reports(db)
        assert flagged == 1
        flag = db.query(StaleFlag).filter(StaleFlag.resolved.is_(False)).first()
        assert flag is not None
        assert "Community reports" in flag.reason
        assert flag.severity == "critical"


# ── Staleness API endpoints ──────────────────────────────────────────


class TestStaleEndpoint:
    def test_get_stale_empty(self, client, api_key):
        resp = client.get("/v1/stale", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["stale_flags"] == []

    def test_get_stale_with_flags(self, client, api_key, db):
        service = db.query(Service).filter(Service.domain == "netflix.com").first()
        db.add(StaleFlag(
            service_id=service.id,
            reason="Test stale flag",
            severity="warning",
        ))
        db.commit()

        resp = client.get("/v1/stale", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["stale_flags"][0]["domain"] == "netflix.com"
        assert data["stale_flags"][0]["severity"] == "warning"

    def test_filter_by_severity(self, client, api_key, db):
        service = db.query(Service).filter(Service.domain == "netflix.com").first()
        db.add(StaleFlag(service_id=service.id, reason="Warning", severity="warning"))
        db.add(StaleFlag(service_id=service.id, reason="Critical", severity="critical"))
        db.commit()

        resp = client.get(
            "/v1/stale?severity=critical", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert data["total"] == 1
        assert data["stale_flags"][0]["severity"] == "critical"

    def test_stale_requires_auth(self, client):
        resp = client.get("/v1/stale")
        assert resp.status_code == 401


class TestStaleSummary:
    def test_summary_response_structure(self, client, api_key):
        resp = client.get("/v1/stale/summary", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "open_flags" in data
        assert "confidence_distribution" in data
        assert "verification_age" in data
        assert "decay_rate" in data
        assert data["open_flags"]["total"] == 0
        assert data["confidence_distribution"]["high"] >= 0
        assert data["verification_age"]["total_paths"] > 0

    def test_summary_with_flags(self, client, api_key, db):
        service = db.query(Service).filter(Service.domain == "netflix.com").first()
        db.add(StaleFlag(service_id=service.id, reason="Test", severity="critical"))
        db.commit()

        resp = client.get("/v1/stale/summary", headers={"X-API-Key": api_key})
        data = resp.json()
        assert data["open_flags"]["total"] == 1
        assert data["open_flags"]["critical"] == 1


# ── Monitor history endpoint ─────────────────────────────────────────


class TestMonitorHistory:
    def test_history_empty(self, client, api_key):
        resp = client.get(
            "/v1/monitor/history/netflix.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert data["checks"] == []

    def test_history_with_results(self, client, api_key, db):
        service = db.query(Service).filter(Service.domain == "netflix.com").first()
        db.add(MonitorResult(
            service_id=service.id,
            url_checked="https://help.netflix.com/cancel",
            http_status=200,
            dom_hash="abc123",
            changed=False,
            check_type="http_dom",
        ))
        db.commit()

        resp = client.get(
            "/v1/monitor/history/netflix.com", headers={"X-API-Key": api_key}
        )
        data = resp.json()
        assert len(data["checks"]) == 1
        assert data["checks"][0]["http_status"] == 200
        assert data["checks"][0]["dom_hash"] == "abc123"

    def test_history_not_found(self, client, api_key):
        resp = client.get(
            "/v1/monitor/history/nonexistent.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 404


# ── Monitor run endpoint ─────────────────────────────────────────────


class TestMonitorRun:
    @patch("app.routers.monitoring.run_monitor_check")
    def test_run_returns_results(self, mock_check, client, growth_key):
        mock_result = type("MockResult", (), {"changed": False})()
        mock_check.return_value = [mock_result]

        resp = client.post(
            "/v1/monitor/run?domain=netflix.com", headers={"X-API-Key": growth_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "netflix.com"
        assert "urls_checked" in data
        assert "changes_detected" in data
        assert data["status"] in ("all_ok", "changes_detected")

    def test_run_not_found(self, client, growth_key):
        resp = client.post(
            "/v1/monitor/run?domain=nonexistent.com", headers={"X-API-Key": growth_key}
        )
        assert resp.status_code == 404

    def test_run_blocked_for_free_tier(self, client, api_key):
        resp = client.post(
            "/v1/monitor/run?domain=netflix.com", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 403


# ── Verify endpoint ──────────────────────────────────────────────────


class TestMonitorVerify:
    def test_verify_resets_confidence(self, client, growth_key, db):
        path = (
            db.query(LifecyclePath)
            .join(Service)
            .filter(Service.domain == "netflix.com")
            .first()
        )
        path.confidence = 0.5
        db.commit()

        resp = client.post(
            "/v1/monitor/verify/netflix.com", headers={"X-API-Key": growth_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_confidence"] == 0.95
        assert data["paths_updated"] > 0

        db.refresh(path)
        assert path.confidence == 0.95

    def test_verify_resolves_stale_flags(self, client, growth_key, db):
        service = db.query(Service).filter(Service.domain == "netflix.com").first()
        db.add(StaleFlag(
            service_id=service.id,
            reason="Test flag",
            severity="warning",
        ))
        db.commit()

        resp = client.post(
            "/v1/monitor/verify/netflix.com", headers={"X-API-Key": growth_key}
        )
        data = resp.json()
        assert data["stale_flags_resolved"] == 1

        flag = db.query(StaleFlag).first()
        assert flag.resolved is True

    def test_verify_not_found(self, client, growth_key):
        resp = client.post(
            "/v1/monitor/verify/nonexistent.com", headers={"X-API-Key": growth_key}
        )
        assert resp.status_code == 404

    def test_verify_by_path_type(self, client, growth_key, db):
        resp = client.post(
            "/v1/monitor/verify/netflix.com?path_type=cancel",
            headers={"X-API-Key": growth_key},
        )
        assert resp.status_code == 200
        assert resp.json()["paths_updated"] >= 1


# ── Decay endpoint ───────────────────────────────────────────────────


class TestDecayEndpoint:
    def test_trigger_decay(self, client, enterprise_key):
        resp = client.post("/v1/monitor/decay", headers={"X-API-Key": enterprise_key})
        assert resp.status_code == 200
        assert "paths_decayed" in resp.json()

    def test_decay_blocked_for_free_tier(self, client, api_key):
        resp = client.post("/v1/monitor/decay", headers={"X-API-Key": api_key})
        assert resp.status_code == 403


# ── Report check endpoint ────────────────────────────────────────────


class TestReportCheckEndpoint:
    def test_trigger_report_check(self, client, enterprise_key):
        resp = client.post(
            "/v1/monitor/report-check", headers={"X-API-Key": enterprise_key}
        )
        assert resp.status_code == 200
        assert "services_auto_flagged" in resp.json()

    def test_report_check_blocked_for_free_tier(self, client, api_key):
        resp = client.post(
            "/v1/monitor/report-check", headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 403


# ── Webhook CRUD ─────────────────────────────────────────────────────


class TestWebhookCRUD:
    def test_create_webhook(self, client, growth_key):
        resp = client.post(
            "/v1/webhooks?url=https://example.com/hook&events=path_stale",
            headers={"X-API-Key": growth_key},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/hook"
        assert data["events"] == ["path_stale"]
        assert data["signing_secret"] is not None
        assert data["is_active"] is True

    def test_create_webhook_both_events(self, client, growth_key):
        resp = client.post(
            "/v1/webhooks?url=https://example.com/hook&events=path_stale,path_fixed",
            headers={"X-API-Key": growth_key},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "path_stale" in data["events"]
        assert "path_fixed" in data["events"]

    def test_create_webhook_invalid_event(self, client, growth_key):
        resp = client.post(
            "/v1/webhooks?url=https://example.com/hook&events=invalid_event",
            headers={"X-API-Key": growth_key},
        )
        assert resp.status_code == 400

    def test_list_webhooks(self, client, growth_key):
        client.post(
            "/v1/webhooks?url=https://example.com/hook&events=path_stale",
            headers={"X-API-Key": growth_key},
        )

        resp = client.get("/v1/webhooks", headers={"X-API-Key": growth_key})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["webhooks"]) == 1
        assert data["webhooks"][0]["url"] == "https://example.com/hook"

    def test_delete_webhook(self, client, growth_key):
        create_resp = client.post(
            "/v1/webhooks?url=https://example.com/hook&events=path_stale",
            headers={"X-API-Key": growth_key},
        )
        wh_id = create_resp.json()["id"]

        resp = client.delete(
            f"/v1/webhooks/{wh_id}", headers={"X-API-Key": growth_key}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        list_resp = client.get("/v1/webhooks", headers={"X-API-Key": growth_key})
        assert len(list_resp.json()["webhooks"]) == 0

    def test_delete_webhook_not_found(self, client, growth_key):
        resp = client.delete("/v1/webhooks/99999", headers={"X-API-Key": growth_key})
        assert resp.status_code == 404

    def test_webhook_deliveries_empty(self, client, growth_key):
        create_resp = client.post(
            "/v1/webhooks?url=https://example.com/hook&events=path_stale",
            headers={"X-API-Key": growth_key},
        )
        wh_id = create_resp.json()["id"]

        resp = client.get(
            f"/v1/webhooks/{wh_id}/deliveries", headers={"X-API-Key": growth_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["webhook_id"] == wh_id
        assert data["deliveries"] == []

    def test_webhook_deliveries_not_found(self, client, growth_key):
        resp = client.get(
            "/v1/webhooks/99999/deliveries", headers={"X-API-Key": growth_key}
        )
        assert resp.status_code == 404

    def test_webhooks_blocked_for_free_tier(self, client, api_key):
        resp = client.post(
            "/v1/webhooks?url=https://example.com/hook&events=path_stale",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403


# ── Dashboard monitoring endpoints ───────────────────────────────────


class TestDashboardMonitoring:
    def test_stats_includes_monitoring(self, client):
        resp = client.get("/v1/dashboard/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "monitoring" in data
        assert "total_checks" in data["monitoring"]
        assert "open_stale_flags" in data["monitoring"]
        assert "active_webhooks" in data["monitoring"]
        assert "avg_confidence" in data["monitoring"]

    def test_monitoring_dashboard(self, client):
        resp = client.get("/v1/dashboard/monitoring")
        assert resp.status_code == 200
        data = resp.json()
        assert "stale_flags" in data
        assert "recent_checks" in data
        assert "lowest_confidence" in data
        assert isinstance(data["stale_flags"], list)
        assert isinstance(data["recent_checks"], list)
        assert isinstance(data["lowest_confidence"], list)
