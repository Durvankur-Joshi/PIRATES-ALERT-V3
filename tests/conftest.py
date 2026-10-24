import asyncio
import pytest
from app.browser.playwright_manager import playwright_manager


@pytest.fixture(scope="session", autouse=True)
def shutdown_playwright_on_session_finish():
    yield
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(playwright_manager.close())
        loop.close()
    except Exception:
        pass
