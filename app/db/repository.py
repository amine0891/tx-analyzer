from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from app.schemas import Article, SiteStatistics


def ensure_indexes(database: Database) -> None:
    database.articles.create_index("url", unique=True)
    database.articles.create_index([("source_id", 1), ("collected_at", -1)])
    database.sitemap_queue.create_index("url", unique=True)
    database.sitemap_queue.create_index([("source_id", 1), ("status", 1)])
    database.article_queue.create_index("url", unique=True)
    database.article_queue.create_index([("source_id", 1), ("status", 1), ("url", 1)])
    database.article_queue.create_index(
        [("source_id", 1), ("status", 1), ("section", 1), ("url", -1)]
    )
    database.predictions.create_index([("created_at", -1)])
    # Batch predictions are linked to their source article. Sparse keeps the
    # single-text /predict documents (which have no article_id) unaffected.
    database.predictions.create_index("article_id", unique=True, sparse=True)
    database.site_statistics.create_index(
        [("source_id", 1), ("analyzed_at", -1)]
    )


def save_article(database: Database, article: Article) -> bool:
    document = article.model_dump(mode="python")
    document["url"] = str(article.url)
    result = database.articles.update_one(
        {"url": str(article.url)},
        {"$setOnInsert": document},
        upsert=True,
    )
    return result.upserted_id is not None


def save_prediction(database: Database, document: dict[str, Any]) -> None:
    database.predictions.insert_one({**document, "created_at": datetime.now(timezone.utc)})


def save_site_statistics(database: Database, statistics: SiteStatistics) -> None:
    database.site_statistics.insert_one(statistics.model_dump(mode="python"))
