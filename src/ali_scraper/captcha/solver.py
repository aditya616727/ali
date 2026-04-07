"""CapSolver integration for Alibaba CAPTCHA solving."""

import asyncio
import base64
import logging
import random
import re

import httpx

logger = logging.getLogger(__name__)

CAPSOLVER_API = "https://api.capsolver.com"

# Selectors that indicate a CAPTCHA wall is present
CAPTCHA_SELECTORS = [
    "#nc_1_wrapper",
    "#baxia-dialog-content",
    'iframe[src*="punish"]',
    "#nocaptcha",
    '[class*="captcha"]',
    "#slider",
    ".nc-container",
    "#baxia-punish",
]

# Selectors for the slider button handle (what the user drags)
SLIDER_SELECTORS = "#nc_1_n1z, .btn_slide, .nc_iconfont, .slider-btn"

# Selectors for the NC puzzle background (image with the hole)
NC_BG_SELECTORS = "#nc_1_bg, .nc_bg, .nc-bg-img, #baxia-dialog-content"

# Selectors for the NC puzzle piece (the small piece to slide in)
NC_PIECE_SELECTORS = "#nc_1_slice, .nc_slice, .nc-slide-piece, #nc_1_jigsaw"


async def detect_captcha(page) -> bool:
    """Return True if a CAPTCHA wall is visible on the page."""
    for sel in CAPTCHA_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                return True
        except Exception:
            pass
    return False


