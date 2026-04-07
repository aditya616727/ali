#!/usr/bin/env python3
"""Run ali-scraper without installing the package."""

import sys
from pathlib import Path

# Add src to path so ali_scraper can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ali_scraper.cli import main

if __name__ == "__main__":
    main()
