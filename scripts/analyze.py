import argparse
import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.db.mongo import create_mongo_client, get_database
from app.db.repository import ensure_indexes, save_site_statistics
from app.ml.classifier import classify_text, classify_texts
from app.schemas import SiteStatistics
from app.scraping.sources import SOURCES

BATCH_SIZE = 16
VALID_LEVELS = {"non_toxic", "slightly_toxic", "highly_toxic"}
ANALYSIS_SOURCES = SOURCES


def _record_level(counts: dict[str, dict[str, int]], source_id: str, level: str) -> None:
    bucket = counts[source_id]
    bucket["total"] += 1
    bucket["slight"] += int(level == "slightly_toxic")
    bucket["high"] += int(level == "highly_toxic")


def _analyze_batch(
    database,
    articles: list[dict],
    counts: dict[str, dict[str, int]],
) -> None:
    to_classify: list[dict] = []
    results_by_id: dict = {}
    article_ids = [article["_id"] for article in articles]
    prediction_cache = {
        prediction["article_id"]: prediction
        for prediction in database.predictions.find(
            {"article_id": {"$in": article_ids}},
            {
                "article_id": 1,
                "toxicity_level": 1,
                "light_threshold": 1,
                "high_threshold": 1,
                "is_toxic": 1,
                "score": 1,
                "toxicity_score": 1,
                "severe_toxicity_score": 1,
            },
        )
    }
    for article in articles:
        source_id = article.get("source_id")
        if source_id not in counts:
            logging.warning("Source inconnue pour l'article %s", article.get("_id"))
            continue
        records_to_check = (article, prediction_cache.get(article["_id"]))
        cached = next(
            (
                record for record in records_to_check
                if record is not None
                and record.get("toxicity_level") in VALID_LEVELS
                and record.get("light_threshold") == settings.toxicity_light_threshold
                and record.get("high_threshold") == settings.toxicity_high_threshold
                and isinstance(record.get("is_toxic"), bool)
                and isinstance(record.get("score"), (int, float))
                and isinstance(record.get("toxicity_score"), (int, float))
            ),
            None,
        )
        if cached is not None:
            level = cached["toxicity_level"]
            results_by_id[article["_id"]] = {
                "prediction": "toxic" if cached["is_toxic"] else "non_toxic",
                "toxicity_level": level,
                "score": cached["score"],
                "toxicity_score": cached["toxicity_score"],
                "severe_toxicity_score": cached.get("severe_toxicity_score", 0.0),
                "is_toxic": cached["is_toxic"],
                "light_threshold": settings.toxicity_light_threshold,
                "high_threshold": settings.toxicity_high_threshold,
            }
        else:
            to_classify.append(article)

    if to_classify:
        try:
            results = classify_texts([str(article.get("content", "")) for article in to_classify])
            results_by_id.update({article["_id"]: result for article, result in zip(to_classify, results)})
        except Exception:
            # One unusual record should not prevent the rest of the batch from being saved.
            logging.exception("Échec du lot de %s articles; reprise article par article", len(to_classify))
            for article in to_classify:
                try:
                    results_by_id[article["_id"]] = classify_text(str(article.get("content", "")))
                except Exception:
                    logging.exception("Classification impossible pour %s", article.get("_id"))

    for article in articles:
        result = results_by_id.get(article.get("_id"))
        if result is None:
            continue
        article_id = article["_id"]
        source_id = article["source_id"]
        database.articles.update_one(
            {"_id": article_id},
            {"$set": {key: value for key, value in result.items() if key != "prediction"}},
        )
        # Keep the complete analyzed text and source URL with the per-article
        # prediction. article_id makes the operation idempotent on restart.
        prediction_document = {
            "article_id": article_id,
            "source_id": source_id,
            "title": article.get("title"),
            "text": str(article.get("content", "")),
            "source_url": article.get("url"),
            **result,
        }
        database.predictions.update_one(
            {"article_id": article_id},
            {
                "$set": prediction_document,
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        _record_level(counts, source_id, str(result["toxicity_level"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Classe les articles et calcule les statistiques par source.")
    parser.add_argument(
        "--source",
        choices=[source.source_id for source in ANALYSIS_SOURCES],
        help="Limiter le traitement à une source, par exemple lepoint. Les résultats déjà valides sont réutilisés.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = create_mongo_client()
    try:
        database = get_database(client)
        ensure_indexes(database)
        selected_sources = tuple(
            source for source in ANALYSIS_SOURCES
            if args.source is None or source.source_id == args.source
        )
        counts = {source.source_id: {"total": 0, "slight": 0, "high": 0} for source in selected_sources}

        source_filter = {"source_id": {"$in": list(counts)}}
        total_articles = database.articles.count_documents(source_filter)
        processed = 0
        projection = {
            "source_id": 1,
            "url": 1,
            "title": 1,
            "content": 1,
            "toxicity_level": 1,
            "light_threshold": 1,
            "high_threshold": 1,
            "is_toxic": 1,
            "score": 1,
            "toxicity_score": 1,
            "severe_toxicity_score": 1,
        }
        while True:
            # Fetch and close a bounded cursor before inference. Keeping one
            # cursor open while the model runs can let MongoDB expire it.
            batch = list(
                database.articles.find(source_filter, projection)
                .sort("_id", 1)
                .skip(processed)
                .limit(BATCH_SIZE)
            )
            if not batch:
                break

            _analyze_batch(database, batch, counts)
            processed += len(batch)
            logging.info("Progression : %s/%s articles parcourus", processed, total_articles)

        analyzed_at = datetime.now(timezone.utc)
        rows: list[SiteStatistics] = []
        for source in selected_sources:
            bucket = counts[source.source_id]
            total = bucket["total"]
            row = SiteStatistics(
                source_id=source.source_id,
                source_name=source.name,
                article_count=total,
                slightly_toxic_percent=round(100 * bucket["slight"] / total, 2) if total else 0.0,
                highly_toxic_percent=round(100 * bucket["high"] / total, 2) if total else 0.0,
                analyzed_at=analyzed_at,
            )
            # A targeted run updates per-article predictions only. Keeping the
            # aggregate snapshot untouched avoids mixing analysis timestamps.
            if args.source is None:
                save_site_statistics(database, row)
            rows.append(row)

        print(f"{'Site':<22} {'Articles':>8} {'Légèrement toxiques':>21} {'Fortement toxiques':>20}")
        for row in rows:
            print(
                f"{row.source_name:<22} {row.article_count:>8} "
                f"{row.slightly_toxic_percent:>19.2f}% {row.highly_toxic_percent:>18.2f}%"
            )
        ranked = sorted((row for row in rows if row.article_count), key=lambda row: row.highly_toxic_percent, reverse=True)
        if ranked:
            top = ranked[0]
            print(
                f"\nPart la plus élevée de textes fortement toxiques : {top.source_name} "
                f"({top.highly_toxic_percent:.2f}%, sur {top.article_count} article(s))."
            )
        else:
            print("\nAucun article n'est disponible pour l'analyse.")
        if args.source is not None:
            print("Statistiques agrégées inchangées : relancez l'analyse complète après la collecte pour actualiser le graphique.")
        print(f"Seuils utilisés : léger={settings.toxicity_light_threshold}, élevé={settings.toxicity_high_threshold}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
