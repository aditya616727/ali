"""MongoDB storage layer."""

import logging
import certifi
from pymongo import MongoClient

logger = logging.getLogger(__name__)


class MongoStorage:
    def __init__(self, uri: str, db_name: str = "kukirin_scraper"):
        self.client = MongoClient(
            uri,
            tlsCAFile=certifi.where(),
            tls=True,
            tlsAllowInvalidCertificates=True,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000,
        )
        self.db = self.client[db_name]
        self.collection = self.db["products"]
        # Test connection
        self.client.admin.command("ping")
        logger.info("Connected to MongoDB database: %s", db_name)

    def upsert_product(self, product: dict):
        """Insert or update a product by its source_url."""
        source_url = product.get("source_url", "")
        if source_url:
            self.collection.update_one(
                {"source_url": source_url}, {"$set": product}, upsert=True
            )
            title = product.get("postAdData", {}).get("title", "") or product.get("title", "")
            logger.info("Upserted product: %s", title)
        else:
            self.collection.insert_one(product)
            title = product.get("postAdData", {}).get("title", "") or product.get("title", "")
            logger.info("Inserted product: %s", title)

    def get_existing_urls(self) -> set[str]:
        """Return set of already-scraped source URLs."""
        urls = self.collection.distinct("source_url")
        return set(urls)

    def close(self):
        self.client.close()
