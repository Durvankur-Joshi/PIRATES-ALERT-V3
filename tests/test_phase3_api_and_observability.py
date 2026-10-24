import io
import json
import logging
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import pytest
from app.ai.gemini_client import gemini_client
from app.ai.strategy_schemas import ExtractionStrategy, FieldExtractionRule
from app.main import app
from app.monitoring.observability import log_extraction_event, metrics_collector
from app.rules.repository import strategy_repository


client = TestClient(app)


def make_dummy_strategy(selector: str = "section.card") -> ExtractionStrategy:
    return ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_item_selector=selector,
        fields={
            "title": FieldExtractionRule(selector="span.title", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a.link", attribute="href", required=True),
        },
    )


@pytest.mark.asyncio
async def test_strategies_api_listing_and_rollback(monkeypatch, tmp_path):
    db_file = tmp_path / "api_test.db"
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    strat1 = make_dummy_strategy("section.card-v1")
    await strategy_repository.save_version(
        hostname="api-shop.com",
        page_type="ecommerce",
        fingerprint="fp_1",
        strategy=strat1,
        validation_metrics={"qualityScore": 0.85},
        validation_status="passed",
    )

    strat2 = make_dummy_strategy("section.card-v2")
    await strategy_repository.save_version(
        hostname="api-shop.com",
        page_type="ecommerce",
        fingerprint="fp_2",
        strategy=strat2,
        validation_metrics={"qualityScore": 0.95},
        validation_status="passed",
    )

    res = client.get("/api/v1/strategies", params={"hostname": "api-shop.com"})
    assert res.status_code == 200
    data = res.json()
    assert data["hostname"] == "api-shop.com"
    assert data["totalVersions"] == 2
    assert data["versions"][0]["version"] == 2
    assert data["versions"][0]["isActive"] is True
    assert data["versions"][1]["version"] == 1
    assert data["versions"][1]["isActive"] is False

    rollback_payload = {
        "hostname": "api-shop.com",
        "pageType": "ecommerce",
        "targetVersion": 1,
        "reason": "V2 had pagination bugs",
    }
    rb_res = client.post("/api/v1/strategies/rollback", json=rollback_payload)
    assert rb_res.status_code == 200
    rb_data = rb_res.json()
    assert rb_data["success"] is True
    assert rb_data["previousVersion"] == 2
    assert rb_data["activeVersion"] == 1

    rb_fail = client.post(
        "/api/v1/strategies/rollback",
        json={
            "hostname": "api-shop.com",
            "pageType": "ecommerce",
            "targetVersion": 999,
            "reason": "Invalid version test",
        },
    )
    assert rb_fail.status_code == 400
    assert "not found" in rb_fail.json()["detail"].lower()


def test_metrics_endpoint():
    res = client.get("/api/v1/metrics")
    assert res.status_code == 200
    data = res.json()
    assert "uptimeSeconds" in data
    assert "totalRequests" in data
    assert "savedStrategyReuseCount" in data
    assert "totalAiCalls" in data
    assert "totalRollbacks" in data


@pytest.mark.asyncio
async def test_extract_endpoint_includes_phase3_metadata(monkeypatch):
    html_fixture = """
    <!DOCTYPE html>
    <html><body>
      <ul class="job-list">
        <li><h3><a href="/jobs/1">Backend Engineer</a></h3></li>
        <li><h3><a href="/jobs/2">Frontend Engineer</a></h3></li>
        <li><h3><a href="/jobs/3">Platform Engineer</a></h3></li>
      </ul>
    </body></html>
    """

    async def mock_load(url, timeout_ms=None):
        return html_fixture, url

    monkeypatch.setattr("app.main.validate_url", lambda u: u)
    monkeypatch.setattr("app.main.load_rendered_page", mock_load)

    res = client.post(
        "/api/v1/extract",
        json={"url": "https://careers.example.org/listings", "pageType": "generic_listing"},
        headers={"X-Request-ID": "test-req-custom-999"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    meta = data["metadata"]
    assert meta["requestId"] == "test-req-custom-999"
    assert "qualityScore" in meta
    assert meta["qualityScore"] is not None
    assert "executionTimeMs" in meta
    assert "stageDurations" in meta


def test_logging_does_not_leak_secrets_or_full_html():
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    logger = logging.getLogger("pirates_alert")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    secret_key = "AIzaSyD-SECRET_KEY_12345"
    full_html = "<html><body>" + "Sensitive content " * 500 + "</body></html>"

    log_extraction_event(
        request_id="req_test_123",
        hostname="https://example.com/search?token=secret123",
        page_type="ecommerce",
        strategy_source="ai_learned",
        record_count=10,
        ai_calls=1,
        quality_score=0.92,
        total_ms=150.0,
        stage_durations={"deterministic": 10.0, "ai_learning": 140.0},
        warnings=[],
        strategy_id="strat_example_com",
        strategy_version=2,
    )

    logged_output = log_capture.getvalue()
    assert secret_key not in logged_output
    assert full_html not in logged_output
    assert "EXTRACTION_EVENT" in logged_output
    assert "req_test_123" in logged_output