async def _warmup_mouse(page) -> None:
    """Move the mouse around the page in a human-like pattern before the drag."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp["width"], vp["height"]
        # A few gentle wandering moves across the viewport
        points = [
            (random.randint(w // 4, w // 2), random.randint(h // 4, h // 2)),
            (random.randint(w // 3, w * 2 // 3), random.randint(h // 3, h * 2 // 3)),
            (random.randint(w // 5, w // 3), random.randint(h // 5, h // 3)),
        ]
        for px, py in points:
            await page.mouse.move(px, py)
            await asyncio.sleep(random.uniform(0.08, 0.25))
    except Exception:
        pass


async def drag_slider(page, distance: float | None = None) -> bool:
    """Drag the NC slider CAPTCHA handle.

    Args:
        page: Playwright page object.
        distance: Override the horizontal drag distance in pixels. When None,
                  the function measures the track width and drags to the end.

    Returns True if the CAPTCHA disappeared after the drag.
    """
    slider_btn = page.locator(SLIDER_SELECTORS).first
    try:
        await slider_btn.wait_for(state="visible", timeout=8000)
    except Exception:
        logger.warning("Slider handle not visible after 8 s — cannot drag")
        return False

    try:
        box = await slider_btn.bounding_box()
        if not box:
            return False

        if distance:
            drag_px = float(distance)
        else:
            # Try to measure the slider track width to drag to end
            track_selectors = "#nc_1__scale_text, .nc-lang-cnt, .scale_text, .slider-track, .nc_scale"
            track = page.locator(track_selectors).first
            track_box = None
            try:
                if await track.count() > 0:
                    track_box = await track.bounding_box()
            except Exception:
                pass

            if track_box:
                # Drag from current position to near the end of the track
                drag_px = track_box["width"] - (box["x"] - track_box["x"]) - box["width"] / 2 - 5
                drag_px = max(drag_px, 200)
            else:
                drag_px = 340.0

        # Approach the slider slowly from the left before pressing down
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        await page.mouse.move(start_x - 30, start_y + random.uniform(-4, 4))
        await asyncio.sleep(random.uniform(0.15, 0.35))
        await page.mouse.move(start_x, start_y + random.uniform(-2, 2))
        await asyncio.sleep(random.uniform(0.08, 0.18))

        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.12))

        # Humanised ease-in-out with micro-jitter
        steps = random.randint(38, 55)
        for i in range(steps):
            t = (i + 1) / steps
            # Smooth step (ease-in-out cubic)
            eased = t * t * (3 - 2 * t)
            x = start_x + drag_px * eased + random.uniform(-0.6, 0.6)
            # Slight downward arc + vertical jitter
            y = start_y + 3 * (t * (1 - t)) + random.uniform(-0.5, 0.5)
            await page.mouse.move(x, y)
            # Slow at start, fast in middle, slow at end
            delay = 0.025 - 0.018 * (1 - abs(2 * t - 1))
            await asyncio.sleep(max(0.006, delay + random.uniform(-0.003, 0.003)))

        # Brief hold at destination, then release
        await asyncio.sleep(random.uniform(0.08, 0.2))
        await page.mouse.up()
        await page.wait_for_timeout(random.randint(2200, 3200))

        if not await detect_captcha(page):
            logger.info("Slider CAPTCHA solved (drag distance=%.0f px)", drag_px)
            return True
    except Exception as e:
        logger.warning("Slider drag failed: %s", e)

    return False


class FreeCaptchaSolver:
    """No-API CAPTCHA solver — uses direct drag only."""

    async def solve_slider(self, page) -> bool:
        if not await detect_captcha(page):
            return True  # no CAPTCHA present
        logger.info("CAPTCHA detected — warming up mouse then attempting free drag")
        await _warmup_mouse(page)
        if await drag_slider(page):
            return True
        # Retry with alternate distances
        for alt_dist in [280, 340, 260]:
            if not await detect_captcha(page):
                return True
            await _warmup_mouse(page)
            if await drag_slider(page, distance=alt_dist):
                return True
        return False



class CaptchaSolver:
    """Solves Alibaba slide/puzzle CAPTCHAs via CapSolver API."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def _create_task(self, task: dict) -> str | None:
        """Submit a task to CapSolver, return taskId."""
        payload = {"clientKey": self.api_key, "task": task}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{CAPSOLVER_API}/createTask", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("errorId", 0) != 0:
                logger.error("CapSolver createTask error: %s", data.get("errorDescription"))
                return None
            return data.get("taskId")

    async def _get_result(self, task_id: str, timeout: int = 120, poll: int = 3) -> dict | None:
        """Poll for task result."""
        payload = {"clientKey": self.api_key, "taskId": task_id}
        async with httpx.AsyncClient(timeout=30) as client:
            elapsed = 0
            while elapsed < timeout:
                resp = await client.post(f"{CAPSOLVER_API}/getTaskResult", json=payload)
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                if status == "ready":
                    return data.get("solution")
                if data.get("errorId", 0) != 0:
                    logger.error("CapSolver error: %s", data.get("errorDescription"))
                    return None
                await asyncio.sleep(poll)
                elapsed += poll
        logger.error("CapSolver timeout after %ds for task %s", timeout, task_id)
        return None

    async def _vision_engine_distance(self, page) -> float | None:
        """Use CapSolver VisionEngine slider_1 to find accurate drag distance.

        Extracts the background (hole) image and the puzzle piece image from the
        NC CAPTCHA widget, then sends both to VisionEngine. Returns the distance
        in pixels, or None if extraction fails.

        VisionEngine createTask is synchronous — result arrives directly in the
        createTask response (no polling needed).
        """
        try:
            # Try to screenshot individual NC elements for best accuracy
            bg_loc = page.locator(NC_BG_SELECTORS).first
            piece_loc = page.locator(NC_PIECE_SELECTORS).first

            bg_visible = await bg_loc.count() > 0 and await bg_loc.is_visible()
            piece_visible = await piece_loc.count() > 0 and await piece_loc.is_visible()

            if bg_visible and piece_visible:
                bg_bytes = await bg_loc.screenshot()
                piece_bytes = await piece_loc.screenshot()
            else:
                # Fallback: crop from full-page screenshot — send same image for both
                logger.debug("NC element screenshots unavailable; using full screenshot")
                full = await page.screenshot(full_page=False)
                bg_bytes = full
                piece_bytes = full

            b64_bg = base64.b64encode(bg_bytes).decode()
            b64_piece = base64.b64encode(piece_bytes).decode()

            task = {
                "type": "VisionEngine",
                "module": "slider_1",
                "image": b64_piece,          # the puzzle piece
                "imageBackground": b64_bg,   # the background with the hole
                "websiteURL": page.url,
            }

            payload = {"clientKey": self.api_key, "task": task}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{CAPSOLVER_API}/createTask", json=payload)
                resp.raise_for_status()
                data = resp.json()

            if data.get("errorId", 0) != 0:
                logger.error("VisionEngine error: %s", data.get("errorDescription"))
                return None

            solution = data.get("solution") or {}
            distance = solution.get("distance")
            if distance:
                logger.info("VisionEngine returned distance=%.0f px", distance)
                return float(distance)

        except Exception as e:
            logger.error("VisionEngine call failed: %s", e)

        return None

    async def solve_slider(self, page) -> bool:
        """Detect and solve Alibaba's slider CAPTCHA on a Playwright page.

        Returns True if solved (or no CAPTCHA), False if failed.
        """
        if not await detect_captcha(page):
            return True  # No CAPTCHA present

        logger.info("CAPTCHA detected — warming up mouse")
        await _warmup_mouse(page)

        # Strategy 1: free drag — try auto-measured distance, then fallback distances
        if await drag_slider(page):
            return True

        # Retry with different distances in case auto-measure was wrong
        for alt_dist in [280, 340, 260]:
            if not await detect_captcha(page):
                return True
            logger.debug("Retrying free drag at %dpx", alt_dist)
            await _warmup_mouse(page)
            if await drag_slider(page, distance=alt_dist):
                return True

        # Strategy 2: VisionEngine gives accurate pixel distance, then drag
        logger.info("Free drag failed — querying CapSolver VisionEngine for distance")
        distance = await self._vision_engine_distance(page)
        if distance:
            logger.info("Attempting drag with VisionEngine distance=%.0f px", distance)
            if await drag_slider(page, distance=distance):
                return True

        # Strategy 3: fallback to full-screenshot AntiSliderTaskByImage
        logger.info("VisionEngine drag failed — falling back to AntiSliderTaskByImage")
        return await self._solve_by_screenshot(page)

    async def _solve_by_screenshot(self, page) -> bool:
        """Legacy fallback: full screenshot → AntiSliderTaskByImage → drag."""
        try:
            slider_btn = page.locator(SLIDER_SELECTORS).first
            screenshot = await page.screenshot(full_page=False)
            b64_image = base64.b64encode(screenshot).decode()

            task = {
                "type": "AntiSliderTaskByImage",
                "image": b64_image,
            }
            task_id = await self._create_task(task)
            if not task_id:
                return await self._try_recaptcha(page)

            solution = await self._get_result(task_id)
            if not solution:
                return False

            slide_x = solution.get("slideX") or solution.get("distance", 0)
            if slide_x:
                return await drag_slider(page, distance=float(slide_x))

        except Exception as e:
            logger.error("AntiSliderTaskByImage solve failed: %s", e)

        return False

    async def _try_recaptcha(self, page) -> bool:
        """Fallback: check for reCAPTCHA iframe and solve via CapSolver."""
        try:
            if await page.locator('iframe[src*="recaptcha"]').count() == 0:
                return False
        except Exception:
            return False

        logger.info("reCAPTCHA detected, solving via CapSolver")
        site_key_match = None
        for frame in page.frames:
            url = frame.url
            if "recaptcha" in url and "k=" in url:
                m = re.search(r'k=([^&]+)', url)
                if m:
                    site_key_match = m.group(1)
                    break

        if not site_key_match:
            logger.warning("Could not extract reCAPTCHA siteKey")
            return False

        task = {
            "type": "ReCaptchaV2TaskProxyLess",
            "websiteURL": page.url,
            "websiteKey": site_key_match,
        }
        task_id = await self._create_task(task)
        if not task_id:
            return False

        solution = await self._get_result(task_id, timeout=180)
        if not solution:
            return False

        token = solution.get("gRecaptchaResponse", "")
        if token:
            await page.evaluate(
                f'document.getElementById("g-recaptcha-response").value = "{token}";'
            )
            submit = page.locator('button[type="submit"], input[type="submit"]').first
            if await submit.count() > 0:
                await submit.click()
                await page.wait_for_timeout(3000)
            logger.info("reCAPTCHA solved via CapSolver")
            return True

        return False
