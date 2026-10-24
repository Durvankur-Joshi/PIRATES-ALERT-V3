from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import pytest
from app.browser.page_loader import NavigationError, load_rendered_page
from app.browser.playwright_manager import playwright_manager
from app.extraction.engine import run_extraction_pipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FixtureHttpHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/catalog"):
            html = (FIXTURES_DIR / "unfamiliar_ecommerce.html").read_text(encoding="utf-8")
            # Inject an SSRF target subresource and a popup trigger to test browser interception
            injected_html = html.replace(
                "</body>",
                '<img src="http://169.254.169.254/latest/meta-data/token.png" alt="ssrf_trap">'
                '<button id="popup-btn" onclick="window.open(\'http://127.0.0.1:59998/popup\')">Popup</button>'
                '</body>',
            )
            data = injected_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/crafts/"):
            data = b"fake_craft_data"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.send_header("Connection", "close")
            self.end_headers()

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def local_fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHttpHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()




@pytest.mark.asyncio
async def test_real_playwright_browser_renders_local_fixture_with_ssrf_blocking(
    local_fixture_server, monkeypatch
):
    server_base = local_fixture_server
    catalog_url = f"{server_base}/catalog"

    from app.security import url_validation

    orig_validate_url = url_validation.validate_url

    def mock_validate_url(url: str, resolve_dns: bool = True) -> str:
        if url.startswith(server_base):
            return url
        return orig_validate_url(url, resolve_dns=resolve_dns)

    monkeypatch.setattr("app.browser.page_loader.validate_url", mock_validate_url)

    rendered_html, final_url = await load_rendered_page(catalog_url, timeout_ms=10000)
    assert rendered_html is not None
    assert "Boutique Artisan Goods" in rendered_html
    assert "Handmade Ceramic Coffee Mug" in rendered_html

    result = await run_extraction_pipeline(
        html=rendered_html,
        source_url=final_url,
        requested_page_type="ecommerce",
        enable_learning=False,
    )
    assert "metadata" in result
    assert result["sourceUrl"] == final_url


@pytest.mark.asyncio
async def test_browser_context_cleanup_on_navigation_error(monkeypatch):
    invalid_url = "http://127.0.0.1:59999/unreachable"

    monkeypatch.setattr("app.browser.page_loader.validate_url", lambda u, **kwargs: u)

    with pytest.raises(NavigationError):
        await load_rendered_page(invalid_url, timeout_ms=3000)

    browser = await playwright_manager.get_browser()
    assert browser.is_connected() is True
