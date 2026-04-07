"""Alibaba KuKirin product scraper using Playwright with stealth.

Strategy:
- Listing pages (.icbu-product-card) are accessible without CAPTCHA
- Detail pages may trigger Alibaba CAPTCHA — solved via CapSolver
- Listing data is always collected; detail data enriches it when SCRAPE_DETAILS=true
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timezone

from playwright.async_api import Page

from ..config.settings import settings
from ..captcha.solver import detect_captcha

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enhanced Stealth JS — patches 15+ detection vectors
# ---------------------------------------------------------------------------

STEALTH_JS = """
// --- webdriver ---
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
delete navigator.__proto__.webdriver;

// --- chrome runtime ---
window.chrome = {
    runtime: { onConnect: { addListener: function(){} }, onMessage: { addListener: function(){} } },
    loadTimes: function(){ return {} },
    csi: function(){ return {} },
    app: { isInstalled: false, InstallState: { INSTALLED: 'installed' }, getDetails: function(){}, getIsInstalled: function(){}, runningState: function(){ return 'cannot_run' } }
};

// --- permissions ---
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(p);

// --- plugins ---
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        arr.refresh = function(){};
        return arr;
    }
});

// --- languages ---
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'platform', { get: () => navigator.userAgent.includes('Windows') ? 'Win32' : 'Linux x86_64' });

// --- hardware concurrency ---
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

// --- WebGL ---
const getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParam.call(this, p);
};

// --- connection ---
if (navigator.connection === undefined) {
    Object.defineProperty(navigator, 'connection', {
        get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
    });
}

// --- Notification ---
if (typeof Notification !== 'undefined') {
    Notification.permission = 'default';
}

// --- iframe contentWindow ---
const origHTMLElement = HTMLIFrameElement.prototype.__lookupGetter__('contentWindow');
if (origHTMLElement) {
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() {
            const result = origHTMLElement.call(this);
            if (!result) return result;
            try { result.chrome = window.chrome; } catch(e) {}
            return result;
        }
    });
}

