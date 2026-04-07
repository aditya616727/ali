"""Main CLI entry point for ali-scraper."""

import asyncio
import json
import logging
import os

from playwright.async_api import async_playwright

from .config.settings import settings
from .proxy import fetch_webshare_proxies
from .cloudflare import CloudflareUploader
from .database import MongoStorage
from .scrapers import AlibabaScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run():
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)

    scraper = AlibabaScraper()

    # CAPTCHA solver
    captcha_solver = None
    if settings.CAPSOLVER_API_KEY:
        from .captcha import CaptchaSolver
        captcha_solver = CaptchaSolver(settings.CAPSOLVER_API_KEY)
        logger.info("CapSolver CAPTCHA solving enabled")

    # Proxies
    proxies = []
    if settings.USE_PROXY and settings.WEBSHARE_API_KEY:
        proxies = await fetch_webshare_proxies(settings.WEBSHARE_API_KEY, settings.PROXY_COUNTRIES)

    # Cloudflare uploader
    cf = None
    if settings.CLOUDFLARE_ACCOUNT_ID and settings.CLOUDFLARE_API_TOKEN:
        cf = CloudflareUploader(
            settings.CLOUDFLARE_ACCOUNT_ID,
            settings.CLOUDFLARE_API_TOKEN,
            settings.MAX_CONCURRENT_UPLOADS,
        )

    # MongoDB
    mongo = None
    existing = set()
    if settings.MONGODB_URI:
        try:
            mongo = MongoStorage(settings.MONGODB_URI)
            existing = mongo.get_existing_urls()
            logger.info("Found %d existing products in DB", len(existing))
        except Exception as e:
            logger.error("MongoDB connection failed: %s — JSON-only mode", e)

    proxy_index = 0
    proxy_lock = asyncio.Lock()

    async with async_playwright() as p:
        launch_args = {
            "headless": settings.HEADLESS,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
        }
        if not settings.CHROME_SANDBOX:
            launch_args["args"].extend([
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
            ])
        if settings.CHROME_PATH:
            launch_args["executable_path"] = settings.CHROME_PATH

        browser = await p.chromium.launch(**launch_args)

        async def _set_sweden_cookies(ctx):
            """Set Alibaba cookies: delivery=Sweden, currency=SEK, language=English."""
            await ctx.add_cookies([
                {
                    "name": "aep_usuc_f",
                    "value": "site=glo&c_tp=SEK&region=SE&b_locale=en_US",
                    "domain": ".alibaba.com",
                    "path": "/",
                },
                {
                    "name": "intl_locale",
                    "value": "en_US",
                    "domain": ".alibaba.com",
                    "path": "/",
                },
                {
                    "name": "ALISITE_CountryCode",
                    "value": "SE",
                    "domain": ".alibaba.com",
                    "path": "/",
                },
                {
                    "name": "xman_us_f",
                    "value": "x_locale=en_US&x_l=0&x_user=SE|default|SEK",
                    "domain": ".alibaba.com",
                    "path": "/",
                },
                {
                    "name": "ali_apache_track",
                    "value": "c_mid=&c_lid=&c_cc=SE",
                    "domain": ".alibaba.com",
                    "path": "/",
                },
                {
                    "name": "ali_ab_rad",
                    "value": "1_true",
                    "domain": ".alibaba.com",
                    "path": "/",
                },
            ])
            await ctx.set_extra_http_headers({
                "Accept-Language": "en-US,en;q=0.9",
            })

        async def _make_context_opts():
            nonlocal proxy_index
            opts = {
                "user_agent": scraper.pick_ua(),
                "viewport": {"width": 1920, "height": 1080},
                "locale": "en-US",
                "timezone_id": "Europe/Stockholm",
                "geolocation": {"latitude": 59.3293, "longitude": 18.0686},
                "permissions": ["geolocation"],
            }
            if proxies:
                async with proxy_lock:
                    px = proxies[proxy_index % len(proxies)]
                    proxy_index += 1
                opts["proxy"] = {
                    "server": px["server"],
                    "username": px["username"],
                    "password": px["password"],
                }
            return opts

        # --- Phase 1: Listing pages — fresh context+proxy per page ---
        raw_products: list[dict] = []
        all_seen: set[str] = set()
        unlimited = settings.MAX_PAGES == -1
        page_limit = 10_000 if unlimited else settings.MAX_PAGES
        next_url: str | None = settings.PRODUCT_LIST_URL

        for page_num in range(1, page_limit + 1):
            if next_url is None:
                break
            url_to_load = next_url

            page_ok = False
            # Retry the same listing page up to 3 proxies, then once without proxy
            max_listing_attempts = (min(3, len(proxies)) + 1) if proxies else 1
            for _attempt in range(max_listing_attempts):
                if _attempt == max_listing_attempts - 1 and proxies:
                    logger.info("Listing page %d: all proxy attempts failed — trying direct", page_num)
                    ctx_opts = {
                        "user_agent": scraper.pick_ua(),
                        "viewport": {"width": 1920, "height": 1080},
                        "locale": "en-US",
                        "timezone_id": "Europe/Stockholm",
                        "geolocation": {"latitude": 59.3293, "longitude": 18.0686},
                        "permissions": ["geolocation"],
                    }
                else:
                    ctx_opts = await _make_context_opts()

                try:
                    context = await browser.new_context(**ctx_opts)
                    await _set_sweden_cookies(context)
                    await context.add_init_script(AlibabaScraper.stealth_js)
                    lpage = await context.new_page()
                    prods, next_url = await scraper.scrape_single_listing_page(
                        lpage, url_to_load, page_num, captcha_solver=captcha_solver,
                    )
                    await context.close()

                    new_prods = [p for p in prods if p.get("url") and p["url"] not in all_seen]
                    for p in new_prods:
                        all_seen.add(p["url"])
                    raw_products.extend(new_prods)
                    page_ok = True

                    proxy_label = ctx_opts.get("proxy", {}).get("server", "direct") if proxies else "direct"
                    logger.info("Listing page %d: got %d products via %s", page_num, len(new_prods), proxy_label)
                    break
                except Exception as e:
                    logger.warning("Listing page %d attempt %d failed: %s", page_num, _attempt + 1, e)
                    try:
                        await context.close()
                    except Exception:
                        pass

            if not page_ok:
                logger.error("Listing page %d failed all attempts — stopping pagination", page_num)
                break

            if next_url is None:
                logger.info("No more pages after page %d.", page_num)
                break

            await scraper.random_delay()

        logger.info("Total unique products from all listing pages: %d", len(raw_products))

        # Filter already-scraped
        if settings.FORCE_RESCRAPE:
            new_raw = raw_products
            logger.info("FORCE_RESCRAPE enabled — processing all %d products", len(new_raw))
        else:
            new_raw = [r for r in raw_products if r.get("url") not in existing]
            logger.info(
                "New products: %d (skipped %d existing)",
                len(new_raw), len(raw_products) - len(new_raw),
            )

        # --- Phase 2: Detail pages (if enabled) ---
        details_map: dict[str, dict] = {}

        if settings.SCRAPE_DETAILS and new_raw:
            logger.info(
                "SCRAPE_DETAILS enabled — visiting %d detail pages with %d workers (delay %d-%ds)",
                len(new_raw), settings.DETAIL_WORKERS, settings.DETAIL_DELAY_MIN, settings.DETAIL_DELAY_MAX,
            )

            success = 0
            failed = 0
            completed = 0
            sem = asyncio.Semaphore(settings.DETAIL_WORKERS)
            results_lock = asyncio.Lock()

            async def _scrape_one(raw: dict):
                nonlocal success, failed, completed
                url = raw.get("url", "")
                if not url:
                    return

                async with sem:
                    detail = None
                    # Always rotate to a fresh proxy for every attempt
                    max_proxy_retries = min(len(proxies), 5) if proxies else 1
                    total_attempts = max_proxy_retries + 1  # +1 for direct fallback
                    for attempt in range(total_attempts):
                        # Last attempt: try without any proxy
                        if attempt == total_attempts - 1 and proxies:
                            ctx_opts = {
                                "user_agent": scraper.pick_ua(),
                                "viewport": {"width": 1920, "height": 1080},
                                "locale": "en-US",
                                "timezone_id": "Europe/Stockholm",
                                "geolocation": {"latitude": 59.3293, "longitude": 18.0686},
                                "permissions": ["geolocation"],
                            }
                            logger.info("  Detail attempt %d/%d (direct, no proxy): %s", attempt + 1, total_attempts, url[:60])
                        else:
                            ctx_opts = await _make_context_opts()
                            proxy_label = ctx_opts.get("proxy", {}).get("server", "direct") if proxies else "direct"
                            logger.info("  Detail attempt %d/%d via %s: %s", attempt + 1, total_attempts, proxy_label, url[:60])

                        ctx = await browser.new_context(**ctx_opts)
                        await _set_sweden_cookies(ctx)
                        await ctx.add_init_script(AlibabaScraper.stealth_js)
                        detail_page = await ctx.new_page()

                        detail = await scraper.scrape_detail_page(
                            detail_page, url, captcha_solver=captcha_solver,
                        )
                        await ctx.close()

                        if detail is not None:
                            break

                    async with results_lock:
                        if detail:
                            details_map[url] = detail
                            success += 1
                        else:
                            failed += 1
                        completed += 1
                        logger.info(
                            "  Detail progress: %d/%d (ok=%d, fail=%d)",
                            completed, len(new_raw), success, failed,
                        )

                    # Human-like delay so each worker pauses between its own requests
                    await scraper.random_delay(
                        settings.DETAIL_DELAY_MIN, settings.DETAIL_DELAY_MAX,
                    )

            await asyncio.gather(*[_scrape_one(r) for r in new_raw])

            logger.info(
                "Detail scraping complete: %d succeeded, %d failed", success, failed,
            )

        await browser.close()

    # Build product documents (merge listing + detail data)
    # build_product_doc returns None for products that should be skipped
    products = [
        doc for r in new_raw
        if (doc := scraper.build_product_doc(r, details_map.get(r.get("url")))) is not None
    ]
    logger.info(
        "Built %d product documents (%s)",
        len(products),
        "including failed" if settings.STORE_FAILED_PRODUCTS else "skipped failed",
    )

    # Upload images to Cloudflare
    if cf:
        for prod in products:
            post_data = prod.get("postAdData", {})
            img_list = post_data.get("images", [])
            if img_list:
                logger.info("Uploading images for: %s", post_data.get("title", "?")[:50])
                original_urls = [img["source_url"] for img in img_list]
                cf_urls = await cf.upload_many(original_urls)
                if cf_urls:
                    for i, cf_url in enumerate(cf_urls):
                        if i < len(img_list):
                            img_list[i]["url"] = cf_url
            for v in post_data.get("variants", []):
                if v.get("images"):
                    cf_v = await cf.upload_many(v["images"])
                    if cf_v:
                        v["images"] = cf_v

    # Save to MongoDB
    if mongo:
        saved = 0
        for prod in products:
            try:
                mongo.upsert_product(prod)
                saved += 1
            except Exception as e:
                logger.error("DB save failed: %s", e)
        logger.info("Saved %d products to MongoDB", saved)

    # Save JSON
    out = os.path.join(settings.OUTPUT_DIR, "kukirin_products.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False, default=str)
    logger.info("Saved %d products to %s", len(products), out)

    if mongo:
        mongo.close()

    return products


def main():
    products = asyncio.run(run())
    print(f"\n{'='*60}")
    print(f"Scraping complete! {len(products)} products collected.")
    print(f"Output: {settings.OUTPUT_DIR}/kukirin_products.json")
    print(f"{'='*60}")
    for p in products[:10]:
        pd = p.get("postAdData", {})
        t = pd.get("title", "?")[:70]
        imgs = len(pd.get("images", []))
        vrs = len(pd.get("variants", []))
        specs = len(pd.get("additionalFields", {}))
        print(f"  {imgs} img | {vrs} var | {specs} specs | {t}")
    if len(products) > 10:
        print(f"  ... and {len(products) - 10} more")


if __name__ == "__main__":
    main()
