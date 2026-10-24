import json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from app.config import settings
from app.main import app

client = TestClient(app)
EXTENSION_DIR = Path(__file__).parent.parent / "extension"


def test_manifest_v3_validity():
    manifest_path = EXTENSION_DIR / "manifest.json"
    assert manifest_path.exists(), "manifest.json must exist in extension/"

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data.get("manifest_version") == 3
    assert data.get("name") == "PIRATES ALERT"
    assert "version" in data
    assert "action" in data
    assert data["action"].get("default_popup") == "popup.html"

    # Verify permissions are minimal and appropriate
    permissions = data.get("permissions", [])
    assert "activeTab" in permissions
    assert "storage" in permissions
    assert "<all_urls>" not in permissions

    # Verify icon assets exist on disk
    icons = data.get("icons", {})
    for size, rel_path in icons.items():
        icon_file = EXTENSION_DIR / rel_path
        assert icon_file.exists(), f"Icon file {rel_path} must exist"
        assert icon_file.stat().st_size > 0


def test_extension_files_have_no_secrets_or_cdns():
    html_path = EXTENSION_DIR / "popup.html"
    js_path = EXTENSION_DIR / "popup.js"
    css_path = EXTENSION_DIR / "popup.css"

    assert html_path.exists()
    assert js_path.exists()
    assert css_path.exists()

    html_content = html_path.read_text(encoding="utf-8")
    js_content = js_path.read_text(encoding="utf-8")

    # Verify no external CDNs or remote scripts
    assert "http://" not in html_content.lower() or "localhost" in html_content.lower() or "127.0.0.1" in html_content.lower()
    assert "https://" not in html_content.lower() or "example.com" in html_content.lower()
    assert "googleapis" not in html_content.lower()
    assert "cdnjs" not in html_content.lower()
    assert "unpkg" not in html_content.lower()

    # Verify no API keys in extension code
    assert "AIzaSy" not in js_content
    assert "gemini_api_key" not in js_content.lower()


def test_cors_options_preflight_for_chrome_extension():
    # Chrome extensions send Origin: chrome-extension://<32-letter-id>
    ext_origin = "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef"

    headers = {
        "Origin": ext_origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-request-id",
    }

    res = client.options("/api/v1/extract", headers=headers)
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == ext_origin


def test_cors_rejects_unauthorized_web_origin():
    disallowed_origin = "https://malicious-site.example.com"
    headers = {
        "Origin": disallowed_origin,
        "Access-Control-Request-Method": "POST",
    }
    res = client.options("/api/v1/extract", headers=headers)
    # When origin is rejected by CORSMiddleware, access-control-allow-origin is omitted
    assert res.headers.get("access-control-allow-origin") != disallowed_origin


def test_health_check_returns_database_readiness():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert "status" in data
    assert "playwrightAvailable" in data
    assert "databaseAvailable" in data
    assert data["databaseAvailable"] is True
