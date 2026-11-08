import argparse
import asyncio
import logging

from app.db.mongo import create_mongo_client, get_database
from app.db.repository import ensure_indexes
from app.scraping.collector import collect_all
from app.scraping.sources import SOURCES_BY_ID


async def main() -> None:
    parser = argparse.ArgumentParser(description="Collecter les articles des sites configurés.")
    parser.add_argument(
        "--source",
        choices=("all", *SOURCES_BY_ID),
        default="all",
        help="Collecter un seul site, ou tous les sites (défaut : all).",
    )
    parser.add_argument(
        "--exclude-source",
        choices=tuple(SOURCES_BY_ID),
        action="append",
        default=[],
        help="Exclure une source lorsque --source all est utilisé (option répétable).",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Plafond par site pour un passage de reprise (défaut : toutes les URL en attente).",
    )
    parser.add_argument(
        "--max-total-articles-per-source",
        type=int,
        default=2000,
        help="Plafond cumulatif d'articles enregistrés par source (défaut : 2000).",
    )
    parser.add_argument(
        "--max-sitemaps",
        type=int,
        default=None,
        help="Plafond de sitemaps par site pour un passage de reprise (défaut : tous les sitemaps en attente).",
    )
    parser.add_argument(
        "--sitemaps-only",
        action="store_true",
        help="Découvrir les sitemaps et mettre leurs URL en file sans télécharger les articles.",
    )
    parser.add_argument(
        "--until-sitemaps-done",
        action="store_true",
        help="Répéter les lots jusqu'à ce qu'il n'y ait plus de sitemap en attente; exige --sitemaps-only.",
    )
    args = parser.parse_args()
    if args.max_articles is not None and args.max_articles < 1:
        parser.error("--max-articles doit être supérieur ou égal à 1")
    if args.max_total_articles_per_source < 1:
        parser.error("--max-total-articles-per-source doit être supérieur ou égal à 1")
    if args.max_sitemaps is not None and args.max_sitemaps < 1:
        parser.error("--max-sitemaps doit être supérieur ou égal à 1")
    if args.until_sitemaps_done and not args.sitemaps_only:
        parser.error("--until-sitemaps-done exige --sitemaps-only")
    if args.exclude_source and args.source != "all":
        parser.error("--exclude-source s'utilise uniquement avec --source all")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = create_mongo_client()
    try:
        database = get_database(client)
        ensure_indexes(database)
        excluded_sources = set(args.exclude_source)
        selected_sources = (
            tuple(
                source for source in SOURCES_BY_ID.values()
                if source.source_id not in excluded_sources
            )
            if args.source == "all"
            else (SOURCES_BY_ID[args.source],)
        )
        if not selected_sources:
            parser.error("La sélection ne contient aucune source à collecter")
        cycle = 0
        while True:
            cycle += 1
            summary = await collect_all(
                database, selected_sources, args.max_articles,
                args.max_sitemaps, args.sitemaps_only,
                max_total_articles=args.max_total_articles_per_source,
            )
            print(f"Passage {cycle}:")
            for source_id, counts in summary.items():
                print(f"{source_id}: {counts}")
            if not args.until_sitemaps_done:
                break
            selected_ids = [source.source_id for source in selected_sources]
            pending = database.sitemap_queue.count_documents({
                "source_id": {"$in": selected_ids}, "status": "pending"
            })
            print(f"Sitemaps encore en attente : {pending}")
            if pending == 0:
                break
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
