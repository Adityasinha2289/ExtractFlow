from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


class ScraperError(Exception):
    pass


class DynamicScraper:
    """
    A generic playwright scraper that navigates to a URL,
    waits for selectors, performs interactions, and returns rendered HTML.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.timeout = config.get("timeout", 30000)

    async def scrape(self, url: str) -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                await page.goto(
                    url, timeout=self.timeout, wait_until="domcontentloaded"
                )

                wait_selector = self.config.get("wait_for_selector")
                if wait_selector:
                    await page.wait_for_selector(wait_selector, timeout=self.timeout)

                for step in self.config.get("interactions", []):
                    action = step.get("action")
                    selector = step.get("selector")
                    if action == "click" and selector:
                        button = page.locator(selector)
                        if await button.count() > 0:
                            await button.first.click(timeout=5000)
                            await page.wait_for_timeout(step.get("delay", 1000))

                content = await page.content()
                return content
            except PlaywrightTimeoutError:
                raise ScraperError(f"Timeout while scraping {url}")
            finally:
                await browser.close()
