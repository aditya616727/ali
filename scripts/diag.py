"""Diagnostic: test navigating to product from within store session."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from playwright.async_api import async_playwright
from ali_scraper.scrapers import AlibabaScraper

PROXY = {"server": "http://p.webshare.io:10061", "username": "obgdtigc-62", "password": "4dfezldrlca4"}

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--no-sandbox",
]


async def main():
    os.makedirs("output", exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=LAUNCH_ARGS)
        ctx = await browser.new_context(
            proxy=PROXY,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()
        await ctx.add_init_script(AlibabaScraper.stealth_js)

        print("Loading listing page...")
        await page.goto("https://kukirin.en.alibaba.com/productlist.html", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)

        # Scroll to trigger lazy-load of product cards
        for _ in range(8):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(800)

        listing_title = await page.title()
        listing_len = len(await page.content())
        print(f"Listing page title: {listing_title}, html len: {listing_len}")
        
        all_links = await page.evaluate(
            "() => [...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => h.includes('alibaba')).slice(0, 10)"
        )
        print("All alibaba links on page:", all_links)

        links = await page.evaluate(
            "() => [...document.querySelectorAll('a[href*=\"product-detail\"], a[href*=\"alibaba.com/product\"]')].map(a => a.href).filter(Boolean).slice(0, 3)"
        )
        print("Product links:", links)

        if links:
            print(f"\nNavigating to: {links[0]}")
            await page.goto(links[0], wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            title = await page.title()
            captcha = await page.locator("#baxia-punish").count()
            h1_count = await page.locator("h1").count()
            print(f"Title: {title}")
            print(f"CAPTCHA: {captcha > 0}")
            print(f"h1 count: {h1_count}")
            if h1_count:
                txt = await page.locator("h1").first.inner_text()
                print(f"h1 text: {txt[:100]}")
            await page.screenshot(path="output/diag_from_store.png", full_page=False)

        await browser.close()

asyncio.run(main())
