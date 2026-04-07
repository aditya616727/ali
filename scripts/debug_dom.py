"""Debug script to inspect Alibaba page DOM structure."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from playwright.async_api import async_playwright
from ali_scraper.config.settings import settings

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=[
            "--disable-blink-features=AutomationControlled",
        ])
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        await ctx.add_init_script(STEALTH_JS)
        page = await ctx.new_page()

        # --- LISTING PAGE ---
        print("=" * 60)
        print("LISTING PAGE ANALYSIS")
        print("=" * 60)
        await page.goto(settings.PRODUCT_LIST_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(1000)

        listing_debug = await page.evaluate("""() => {
            const results = [];
            const links = document.querySelectorAll('a[href*="/product-detail/"]');
            const seen = new Set();
            let count = 0;
            links.forEach(link => {
                if (count >= 3) return;
                if (seen.has(link.href)) return;
                seen.add(link.href);
                count++;
                let el = link;
                for (let i = 0; i < 4; i++) {
                    if (el.parentElement) el = el.parentElement;
                }
                results.push({
                    linkHref: link.href,
                    linkText: link.innerText.substring(0, 200),
                    linkHTML: link.outerHTML.substring(0, 500),
                    parentHTML: el.outerHTML.substring(0, 2000),
                    parentClasses: el.className,
                    parentTag: el.tagName,
                });
            });
            return results;
        }""")
        for i, item in enumerate(listing_debug):
            print(f"\n--- Product Card {i+1} ---")
            print(f"Link: {item['linkHref']}")
            print(f"Link text: {item['linkText']}")
            print(f"Parent tag/class: {item['parentTag']}.{item['parentClasses']}")
            print(f"Parent HTML:\n{item['parentHTML'][:1500]}")
            print()

        img_debug = await page.evaluate("""() => {
            const imgs = [];
            document.querySelectorAll('img').forEach(img => {
                const src = img.src || img.dataset.src || '';
                if (src && src.startsWith('http') && (src.includes('kf/') || src.includes('imgextra'))) {
                    const parent = img.closest('a');
                    imgs.push({
                        src: src.substring(0, 200),
                        parentHref: parent ? parent.href.substring(0, 200) : '',
                        alt: img.alt
                    });
                }
            });
            return imgs.slice(0, 10);
        }""")
        print("\n--- Images on listing page ---")
        for img in img_debug:
            print(f"  src: {img['src']}")
            print(f"  parent link: {img['parentHref']}")
            print(f"  alt: {img['alt']}")
            print()

        await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
