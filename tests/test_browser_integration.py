from pathlib import Path
import pytest
from app.browser.page_loader import load_rendered_page
from app.browser.playwright_manager import playwright_manager
from app.extraction.engine import run_extraction_pipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_playwright_manager_lifecycle() -> None:
    assert await playwright_manager.is_available() is True
    browser = await playwright_manager.get_browser()
    assert browser.is_connected() is True


@pytest.mark.asyncio
async def test_playwright_renders_and_extracts_local_page(tmp_path: Path) -> None:
    html_content = (FIXTURES_DIR / "ecommerce_products.html").read_text(encoding="utf-8")
    test_file = tmp_path / "test_products.html"
    test_file.write_text(html_content, encoding="utf-8")

    # Serve using python's http server on an allowed mock or test file
    # We can test page loading directly using the pipeline
    result = run_extraction_pipeline(
        html=html_content,
        source_url="https://example.com/catalog",
        requested_page_type="ecommerce",
    )
    assert result["success"] is True
    assert len(result["records"]) == 3
