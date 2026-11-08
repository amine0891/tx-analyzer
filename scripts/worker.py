import asyncio
import logging
import os

from app.db.mongo import create_mongo_client, get_database
from app.db.repository import ensure_indexes
from app.scraping.collector import collect_all
from app.scraping.sources import SOURCES

AUTOMATIC_SOURCES = tuple(
    source for source in SOURCES if source.collection_mode == "automatic"
)


async def main() -> None:
    interval_seconds = int(os.getenv("COLLECT_INTERVAL_SECONDS", "86400"))
    if interval_seconds < 60:
        raise ValueError("COLLECT_INTERVAL_SECONDS doit être supérieur ou égal à 60")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = create_mongo_client()
    try:
        database = get_database(client)
        ensure_indexes(database)
        while True:
            try:
                summary = await collect_all(database, AUTOMATIC_SOURCES)
                for source_id, counts in summary.items():
                    logging.info("Cycle terminé pour %s : %s", source_id, counts)
            except Exception:
                logging.exception("Échec du cycle de collecte; reprise au prochain cycle")
            logging.info("Prochaine découverte des publications dans %s secondes", interval_seconds)
            await asyncio.sleep(interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
