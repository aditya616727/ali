"""Dump a single Alibaba product detail page HTML for DOM inspection.

Uses the same stealth JS + proxy setup as the main scraper.
Saves raw HTML and extracts window.runParams / __INIT_DATA__ if found.
"""

import asyncio
import json
import os
import sys

# Add project root to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from playwright.async_api import async_playwright
from ali_scraper.config.settings import settings
from ali_scraper.proxy import fetch_webshare_proxies
from ali_scraper.scrapers.alibaba import STEALTH_JS

TARGET_URL = "https://www.alibaba.com/product-detail/KuKirin-X1-Electric-Motorbike-Mid-Drive_1601735482642.html"


async def main():
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)

    # Get a proxy
    proxies = []
    if settings.USE_PROXY and settings.WEBSHARE_API_KEY:
        proxies = await fetch_webshare_proxies(settings.WEBSHARE_API_KEY, settings.PROXY_COUNTRIES)
        print(f"Got {len(proxies)} proxies")

    async with async_playwright() as p:
        launch_args = {
            "headless": settings.HEADLESS,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
            ],
        }
        browser = await p.chromium.launch(**launch_args)

        ctx_opts = {
            "user_agent": settings.USER_AGENTS[0],
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if proxies:
            px = proxies[0]
            ctx_opts["proxy"] = {
                "server": px["server"],
                "username": px["username"],
                "password": px["password"],
            }
            print(f"Using proxy: {px['server']}")

        context = await browser.new_context(**ctx_opts)
        await context.add_init_script(STEALTH_JS)
        page = await context.new_page()

        # Warm session — visit listing page first
        print("Warming session on listing page...")
        await page.goto(settings.PRODUCT_LIST_URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)

        # Navigate to target
        print(f"Loading detail page: {TARGET_URL[:80]}...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # Check for CAPTCHA — if found, wait for manual solve
        captcha_text = await page.evaluate("""() => {
            const el = document.querySelector('#baxia-dialog-content, .baxia-dialog, #nc_1_wrapper');
            return el ? el.innerText : '';
        }""")
        if captcha_text:
            print("CAPTCHA detected! Waiting 30s for manual solve...")
            await page.wait_for_timeout(30000)

        # Scroll to trigger lazy content
        for _ in range(6):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(1000)

        # Save raw HTML
        html = await page.content()
        html_path = os.path.join(settings.OUTPUT_DIR, "product_detail.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Saved HTML ({len(html)} chars) -> {html_path}")

        # Extract JS data objects
        js_data = await page.evaluate("""() => {
            const data = {};
            if (window.runParams) data.runParams = window.runParams;
            if (window.__INIT_DATA__) data.__INIT_DATA__ = window.__INIT_DATA__;
            if (window.detailData) data.detailData = window.detailData;
            if (window.PAGE_DATA) data.PAGE_DATA = window.PAGE_DATA;

            // Also try to find data in script tags
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
                const text = s.textContent || '';
                if (text.includes('window.runParams')) {
                    const match = text.match(/window\\.runParams\\s*=\\s*(\\{[\\s\\S]*?\\});/);
                    if (match) {
                        try { data.runParamsFromScript = JSON.parse(match[1]); } catch(e) {}
                    }
                }
                if (text.includes('detailData')) {
                    const match = text.match(/detailData\\s*[:=]\\s*(\\{[\\s\\S]*?\\});/);
                    if (match) {
                        try { data.detailDataFromScript = JSON.parse(match[1]); } catch(e) {}
                    }
                }
            }
            return data;
        }""")

        if js_data:
            js_path = os.path.join(settings.OUTPUT_DIR, "product_jsdata.json")
            with open(js_path, "w", encoding="utf-8") as f:
                json.dump(js_data, f, indent=2, ensure_ascii=False, default=str)
            print(f"Saved JS data -> {js_path}")
            for k in js_data:
                val = js_data[k]
                if isinstance(val, dict):
                    print(f"  {k}: {len(val)} keys -> {list(val.keys())[:10]}")
                else:
                    print(f"  {k}: {type(val).__name__}")
        else:
            print("No window.runParams or __INIT_DATA__ found")

        # Quick DOM structure dump
        dom_info = await page.evaluate("""() => {
            const info = {};
            
            // Title
            const h1 = document.querySelector('h1');
            info.h1 = h1 ? h1.innerText.trim() : null;
            
            // All element class names that might be product-related
            const classes = new Set();
            document.querySelectorAll('[class]').forEach(el => {
                const c = el.className;
                if (typeof c === 'string' && (
                    c.includes('product') || c.includes('price') || c.includes('sku') ||
                    c.includes('gallery') || c.includes('image') || c.includes('spec') ||
                    c.includes('attr') || c.includes('desc') || c.includes('detail') ||
                    c.includes('supplier') || c.includes('company') || c.includes('variant') ||
                    c.includes('module') || c.includes('offer')
                )) {
                    classes.add(c.substring(0, 120));
                }
            });
            info.relevantClasses = [...classes].sort();
            
            // Count images
            info.totalImages = document.querySelectorAll('img').length;
            
            // Check for common data containers
            info.hasRunParams = !!window.runParams;
            info.hasInitData = !!window.__INIT_DATA__;
            
            return info;
        }""")

        dom_path = os.path.join(settings.OUTPUT_DIR, "dom_structure.json")
        with open(dom_path, "w", encoding="utf-8") as f:
            json.dump(dom_info, f, indent=2, ensure_ascii=False)
        print(f"\nDOM structure saved -> {dom_path}")
        print(f"  h1: {dom_info.get('h1')}")
        print(f"  images: {dom_info.get('totalImages')}")
        print(f"  runParams: {dom_info.get('hasRunParams')}")
        print(f"  __INIT_DATA__: {dom_info.get('hasInitData')}")
        print(f"  relevant classes: {len(dom_info.get('relevantClasses', []))}")

        await page.screenshot(path=os.path.join(settings.OUTPUT_DIR, "detail_screenshot.png"))
        print("Screenshot saved")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
