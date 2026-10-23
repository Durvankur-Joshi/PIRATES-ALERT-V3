from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.browser.page_loader import (
    BotChallengeDetectedError,
    EmptyPageError,
    NavigationError,
    PageLoadTimeoutError,
)
from app.main import app

client = TestClient(app)
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> str:
    path = FIXTURES_DIR / filename
    return path.read_text(encoding="utf-8")


def test_root_endpoint() -> None:
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "PIRATES ALERT V3 API"
    assert "GET /health" in data["endpoints"]


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "playwrightAvailable" in data
    assert "timestamp" in data


def test_extract_rejects_ssrf_urls() -> None:
    blocked_urls = (
        "http://localhost:8000/secret",
        "http://127.0.0.1:9000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/admin",
        "http://10.0.0.1/status",
        "ftp://example.com/test",
    )
    for target in blocked_urls:
        response = client.post("/api/v1/extract", json={"url": target})
        assert response.status_code == 400
        assert "URL validation failed" in response.json()["detail"]


@patch("app.main.load_rendered_page", new_callable=AsyncMock)
@patch("app.main.validate_url")
def test_extract_success_ecommerce(mock_validate_url, mock_load) -> None:
    mock_validate_url.return_value = "https://example.com/search"
    mock_load.return_value = (load_fixture("ecommerce_products.html"), "https://example.com/search")

    response = client.post(
        "/api/v1/extract",
        json={"url": "https://example.com/search", "pageType": "ecommerce"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["pageType"] == "ecommerce"
    assert data["sourceUrl"] == "https://example.com/search"
    assert len(data["records"]) == 3

    first = data["records"][0]
    assert "Galaxy Ultra" in first["title"]
    assert first["sellingPrice"] == 69999.0
    assert first["currency"] == "INR"
    assert first["productId"] == "PROD-101"
    assert data["metadata"]["recordCount"] == 3
    assert data["metadata"]["validationStatus"] == "passed"


@patch("app.main.load_rendered_page", new_callable=AsyncMock)
@patch("app.main.validate_url")
def test_extract_success_generic_jobs(mock_validate_url, mock_load) -> None:
    mock_validate_url.return_value = "https://example.com/careers"
    mock_load.return_value = (load_fixture("generic_jobs.html"), "https://example.com/careers")

    response = client.post(
        "/api/v1/extract",
        json={"url": "https://example.com/careers", "pageType": "generic_listing"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["pageType"] == "generic_listing"
    assert len(data["records"]) == 3
    assert data["records"][0]["extra"]["company"] == "Acme Cloud Systems"


@patch("app.main.load_rendered_page", new_callable=AsyncMock)
@patch("app.main.validate_url")
def test_extract_bot_challenge_returns_403(mock_validate_url, mock_load) -> None:
    mock_validate_url.return_value = "https://example.com/blocked"
    mock_load.side_effect = BotChallengeDetectedError("Bot protection challenge detected")

    response = client.post(
        "/api/v1/extract",
        json={"url": "https://example.com/blocked"},
    )
    assert response.status_code == 403
    assert "Bot protection challenge detected" in response.json()["detail"]


@patch("app.main.load_rendered_page", new_callable=AsyncMock)
@patch("app.main.validate_url")
def test_extract_timeout_returns_504(mock_validate_url, mock_load) -> None:
    mock_validate_url.return_value = "https://example.com/slow"
    mock_load.side_effect = PageLoadTimeoutError("Navigation timed out after 30000ms")

    response = client.post(
        "/api/v1/extract",
        json={"url": "https://example.com/slow"},
    )
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]


@patch("app.main.load_rendered_page", new_callable=AsyncMock)
@patch("app.main.validate_url")
def test_extract_empty_page_returns_422(mock_validate_url, mock_load) -> None:
    mock_validate_url.return_value = "https://example.com/empty"
    mock_load.side_effect = EmptyPageError("Page returned empty content")

    response = client.post(
        "/api/v1/extract",
        json={"url": "https://example.com/empty"},
    )
    assert response.status_code == 422
    assert "empty" in response.json()["detail"]


@patch("app.main.load_rendered_page", new_callable=AsyncMock)
@patch("app.main.validate_url")
def test_extract_navigation_failure_returns_502(mock_validate_url, mock_load) -> None:
    mock_validate_url.return_value = "https://example.com/error"
    mock_load.side_effect = NavigationError("Connection refused by target")

    response = client.post(
        "/api/v1/extract",
        json={"url": "https://example.com/error"},
    )
    assert response.status_code == 502
    assert "Connection refused" in response.json()["detail"]
