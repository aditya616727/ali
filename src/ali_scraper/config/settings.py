"""Environment-based settings for ali-scraper."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file)
_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path)


class _Settings:
    # MongoDB
    MONGODB_URI: str = os.getenv("MONGODB_URI", "")

    # Cloudflare Images
    CLOUDFLARE_ACCOUNT_ID: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    CLOUDFLARE_API_TOKEN: str = os.getenv("CLOUDFLARE_API_TOKEN", "")

    # Browser
    HEADLESS: bool = os.getenv("HEADLESS", "false").lower() == "true"
    CHROME_PATH: str | None = os.getenv("CHROME_PATH", "") or None
    CHROME_SANDBOX: bool = os.getenv("CHROME_SANDBOX", "true").lower() == "true"

    # Scraping
    MAX_PAGES: int = int(os.getenv("MAX_PAGES", "5"))  # -1 = scrape all pages
    DELAY_MIN: int = int(os.getenv("DELAY_MIN", "2"))
    DELAY_MAX: int = int(os.getenv("DELAY_MAX", "5"))
    MAX_CONCURRENT_TABS: int = int(os.getenv("MAX_CONCURRENT_TABS", "3"))
    MAX_CONCURRENT_UPLOADS: int = int(os.getenv("MAX_CONCURRENT_UPLOADS", "5"))

    # Output
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")

    # Proxy
    USE_PROXY: bool = os.getenv("USE_PROXY", "true").lower() == "true"
    WEBSHARE_API_KEY: str = os.getenv("WEBSHARE_API_KEY", "")
    # Comma-separated country codes to allow (empty = all countries)
    PROXY_COUNTRIES: list[str] = [c.strip().upper() for c in os.getenv("PROXY_COUNTRIES", "").split(",") if c.strip()]

    # CAPTCHA solving (CapSolver)
    CAPSOLVER_API_KEY: str = os.getenv("CAPSOLVER_API_KEY", "")
    SCRAPE_DETAILS: bool = os.getenv("SCRAPE_DETAILS", "false").lower() == "true"
    DETAIL_DELAY_MIN: int = int(os.getenv("DETAIL_DELAY_MIN", "10"))
    DETAIL_DELAY_MAX: int = int(os.getenv("DETAIL_DELAY_MAX", "20"))
    MAX_CAPTCHA_RETRIES: int = int(os.getenv("MAX_CAPTCHA_RETRIES", "2"))
    DETAIL_WORKERS: int = int(os.getenv("DETAIL_WORKERS", "3"))
    FORCE_RESCRAPE: bool = os.getenv("FORCE_RESCRAPE", "false").lower() == "true"
    STORE_FAILED_PRODUCTS: bool = os.getenv("STORE_FAILED_PRODUCTS", "false").lower() == "true"

    # Target
    BASE_URL: str = "https://kukirin.en.alibaba.com"
    PRODUCT_LIST_URL: str = f"{BASE_URL}/productlist.html"

    # User agents for rotation
    USER_AGENTS: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ]


settings = _Settings()
