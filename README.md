# ali-scraper

Alibaba product scraper for KuKirin store using Playwright with stealth.

## Project Structure

```
ali-scraper/
├── pyproject.toml
├── requirements.txt
├── run.py
├── Dockerfile
├── docker-compose.yml
├── scripts/
│   ├── scrape.py
│   └── debug_dom.py
└── src/
    └── ali_scraper/
        ├── __init__.py
        ├── __main__.py
        ├── cli.py
        ├── config/
        │   ├── __init__.py
        │   └── settings.py
        ├── database/
        │   ├── __init__.py
        │   └── mongodb.py
        ├── proxy/
        │   ├── __init__.py
        │   └── manager.py
        ├── cloudflare/
        │   ├── __init__.py
        │   └── uploader.py
        ├── scrapers/
        │   ├── __init__.py
        │   └── alibaba.py
        └── utils/
            ├── __init__.py
            └── helpers.py
```

## Quick Start

```bash
# Install
pip install -e .
playwright install chromium

# Run
python run.py
# or
ali-scraper
# or
python -m ali_scraper
```

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `MONGODB_URI` | MongoDB connection string | _(empty)_ |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Images account | _(empty)_ |
| `CLOUDFLARE_API_TOKEN` | Cloudflare Images token | _(empty)_ |
| `HEADLESS` | Run browser headless | `false` |
| `MAX_PAGES` | Max listing pages to scrape | `5` |
| `USE_PROXY` | Enable Webshare proxies | `true` |
| `WEBSHARE_API_KEY` | Webshare API key | _(empty)_ |
| `DELAY_MIN` / `DELAY_MAX` | Request delay range (seconds) | `2` / `5` |
| `OUTPUT_DIR` | JSON output directory | `output` |

## Docker

```bash
docker compose up --build
```
