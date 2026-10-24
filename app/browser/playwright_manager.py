import asyncio
from typing import Optional
from playwright.async_api import Browser, Playwright, async_playwright
from app.config import settings


class PlaywrightManager:
    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._browser_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop != current_loop:
            self._lock = asyncio.Lock()
            self._lock_loop = current_loop
        return self._lock

    async def start(self) -> None:
        lock = self._get_lock()
        async with lock:
            current_loop = asyncio.get_running_loop()
            if self._browser_loop != current_loop:
                self._browser = None
                self._playwright = None

            if self._playwright is None:
                self._playwright = await async_playwright().start()
                self._browser_loop = current_loop

            if self._browser is None or not self._browser.is_connected():
                self._browser = await self._playwright.chromium.launch(
                    headless=settings.playwright_headless
                )
                self._browser_loop = current_loop

    async def get_browser(self) -> Browser:
        current_loop = asyncio.get_running_loop()
        if (
            self._browser is None
            or self._browser_loop != current_loop
            or not self._browser.is_connected()
        ):
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
        try:
            lock = self._get_lock()
            async with lock:
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
                self._browser_loop = None
        except Exception:
            self._browser = None
            self._playwright = None
            self._browser_loop = None


playwright_manager = PlaywrightManager()
