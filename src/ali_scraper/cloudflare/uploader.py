"""Cloudflare Images uploader."""

import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)


class CloudflareUploader:
    def __init__(self, account_id: str, api_token: str, max_concurrent: int = 5):
        self.account_id = account_id
        self.api_token = api_token
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.upload_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/images/v1"
        self.headers = {"Authorization": f"Bearer {api_token}"}

    async def upload_from_url(self, image_url: str) -> str | None:
        """Upload an image by URL to Cloudflare Images. Returns delivery URL or None."""
        async with self.semaphore:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        self.upload_url,
                        headers=self.headers,
                        files={"url": (None, image_url)},
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    if result.get("success"):
                        variants = result["result"]["variants"]
                        public_url = next(
                            (v for v in variants if v.endswith("/public")), variants[0]
                        )
                        return public_url
                    else:
                        logger.warning("CF upload failed for %s: %s", image_url, result.get("errors"))
            except Exception as e:
                logger.warning("CF upload error for %s: %s", image_url, e)
        return None

    async def upload_many(self, image_urls: list[str]) -> list[str]:
        """Upload multiple images, return list of CF delivery URLs (skips failures)."""
        tasks = [self.upload_from_url(url) for url in image_urls]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r]
