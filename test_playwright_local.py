import asyncio
from playwright.async_api import async_playwright

async def main():
    loop = asyncio.get_running_loop()
    print("Event loop:", type(loop).__name__)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        print("Chromium launched successfully")
        await browser.close()

asyncio.run(main())