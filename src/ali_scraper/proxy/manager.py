"""Fetch proxy list from Webshare API."""

import httpx
import logging
from typing import Sequence

logger = logging.getLogger(__name__)


async def fetch_webshare_proxies(api_key: str, allowed_countries: Sequence[str] | None = None) -> list[dict]:
    """Return list of proxy dicts from Webshare.

    Tries modes in order: backbone (most compatible), then default.
    Uses the rotating endpoint p.webshare.io so each request gets a fresh IP.
    """
    headers = {"Authorization": f"Token {api_key}", "Accept": "application/json"}
    filter_cc = {c.upper() for c in allowed_countries} if allowed_countries else set()

    async with httpx.AsyncClient() as client:
        for mode in ("backbone", ""):
            mode_param = f"mode={mode}&" if mode else ""
            url = f"https://proxy.webshare.io/api/v2/proxy/list/?{mode_param}page=1&page_size=250"
            try:
                resp = await client.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    logger.debug("Webshare mode=%s returned 0 results", mode or "default")
                    continue

                # Build rotating-endpoint proxy list so every request gets a different IP
                # The rotating endpoint p.webshare.io cycles through the pool automatically
                rotating_host = "p.webshare.io"
                rotating_port = 80

                # Try to find the proxy-level username/password from any result
                sample = results[0]
                username = sample.get("username", "")
                password = sample.get("password", "")

                # Use per-IP entries for true per-request rotation when available,
                # but also include the rotating endpoint as a fallback
                proxies: list[dict] = []
                for p in results:
                    if filter_cc and p.get("country_code", "").upper() not in filter_cc:
                        continue
                    host = p.get("proxy_address") or rotating_host
                    proxies.append({
                        "server": f"http://{host}:{p['port']}",
                        "username": p["username"],
                        "password": p["password"],
                        "country_code": p.get("country_code", ""),
                    })

                if not proxies and filter_cc:
                    logger.warning("Webshare mode=%s: no proxies matched country filter %s", mode or "default", filter_cc)
                    continue

                # Append rotating endpoint so we never run out of proxies
                if username and password:
                    proxies.append({
                        "server": f"http://{rotating_host}:{rotating_port}",
                        "username": username,
                        "password": password,
                        "country_code": "rotating",
                    })

                logger.info(
                    "Loaded %d proxies from Webshare (mode=%s%s) + 1 rotating endpoint",
                    len(proxies) - 1,
                    mode or "default",
                    f", filtered to: {', '.join(sorted(filter_cc))}" if filter_cc else "",
                )
                return proxies
            except Exception as e:
                logger.warning("Webshare mode=%s failed: %s", mode or "default", e)

    logger.error("Failed to load any proxies from Webshare")
    return []
