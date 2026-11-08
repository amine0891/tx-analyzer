"""Human-assisted collector for sources whose public pages need browser validation."""

import argparse
import asyncio
import logging
import os
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone

from app.db.mongo import create_mongo_client, get_database
from app.db.repository import ensure_indexes
from app.scraping.collector import _article_section, _enqueue, _is_article_candidate, collect_all
from app.scraping.sources import SOURCES_BY_ID

logger = logging.getLogger(__name__)
MANUAL_SOURCE_IDS = ("marianne", "lepoint", "gamespot")


def _start_process(command: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(command, env=env)
    time.sleep(0.5)
    if process.poll() is not None:
        raise RuntimeError(f"Impossible de démarrer le service d'affichage : {command[0]}")
    return process


def _start_manual_desktop() -> list[subprocess.Popen[bytes]]:
    env = {**os.environ, "DISPLAY": ":99"}
    processes: list[subprocess.Popen[bytes]] = []
    processes.append(_start_process(
        ["Xvfb", ":99", "-screen", "0", "1364x900x24", "-ac", "-nolisten", "tcp"], env
    ))
    time.sleep(1)
    processes.append(_start_process(
        ["x11vnc", "-display", ":99", "-forever", "-shared", "-nopw",
         "-noxdamage", "-rfbport", "5900", "-listen", "0.0.0.0"], env
    ))
    processes.append(_start_process(
        ["websockify", "--web", "/usr/share/novnc", "7900", "127.0.0.1:5900"], env
    ))
    return processes


def _stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


async def _collect(args: argparse.Namespace) -> None:
    client = create_mongo_client()
    try:
        database = get_database(client)
        ensure_indexes(database)
        source = SOURCES_BY_ID[args.source]
        if args.article_url:
            for url in args.article_url:
                if not _is_article_candidate(url, source):
                    raise ValueError(
                        f"URL invalide ou hors domaine/format article pour {source.name}"
                    )
                _enqueue(database, "article_queue", {
                    "url": url,
                    "section": _article_section(url),
                    "source_id": source.source_id,
                    "status": "pending",
                    "attempts": 0,
                    "discovered_at": datetime.now(timezone.utc),
                })
                database.article_queue.update_one(
                    {
                        "url": url,
                        "source_id": source.source_id,
                        "status": {"$in": ["error", "blocked", "skipped"]},
                    },
                    {"$set": {"status": "pending", "attempts": 0},
                     "$unset": {"last_error": ""}},
                )
            # A targeted article probe must not first open a blocked home page
            # or guess a sitemap URL; collect_source still uses the same HTTP,
            # browser fallback, parser, queue, and Mongo persistence path.
            source = replace(source, listing_paths=())
            max_sitemaps = 0
            max_articles = len(args.article_url)
        else:
            max_sitemaps = None if (args.full or args.until_sitemaps_done) else args.max_sitemaps
            max_articles = None if args.full else args.max_articles
        cycle = 0
        while True:
            cycle += 1
            summary = await collect_all(
                database,
                (source,),
                max_articles=max_articles,
                max_sitemaps=max_sitemaps,
                sitemaps_only=args.sitemaps_only,
                manual_browser=True,
                max_total_articles=2000,
            )
            print(f"Passage semi-automatique {cycle} :")
            for source_id, counts in summary.items():
                print(f"{source_id}: {counts}")
            if not args.until_sitemaps_done:
                break
            pending = database.sitemap_queue.count_documents({
                "source_id": source.source_id,
                "status": "pending",
            })
            print(f"Sitemaps encore en attente : {pending}")
            if pending == 0:
                break
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collecte Marianne, Le Point ou GameSpot avec navigateur visible et validation humaine au besoin."
    )
    parser.add_argument("--source", choices=MANUAL_SOURCE_IDS, default="marianne")
    parser.add_argument(
        "--article-url",
        action="append",
        default=[],
        help="Tester directement une URL publique de la source sans découverte par sitemap/rubriques.",
    )
    parser.add_argument("--max-sitemaps", type=int, default=1)
    parser.add_argument("--max-articles", type=int, default=1)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Traiter toutes les URL en attente (à utiliser après validation du petit passage).",
    )
    parser.add_argument(
        "--sitemaps-only",
        action="store_true",
        help="Découvrir les URL depuis les sitemaps sans télécharger les articles.",
    )
    parser.add_argument(
        "--until-sitemaps-done",
        action="store_true",
        help="Répéter les lots jusqu'à épuisement des sitemaps; exige --sitemaps-only.",
    )
    args = parser.parse_args()
    if args.max_sitemaps < 1 or args.max_articles < 1:
        parser.error("--max-sitemaps et --max-articles doivent être supérieurs ou égaux à 1")
    if args.until_sitemaps_done and not args.sitemaps_only:
        parser.error("--until-sitemaps-done exige --sitemaps-only")
    if args.full and args.until_sitemaps_done:
        parser.error("--full et --until-sitemaps-done ne s'utilisent pas ensemble")
    if args.article_url and (args.full or args.sitemaps_only or args.until_sitemaps_done):
        parser.error("--article-url ne s'utilise pas avec --full ou les options sitemap-only")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    processes: list[subprocess.Popen[bytes]] = []
    try:
        processes = _start_manual_desktop()
        logger.info(
            "Bureau navigateur disponible uniquement en local : "
            "http://localhost:7900/vnc.html?autoconnect=1&resize=scale"
        )
        asyncio.run(_collect(args))
    finally:
        _stop_processes(processes)


if __name__ == "__main__":
    main()
