"""Debug script to test setting delivery country to Sweden on Alibaba."""
import asyncio
import sys
sys.path.insert(0, "src")

from playwright.async_api import async_playwright
from ali_scraper.scrapers.alibaba import AlibabaScraper


async def main():
    scraper = AlibabaScraper()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="sv-SE",
            timezone_id="Europe/Stockholm",
        )
        await ctx.add_cookies([
            {"name": "aep_usuc_f", "value": "site=glo&c_tp=USD&region=SE&b_locale=en_US", "domain": ".alibaba.com", "path": "/"},
            {"name": "ALISITE_CountryCode", "value": "SE", "domain": ".alibaba.com", "path": "/"},
        ])
        page = await ctx.new_page()
        await page.goto("https://kukirin.en.alibaba.com/productlist.html", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # Check current delivery country
        current = await page.locator('.tnh-country-flag span').first.text_content()
        print(f"Current delivery country: {current}")

        # Try setting to Sweden using the scraper method
        result = await scraper.set_delivery_country(page, "Sweden")
        print(f"set_delivery_country returned: {result}")

        await page.wait_for_timeout(3000)

        # Check again
        try:
            new_country = await page.locator('.tnh-country-flag span').first.text_content()
            print(f"New delivery country: {new_country}")
        except Exception as e:
            print(f"Could not check new country: {e}")

        await browser.close()


asyncio.run(main())
