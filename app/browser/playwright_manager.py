import asyncio
from typing import Optional
from playwright.async_api import Browser, Playwright, async_playwright
from app.config import settings


class PlaywrightManager:
    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            if self._browser is None or not self._browser.is_connected():
                self._browser = await self._playwright.chromium.launch(
                    headless=settings.playwright_headless
                )

    async def get_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            await self.start()
        if self._browser is None:
            raise RuntimeError("Failed to initialize Playwright browser")
        return self._browser

    async def is_available(self) -> bool:
        try:
            browser = await self.get_browser()
            return browser.is_connected()
        except Exception:
            return False

    async def close(self) -> None:
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None


playwright_manager = PlaywrightManager()