// --- toString spoofing ---
const origToString = Function.prototype.toString;
const customFns = new Set();
Function.prototype.toString = function() {
    if (customFns.has(this)) return 'function () { [native code] }';
    return origToString.call(this);
};
"""


class AlibabaScraper:
    """Handles listing-page scraping and product document building."""

    stealth_js = STEALTH_JS

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    async def random_delay(lo=None, hi=None):
        await asyncio.sleep(random.uniform(lo or settings.DELAY_MIN, hi or settings.DELAY_MAX))

    @staticmethod
    def pick_ua() -> str:
        return random.choice(settings.USER_AGENTS)

    @staticmethod
    def upgrade_image_url(url: str) -> str:
        """Remove Alibaba's _200x200 / _480x480 suffix to get full-res image."""
        url = re.sub(r'_\d+x\d+\.\w+(\?.*)?$', '', url)
        if url.startswith('//'):
            url = 'https:' + url
        return url

    @staticmethod
    def extract_price_range(text: str) -> tuple[float, float]:
        """Extract low and high price from text like '$780-1,500' or European '4.000-6.000'.

        Handles both US format (comma = thousands, dot = decimal) and
        European format (dot = thousands, comma = decimal).
        """
        segments = re.findall(r'[\d.,]+', text)
        cleaned: list[float] = []
        for seg in segments:
            if not any(c.isdigit() for c in seg):
                continue
            has_comma = ',' in seg
            has_dot = '.' in seg
            try:
                if has_comma and has_dot:
                    # Whichever separator appears last is the decimal separator
                    if seg.rfind('.') > seg.rfind(','):
                        val = float(seg.replace(',', ''))            # US: 1,000.50
                    else:
                        val = float(seg.replace('.', '').replace(',', '.'))  # EU: 1.000,50
                elif has_comma:
                    after = seg.rsplit(',', 1)[1]
                    if len(after) == 3:
                        val = float(seg.replace(',', ''))            # US thousands: 1,500
                    else:
                        val = float(seg.replace(',', '.'))          # EU decimal: 1,50
                elif has_dot:
                    after = seg.rsplit('.', 1)[1]
                    if len(after) == 3:
                        val = float(seg.replace('.', ''))            # EU thousands: 4.000
                    else:
                        val = float(seg)                            # standard decimal: 4.50
                else:
                    val = float(seg)
            except ValueError:
                continue
            cleaned.append(val)
        if len(cleaned) >= 2:
            return cleaned[0], cleaned[1]
        elif len(cleaned) == 1:
            return cleaned[0], cleaned[0]
        return 0.0, 0.0

    # -----------------------------------------------------------------------
    # Delivery-country selector — click "Ship to" header and pick Sweden
    # -----------------------------------------------------------------------

    async def set_delivery_country(self, page: Page, country: str = "Sweden") -> bool:
        """Click the 'Ship to' header dropdown and select *country*.

        Returns True if the country was changed (or already set), False on failure.
        """
        try:
            # --- Step 1: Hover over .tnh-ship-to to open the popup ---
            ship_to = page.locator('.tnh-ship-to').first
            if not await ship_to.is_visible(timeout=3000):
                logger.warning("Ship-to element not visible on page")
                return False

            await ship_to.hover()
            await page.wait_for_timeout(1500)

            # --- Step 2: Check if popup appeared and find the react-select input ---
            select_input = page.locator('#react-select-2-input').first
            if not await select_input.is_visible(timeout=3000):
                # Try a broader selector for react-select input
                select_input = page.locator('.ship-to-country input[role="combobox"]').first
                if not await select_input.is_visible(timeout=2000):
                    logger.warning("Country select input not found in Ship-to popup")
                    return False

            # --- Step 3: Click the select control and type the country name ---
            await select_input.click()
            await page.wait_for_timeout(300)
            await select_input.fill("")
            await select_input.type(country, delay=60)
            await page.wait_for_timeout(1000)

            # --- Step 4: Select the matching option from the dropdown ---
            option = page.locator(f'.crated-header-ship-to-country-item:has-text("{country}")').first
            if not await option.is_visible(timeout=3000):
                # Fallback to broader selectors
                option = page.locator(f'[class*="menu"] div:has-text("{country}")').last
                if not await option.is_visible(timeout=2000):
                    logger.warning("Country option '%s' not found in dropdown", country)
                    return False
            await option.click()
            await page.wait_for_timeout(500)

            # --- Step 5: Click Save ---
            save_btn = page.locator('button[data-role="save"]').first
            if await save_btn.is_visible(timeout=2000):
                await save_btn.click()
                await page.wait_for_timeout(3000)
                logger.info("Delivery country set to %s — page reloading", country)
                return True
            else:
                logger.warning("Save button not found in Ship-to popup")
                return False

        except Exception as e:
            logger.warning("set_delivery_country failed: %s", e)
            return False

    # -----------------------------------------------------------------------
    # Listing page scraper
    # -----------------------------------------------------------------------

    async def scrape_single_listing_page(
        self, page: Page, url: str, page_num: int = 1, captcha_solver=None
    ) -> tuple[list[dict], str | None]:
        """Scrape one listing page. Returns (products, next_page_url_or_None)."""
        logger.info("Listing page %d – loading %s", page_num, url[:80])

        # Set up network interceptor to capture NC captcha image URLs/bytes before load
        intercepted_nc_urls: list[str] = []
        intercepted_nc_bytes: list[bytes] = []

        async def _capture_nc_images(response):
            rurl = response.url
            content_type = response.headers.get("content-type", "")
            is_image = content_type.startswith("image/") or any(
                f".{ext}" in rurl.split("?")[0].lower()
                for ext in ("jpg", "jpeg", "png", "webp")
            )
            if not is_image:
                return
            # Alibaba NC captcha puzzle images — URL may not contain obvious keywords
            # Match known NC patterns AND generic alicdn.com paths loaded during captcha phase
            is_nc_related = any(x in rurl for x in (
                "nocaptcha", "nc_", "punish", "captcha", "baxia", "aliyundun",
                "slide", "puzzle", "verify",
            ))
            if not is_nc_related:
                return
            # Try to store the actual bytes so we don't need to re-download later
            try:
                body = await response.body()
                if body and 500 < len(body) < 2_000_000:
                    intercepted_nc_bytes.append(body)
                    logger.debug("Captured NC image bytes (%d B): %s", len(body), rurl[:80])
                    return
            except Exception:
                pass
            intercepted_nc_urls.append(rurl)
            logger.debug("Intercepted NC image URL: %s", rurl[:80])

        page.on("response", _capture_nc_images)

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Solve CAPTCHA on listing page if present
        if captcha_solver:
            from ..captcha.solver import detect_captcha
            if await detect_captcha(page):
                logger.info(
                    "Listing page %d: CAPTCHA detected — solving (intercepted %d NC images, %d bytes)",
                    page_num, len(intercepted_nc_urls), len(intercepted_nc_bytes),
                )
                if intercepted_nc_bytes:
                    captcha_solver._last_intercepted_nc_bytes = intercepted_nc_bytes
                elif intercepted_nc_urls:
                    captcha_solver._last_intercepted_nc_urls = intercepted_nc_urls
                solved = await captcha_solver.solve_slider(page)
                if solved:
                    logger.info("Listing page %d: CAPTCHA solved", page_num)
                    await page.wait_for_timeout(2000)
                else:
                    logger.warning("Listing page %d: CAPTCHA not solved — page may be blocked", page_num)
                    # Save debug files and signal failure by raising
                    try:
                        await page.screenshot(path=f"{settings.OUTPUT_DIR}/debug_captcha_p{page_num}.png", full_page=True)
                    except Exception:
                        pass
                    raise RuntimeError(f"CAPTCHA unsolved on listing page {page_num}")

        # Wait for product cards to render (React hydration)
        try:
            await page.wait_for_selector(
                '.icbu-product-card, .product-item',
                state='attached',
                timeout=20000,
            )
            logger.info("  Product cards detected on page %d", page_num)
        except Exception:
            logger.warning(
                "  Product cards not found after 20s on page %d — dumping debug files",
                page_num,
            )
            try:
                await page.screenshot(path=f"{settings.OUTPUT_DIR}/debug_page{page_num}.png", full_page=True)
                html = await page.content()
                with open(f"{settings.OUTPUT_DIR}/debug_page{page_num}.html", "w", encoding="utf-8") as f:
                    f.write(html)
                logger.info("  Debug screenshot and HTML saved to output/")
            except Exception as dbg_err:
                logger.warning("  Could not save debug files: %s", dbg_err)

        await page.wait_for_timeout(2000)

        # Set delivery country to Sweden on each fresh page
        await self.set_delivery_country(page, "Sweden")

        # Scroll to trigger lazy-load
        for _ in range(10):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(1000)

        # Extract product cards via JS
        products = await page.evaluate("""() => {
                const products = [];
                const cards = document.querySelectorAll('.icbu-product-card, .product-item .icbu-product-card');

                cards.forEach(card => {
                    const data = {};

                    // --- Link & title ---
                    const titleLink = card.querySelector('a.title-link');
                    if (titleLink) {
                        let href = titleLink.getAttribute('href') || '';
                        if (href.startsWith('//')) href = 'https:' + href;
                        data.url = href;
                        data.title = titleLink.getAttribute('title') || titleLink.innerText.trim();
                    } else {
                        const anyLink = card.querySelector('a[href*="/product-detail/"]');
                        if (anyLink) {
                            let href = anyLink.getAttribute('href') || '';
                            if (href.startsWith('//')) href = 'https:' + href;
                            data.url = href;
                            data.title = anyLink.getAttribute('title') || anyLink.innerText.trim();
                        }
                    }
                    if (!data.url) return;

                    // --- Price ---
                    const priceEl = card.querySelector('.price');
                    if (priceEl) {
                        data.priceTitle = priceEl.getAttribute('title') || '';
                        const numEl = priceEl.querySelector('.num');
                        data.priceText = numEl ? numEl.innerText.trim() : priceEl.innerText.trim();
                    }

                    // --- Image ---
                    const img = card.querySelector('a.product-image img');
                    if (img) {
                        data.image = img.getAttribute('src') || img.getAttribute('data-src') || '';
                        if (data.image.startsWith('//')) data.image = 'https:' + data.image;
                    }
                    if (!data.image) {
                        const anyImg = card.querySelector('img');
                        if (anyImg) {
                            data.image = anyImg.getAttribute('src') || '';
                            if (data.image && data.image.startsWith('//')) data.image = 'https:' + data.image;
                        }
                    }

                    // --- Min order ---
                    const moq = card.querySelector('.moq, [class*="min-order"], [class*="minOrder"]');
                    if (moq) {
                        data.minOrder = moq.innerText.trim();
                    }

                    // --- Product ID ---
                    data.productId = card.getAttribute('data-id') || '';

                    products.push(data);
                });

                // Deduplicate by URL
                const seen = new Set();
                return products.filter(p => {
                    if (seen.has(p.url)) return false;
                    seen.add(p.url);
                    return true;
                });
            }""")

        logger.info("  Found %d products on page %d", len(products), page_num)

        # --- Resolve next page URL without clicking (to avoid navigation in this context) ---
        next_url: str | None = None
        next_num = str(page_num + 1)
        for sel in [f'a:text-is("{next_num}")', f'button:text-is("{next_num}")', 'a.next', '[class*="next"]']:
            btn = page.locator(sel).first
            try:
                if await btn.count() > 0 and await btn.is_visible():
                    href = await btn.get_attribute("href")
                    if href and href not in ("javascript:;", "#", ""):
                        if href.startswith("//"):
                            href = "https:" + href
                        elif not href.startswith("http"):
                            href = settings.BASE_URL + href
                        next_url = href
                        break
                    # No href — construct page URL from current URL pattern
                    current = page.url
                    if f"page={page_num}" in current:
                        next_url = current.replace(f"page={page_num}", f"page={page_num + 1}")
                    elif "?" in current:
                        next_url = current + f"&page={page_num + 1}"
                    else:
                        next_url = current + f"?page={page_num + 1}"
                    break
            except Exception:
                pass

        return products, next_url

    async def scrape_all_listing_pages(self, page: Page, max_pages: int) -> list[dict]:
        """Legacy single-context scrape — kept for backward compatibility."""
        unlimited = max_pages == -1
        page_limit = 10_000 if unlimited else max_pages
        all_products: list[dict] = []
        url = settings.PRODUCT_LIST_URL
        for page_num in range(1, page_limit + 1):
            prods, next_url = await self.scrape_single_listing_page(page, url, page_num)
            all_products.extend(prods)
            await self.random_delay()
            if not next_url:
                logger.info("No more pages after page %d.", page_num)
                break
            url = next_url
        seen: set[str] = set()
        unique = [p for p in all_products if p.get("url") and not seen.add(p["url"]) and p["url"] not in seen]  # type: ignore[func-returns-value]
        logger.info("Total unique products from all listing pages: %d", len(unique))
        return unique

    # -----------------------------------------------------------------------
    # Detail page scraper — extracts from window.detailData JS object
    # -----------------------------------------------------------------------

    async def scrape_detail_page(self, page: Page, url: str, captcha_solver=None) -> dict | None:
        """Navigate to a product detail page, solve CAPTCHA if needed, extract data."""
        logger.info("Detail page – loading %s", url[:80])
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)
        except Exception as e:
            logger.warning("Failed to load detail page %s: %s", url[:60], e)
            return None

        # --- Solve CAPTCHA if present ---
        if captcha_solver:
            for attempt in range(settings.MAX_CAPTCHA_RETRIES):
                solved = await captcha_solver.solve_slider(page)
                if solved:
                    break
                logger.warning("CAPTCHA solve attempt %d failed for %s", attempt + 1, url[:60])
                if attempt < settings.MAX_CAPTCHA_RETRIES - 1:
                    await page.reload(wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
            else:
                logger.error("All CAPTCHA attempts failed for %s — skipping", url[:60])
                return None

        if await detect_captcha(page):
            logger.warning("CAPTCHA still present after solving for %s — triggering proxy retry", url[:60])
            return None

        # Wait for product content — prefer window.detailData (React SPA),
        # fall back to common DOM selectors
        content_ready = False
        try:
            await page.wait_for_function(
                "() => !!(window.detailData && window.detailData.globalData)",
                timeout=20000,
            )
            content_ready = True
        except Exception:
            # Fallback: wait for any visible product heading
            try:
                await page.wait_for_selector(
                    "h1, .product-title, .title, .detail-header-title, "
                    "[data-spm='title'], .product-info, .module-pdp-title",
                    timeout=10000,
                )
                content_ready = True
            except Exception:
                pass

        if not content_ready:
            logger.warning("Product content did not appear for %s", url[:60])
            try:
                import os
                os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
                slug = url.rstrip("/").split("/")[-1][:40]
                await page.screenshot(path=f"{settings.OUTPUT_DIR}/debug_{slug}.png")
            except Exception:
                pass
            return None

        # Scroll to trigger lazy-load
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(800)

        # --- Extract structured data from window.detailData ---
        detail = await page.evaluate("""() => {
            const dd = window.detailData;
            if (!dd || !dd.globalData) return null;

            const gd = dd.globalData;
            const product = gd.product || {};
            const seller = gd.seller || {};
            const trade = gd.trade || {};
            const nm = dd.nodeMap || {};
            const result = {};

            // --- Title ---
            result.title = product.subject || '';
            if (!result.title) {
                const h1 = document.querySelector('h1');
                if (h1) result.title = h1.innerText.trim();
            }

            // --- Product ID ---
            result.productId = product.productId || '';

            // --- Images (full resolution from mediaItems) ---
            const images = [];
            (product.mediaItems || []).forEach(m => {
                if (m.type === 'image' && m.imageUrl) {
                    const url = typeof m.imageUrl === 'object' ? m.imageUrl.big : m.imageUrl;
                    if (url) images.push(url);
                }
            });
            result.images = images;

            // --- Price tiers ---
            const price = product.price || product.customPrice || {};
            result.priceTiers = (price.productLadderPrices || []).map(t => ({
                min: t.min,
                max: t.max,
                price: t.price || t.dollarPrice,
                formatted: t.formatPrice || ''
            }));
            result.priceRange = price.formatLadderPrice || '';

            // --- SKU attributes & variants ---
            const sku = product.sku || {};
            const skuAttrs = sku.skuAttrs || sku.skuSummaryAttrs || [];
            result.skuAttributes = skuAttrs.map(a => ({
                name: a.name || '',
                values: (a.values || []).map(v => ({
                    id: v.id,
                    name: v.name || '',
                    color: v.color || '',
                    imageUrl: v.imageUrl || ''
                }))
            }));

            // SKU combos (attribute IDs -> SKU ID)
            result.skuInfoMap = sku.skuInfoMap || {};

            // --- Product attributes (specs) ---
            const attrs = {};
            const basicProps = product.productBasicProperties || [];
            const otherProps = product.productOtherProperties || [];
            const allProps = [...basicProps, ...otherProps];
            const seen = new Set();
            allProps.forEach(p => {
                const k = (p.attrName || '').trim();
                const v = (p.attrValue || '').trim();
                if (k && v && !seen.has(k)) {
                    seen.add(k);
                    attrs[k] = v;
                }
            });

            // Also include sorted attributes from nodeMap
            const sortedAttr = nm.module_sorted_attribute;
            if (sortedAttr && sortedAttr.privateData) {
                const sorted = sortedAttr.privateData.productSortedProperties || [];
                sorted.forEach(group => {
                    (group.attributeList || []).forEach(a => {
                        const k = (a.attribute || '').trim();
                        const v = (a.value || '').trim();
                        if (k && v && !seen.has(k)) {
                            seen.add(k);
                            attrs[k] = v;
                        }
                    });
                });
            }
            result.attributes = attrs;

            // --- Key industry properties (highlighted specs) ---
            result.keyProperties = (product.productKeyIndustryProperties || []).map(
                p => ({ name: p.attrName, value: p.attrValue })
            );

            // --- Supplier / Company info ---
            result.supplier = {
                name: seller.companyName || '',
                country: seller.companyRegisterCountry || '',
                businessType: seller.companyBusinessType || '',
                yearsOnAlibaba: seller.companyJoinYears || 0,
                contactName: seller.contactName || '',
                responseTime: seller.responseTimeText || '',
                onTimeDelivery: seller.supplierOnTimeDeliveryRate || '',
                logo: seller.companyLogoFileUrlSmall || '',
                profileUrl: seller.companyProfileUrl || '',
            };

            // Mini company card extra data
            const mcc = nm.module_mini_company_card;
            if (mcc && mcc.privateData) {
                const pd = mcc.privateData;
                result.supplier.rating = pd.storeRatingScore || '';
                result.supplier.reviewCount = pd.storeReviewText || '';
                result.supplier.reorderRate = pd.reorderRateValue || '';
                result.supplier.countryName = pd.countryName || '';
                result.supplier.countryFlag = pd.countryFlagImg || '';
            }

            // --- Shipping / origin info ---
            const shipFrom = trade.shipFromInfo || {};
            result.shipFrom = shipFrom.shipFromCountryText || '';

            // --- Trade info ---
            const tradeInfo = trade.tradeInfo || {};
            result.moq = product.moq || 1;
            result.quantityUnit = tradeInfo.quantityUnitStr || '';

            // --- Packaging info from sorted attributes ---
            if (sortedAttr && sortedAttr.privateData) {
                const sorted = sortedAttr.privateData.productSortedProperties || [];
                sorted.forEach(group => {
                    if (group.title && group.title.toLowerCase().includes('packag')) {
                        const pkg = {};
                        (group.attributeList || []).forEach(a => {
                            pkg[a.attribute] = a.value;
                        });
                        result.packaging = pkg;
                    }
                });
            }

            // --- Logistics info ---
            const logistic = trade.logisticInfo || {};
            if (logistic.unitWeight) result.unitWeight = logistic.unitWeight;
            if (logistic.unitVolume) result.unitVolume = logistic.unitVolume;
            if (logistic.unitSize) result.unitSize = logistic.unitSize;

            return result;
        }""")

        # If detailData extraction failed, fall back to basic DOM extraction
        if not detail:
            logger.warning("detailData not found for %s, falling back to DOM", url[:60])
            detail = await self._extract_from_dom(page)

        if detail:
            # --- Extract description from iframe ---
            desc_text = await self._extract_description(page)
            if desc_text:
                detail["description"] = desc_text

            detail["source_url"] = url
            logger.info(
                "  Detail extracted: %s | %d images | %d attrs | %d variants | desc=%d chars",
                (detail.get("title") or "?")[:50],
                len(detail.get("images", [])),
                len(detail.get("attributes", {})),
                len(detail.get("skuAttributes", [])),
                len(detail.get("description", "")),
            )
        return detail

    async def _extract_description(self, page: Page) -> str:
        """Extract product description from the lazy-loaded iframe."""
        try:
            # Scroll to the bottom to trigger lazy-load of description iframe
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

            # Log available frames for debugging
            frame_urls = [f.url for f in page.frames if f.url and f.url != "about:blank"]
            logger.debug("Available frames (%d): %s", len(frame_urls), [u[:80] for u in frame_urls])

            # Try waiting for the iframe to appear
            desc_frame = None
            for _ in range(3):
                for frame in page.frames:
                    if "descIframe" in frame.url or "desc" in frame.url.lower():
                        desc_frame = frame
                        break
                if desc_frame:
                    break
                # Scroll more to trigger lazy load
                await page.evaluate("window.scrollBy(0, 500)")
                await page.wait_for_timeout(1500)

            if desc_frame:
                await desc_frame.wait_for_load_state("domcontentloaded", timeout=10000)
                await page.wait_for_timeout(1000)
                text = await desc_frame.evaluate("""() => {
                    return document.body ? document.body.innerText.trim().substring(0, 5000) : '';
                }""")
                if text and len(text) > 20:
                    logger.debug("Description from iframe: %d chars", len(text))
                    return text
            else:
                logger.debug("No description iframe found")

            # Fallback: try extracting via productId-based API
            product_id = await page.evaluate("""() => {
                const dd = window.detailData;
                return dd && dd.globalData && dd.globalData.product
                    ? dd.globalData.product.productId || '' : '';
            }""")
            if product_id:
                desc_url = f"https://www.alibaba.com/product-detail/description/descIframe.html?productId={product_id}"
                try:
                    resp = await page.context.request.get(desc_url, timeout=10000)
                    if resp.ok:
                        html = await resp.text()
                        text = await page.evaluate("""(html) => {
                            const div = document.createElement('div');
                            div.innerHTML = html;
                            return div.innerText.trim().substring(0, 5000);
                        }""", html)
                        if text and len(text) > 20:
                            logger.debug("Description from API fetch: %d chars", len(text))
                            return text
                except Exception as e:
                    logger.debug("Description API fetch failed: %s", e)

            # Fallback: try extracting description from detailData nodeMap
            text = await page.evaluate("""() => {
                const dd = window.detailData;
                if (dd && dd.nodeMap) {
                    const descMod = dd.nodeMap.module_description || dd.nodeMap.module_product_description;
                    if (descMod && descMod.privateData) {
                        const html = descMod.privateData.descriptionContent || descMod.privateData.content || '';
                        if (html) {
                            const div = document.createElement('div');
                            div.innerHTML = html;
                            return div.innerText.trim().substring(0, 5000);
                        }
                    }
                }
                return '';
            }""")
            if text and len(text) > 20:
                logger.debug("Description from nodeMap: %d chars", len(text))
                return text

            # Fallback: try detailModule on main page
            text = await page.evaluate("""() => {
                const el = document.querySelector('.detailModule, [class*="detail-decorate"], [class*="product-description"]');
                return el ? el.innerText.trim().substring(0, 5000) : '';
            }""")
            if text and len(text) > 20:
                logger.debug("Description from DOM: %d chars", len(text))
                return text
        except Exception as e:
            logger.debug("Description extraction failed: %s", e)
        return ""

    async def _extract_from_dom(self, page: Page) -> dict | None:
        """Fallback DOM-based extraction if window.detailData is not available."""
        return await page.evaluate("""() => {
            const result = {};
            const h1 = document.querySelector('h1');
            if (h1) result.title = h1.innerText.trim();

            const images = [];
            const seen = new Set();
            document.querySelectorAll(
                '.main-image img, .detail-gallery img, [class*="gallery"] img, ' +
                '.magic-gallery img, .img-sequence img, .thumb-list img'
            ).forEach(img => {
                let src = img.getAttribute('src') || img.getAttribute('data-src') || '';
                if (src.startsWith('//')) src = 'https:' + src;
                src = src.replace(/_\\d+x\\d+\\.\\w+(\\?.*)?$/, '');
                if (src && src.startsWith('http') && !seen.has(src)) {
                    seen.add(src);
                    images.push(src);
                }
            });
            result.images = images;
            result.attributes = {};
            result.skuAttributes = [];
            result.priceTiers = [];
            result.supplier = {};
            return result;
        }""")

    # -----------------------------------------------------------------------
    # Build final product documents — matches target schema
    # -----------------------------------------------------------------------

    @staticmethod
    def build_product_doc(raw: dict, detail: dict | None = None) -> dict | None:
        """Convert raw listing card + detail data into the target document schema.

        Returns None if the product has no meaningful detail data and
        STORE_FAILED_PRODUCTS is disabled.
        """
        from ..config.settings import settings

        has_detail = detail and detail.get("images")

        # Skip products where detail extraction failed completely
        if not has_detail and not settings.STORE_FAILED_PRODUCTS:
            return None

        # If no detail data, return a minimal "failed" document
        if not has_detail:
            return {
                "source_url": raw.get("url", ""),
                "isPosted": False,
                "category": "Fitness & Sports",
                "subcategory": "Outdoor Activities",
                "postAdData": {
                    "title": raw.get("title", ""),
                    "description": "",
                    "country": "Sweden",
                    "state": "Skåne",
                    "city": "Malmö",
                    "address": "",
                    "images": [],
                    "productType": "",
                    "variants": [],
                    "additionalFields": {},
                    "scrapeFailed": True,
                },
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

        title = raw.get("title", "")
        price_text = raw.get("priceText", "") or raw.get("priceTitle", "")
        price_low, price_high = AlibabaScraper.extract_price_range(price_text)

        # Prefer priceTiers from detail page — they are more accurate than listing DOM text.
        # Use the tier with the lowest minimum quantity (i.e. the single-unit price).
        if detail:
            tiers = detail.get("priceTiers") or []
            if tiers:
                # Sort by min quantity ascending; pick the first tier's price
                sorted_tiers = sorted(tiers, key=lambda t: t.get("min", 0))
                tier_price = sorted_tiers[0].get("price", 0)
                if tier_price and tier_price > 0:
                    price_low = tier_price
                    # High price = largest-quantity (bulk) tier
                    price_high = sorted_tiers[-1].get("price", tier_price) or price_low

        # Use detail title if longer/better
        if detail.get("title") and len(detail["title"]) > len(title):
            title = detail["title"]

        # --- Images: store both Cloudflare-uploaded URL and original source ---
        images = []
        for img_url in detail.get("images", []):
            images.append({
                "url": img_url,           # will be replaced by Cloudflare URL after upload
                "source_url": img_url,    # original Alibaba image link
            })

        # --- Description ---
        description = detail.get("description", "") or title

        # --- Product type from title/category ---
        title_lower = title.lower()
        attrs = detail.get("attributes", {})

        if "motorcycle" in title_lower or "motorbike" in title_lower:
            product_type = "Electric Motorcycle"
        elif "bicycle" in title_lower or "bike" in title_lower or "ebike" in title_lower:
            product_type = "Electric Bicycle"
        elif "scooter" in title_lower:
            product_type = "Electric Scooter"
        else:
            product_type = "Electric Vehicle"

        # --- Variants ---
        variants = []
        if detail.get("skuAttributes"):
            sku_attrs = detail["skuAttributes"]
            sku_info_map = detail.get("skuInfoMap", {})

            if len(sku_info_map) <= 1:
                variant_attrs = {}
                for attr in sku_attrs:
                    attr_name = attr.get("name", "")
                    values = attr.get("values", [])
                    if values:
                        variant_attrs[attr_name] = values[0].get("name", "")

                variant_name = " / ".join(
                    v.get("name", "")
                    for a in sku_attrs
                    for v in a.get("values", [])[:1]
                    if v.get("name")
                )
                if variant_name:
                    variants.append({
                        "name": variant_name,
                        "price": price_low,
                        "attributes": variant_attrs,
                        "images": [img["url"] for img in images],
                    })
            else:
                attr_id_to_name = {}
                value_id_to_info = {}
                for attr in sku_attrs:
                    attr_name = attr.get("name", "")
                    for val in attr.get("values", []):
                        vid = val.get("id")
                        if vid is not None:
                            attr_id_to_name[str(vid)] = attr_name
                            value_id_to_info[str(vid)] = {
                                "name": val.get("name", ""),
                                "imageUrl": val.get("imageUrl", ""),
                            }

                for combo_key, sku_data in sku_info_map.items():
                    parts = [p for p in combo_key.split(";") if ":" in p]
                    variant_attrs = {}
                    variant_images = []
                    name_parts = []

                    for part in parts:
                        attr_id, val_id = part.split(":", 1)
                        val_info = value_id_to_info.get(val_id, {})
                        attr_name = attr_id_to_name.get(val_id, f"attr_{attr_id}")

                        for attr in sku_attrs:
                            for v in attr.get("values", []):
                                if str(v.get("id")) == val_id:
                                    attr_name = attr.get("name", attr_name)
                                    break

                        val_name = val_info.get("name", val_id)
                        variant_attrs[attr_name] = val_name
                        name_parts.append(val_name)

                        img_url = val_info.get("imageUrl", "")
                        if img_url:
                            variant_images.append(img_url)

                    variants.append({
                        "name": " / ".join(name_parts) if name_parts else f"SKU {sku_data.get('id', '')}",
                        "price": price_low,
                        "attributes": variant_attrs,
                        "images": variant_images if variant_images else [img["url"] for img in images],
                    })

        if not variants and price_low > 0:
            base_name = title.split(",")[0].strip() if "," in title else title
            variants.append({
                "name": base_name,
                "price": price_low,
                "attributes": {},
                "images": [img["url"] for img in images],
            })

        # --- Additional fields ---
        additional = {}

        brand = attrs.get("Brand", "") or attrs.get("brand", "")
        if brand:
            additional["brand"] = brand

        color_val = ""
        for a in detail.get("skuAttributes", []):
            if a.get("name", "").lower() == "color":
                vals = a.get("values", [])
                if vals:
                    color_val = vals[0].get("name", "")
        if color_val:
            additional["color"] = color_val

        attr_mapping = {
            "Voltage": "voltage",
            "Power": "power",
            "Watt-Hour": "wattHour",
            "Max Speed": "maxSpeed",
            "Range Per Charge": "rangePerCharge",
            "Battery Capacity": "batteryCapacity",
            "Battery Type": "batteryType",
            "Charging Time": "chargingTime",
            "Foldable": "foldable",
            "Waterproof": "waterproof",
            "max load": "maxLoad",
            "Material": "material",
            "Suspension Type": "suspensionType",
            "Braking System": "brakingSystem",
            "motor": "motorType",
            "Plug Type": "plugType",
            "Smart Type": "smartType",
            "Control Method": "controlMethod",
            "Category": "category",
            "Applicable To The Crowd": "applicableTo",
            "Model Number": "modelNumber",
            "Tire Size": "tireSize",
            "Climbing degree": "climbingDegree",
            "Size": "size",
        }
        for attr_name, field_name in attr_mapping.items():
            val = attrs.get(attr_name, "")
            if val and field_name not in additional:
                additional[field_name] = val

        mapped_names = set(attr_mapping.keys()) | {"Brand", "brand", "Place of Origin"}
        for k, v in attrs.items():
            if k not in mapped_names and v:
                key = k[0].lower() + k[1:].replace(" ", "")
                additional[key] = v

        if detail.get("priceTiers"):
            additional["priceTiers"] = detail["priceTiers"]
        if detail.get("packaging"):
            additional["packaging"] = detail["packaging"]
        moq = detail.get("moq", 0)
        if moq:
            additional["moq"] = moq

        return {
            "source_url": raw.get("url", ""),
            "isPosted": False,
            "category": "Fitness & Sports",
            "subcategory": "Outdoor Activities",
            "postAdData": {
                "title": title,
                "description": description,
                "country": "Sweden",
                "state": "Skåne",
                "city": "Malmö",
                "address": "",
                "images": images,
                "productType": product_type,
                "variants": variants,
                "additionalFields": additional,
            },
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
