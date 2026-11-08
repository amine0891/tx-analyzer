"""Render the latest completed per-source toxicity statistics as a PNG."""

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from app.db.mongo import create_mongo_client, get_database
from app.scraping.sources import SOURCES

ANALYSIS_SOURCES = SOURCES
OUTPUT_PATH = Path(os.getenv("DASHBOARD_OUTPUT", "/app/output/toxicite_par_site.png"))


def _latest_statistics(database) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in database.site_statistics.find({}).sort("analyzed_at", -1):
        latest.setdefault(row["source_id"], row)
    return latest


def _prediction_counts(database) -> dict[str, int]:
    counts = {source.source_id: 0 for source in ANALYSIS_SOURCES}
    pipeline = [
        {"$match": {"article_id": {"$exists": True}, "source_id": {"$in": list(counts)}}},
        {"$group": {"_id": "$source_id", "count": {"$sum": 1}}},
    ]
    for row in database.predictions.aggregate(pipeline):
        counts[row["_id"]] = row["count"]
    return counts


def main() -> None:
    client = create_mongo_client()
    try:
        database = get_database(client)
        statistics = _latest_statistics(database)
        prediction_counts = _prediction_counts(database)

        missing = [source.source_id for source in ANALYSIS_SOURCES if source.source_id not in statistics]
        if missing:
            raise RuntimeError(
                "Statistiques absentes pour certaines sources. Lancez d'abord "
                "scripts/analyze.py jusqu'à son tableau final : " + ", ".join(missing)
            )

        rows = [(source, statistics[source.source_id]) for source in ANALYSIS_SOURCES]
        analysis_times = {row.get("analyzed_at") for _, row in rows}
        if len(analysis_times) != 1:
            raise RuntimeError(
                "Les statistiques des sources ne proviennent pas du même "
                "passage d'analyse. Relancez scripts/analyze.py jusqu'à la fin."
            )
        mismatches = [
            f"{source.source_id}: stats={row.get('article_count', 0)}, prédictions={prediction_counts[source.source_id]}"
            for source, row in rows
            if row.get("article_count", 0) != prediction_counts[source.source_id]
        ]
        if mismatches:
            raise RuntimeError(
                "Le dashboard n'est pas généré : les statistiques et les prédictions "
                "par article ne concordent pas. Relancez scripts/analyze.py. "
                + " | ".join(mismatches)
            )

        names = [source.name for source, _ in rows]
        sample_sizes = [int(row.get("article_count", 0)) for _, row in rows]
        slight = [float(row.get("slightly_toxic_percent", 0)) for _, row in rows]
        high = [float(row.get("highly_toxic_percent", 0)) for _, row in rows]
        total = sum(sample_sizes)
        ranked_rows = sorted(
            zip(names, sample_sizes, slight, high),
            key=lambda item: item[3],
            reverse=True,
        )
        names, sample_sizes, slight, high = map(list, zip(*ranked_rows))

        fig, ax = plt.subplots(figsize=(15, 8), dpi=160)
        fig.patch.set_facecolor("#f5f7fb")
        ax.set_facecolor("#ffffff")
        y = np.arange(len(names))
        point_offset = 0.13
        for idx, (light_value, high_value) in enumerate(zip(slight, high)):
            ax.plot(
                [light_value, high_value], [y[idx], y[idx]],
                color="#d8deea", linewidth=1.4, zorder=1,
            )
        ax.scatter(
            slight, y + point_offset, label="Légèrement toxiques",
            color="#5965d8", s=64, marker="o", zorder=3,
        )
        ax.scatter(
            high, y - point_offset, label="Fortement toxiques",
            color="#e04f5f", s=64, marker="D", zorder=3,
        )

        ax.set_title("Analyse de toxicité par source", loc="left", fontsize=22, weight="bold", pad=24)
        ax.text(
            0,
            1.04,
            f"{total:,} articles analysés · {len(names)} sources · seuils : léger ≥ 0,50 / très toxique ≥ 0,75".replace(",", " "),
            transform=ax.transAxes,
            fontsize=11,
            color="#536078",
        )
        max_value = max(slight + high, default=0.0)
        tick_step = 0.1 if max_value <= 1 else (0.25 if max_value <= 2.5 else 0.5)
        x_limit = max(5 * tick_step, np.ceil(max_value * 1.25 / tick_step) * tick_step)
        ax.set_xlim(0, x_limit)
        ax.set_xticks(np.arange(0, x_limit + tick_step / 2, tick_step))
        ax.set_xticklabels([f"{tick:.2f}%" for tick in ax.get_xticks()])
        ax.set_xlabel("Part des articles (%) — échelle resserrée", fontsize=11)
        ax.set_yticks(y, [f"{name}  (n={count:,})".replace(",", " ") for name, count in zip(names, sample_sizes)])
        ax.invert_yaxis()
        ax.tick_params(axis="x", labelsize=9, colors="#536078")
        ax.tick_params(axis="y", labelsize=10, colors="#27324a", length=0, pad=10)
        ax.grid(axis="x", color="#e8ecf3", linewidth=0.8)
        ax.set_axisbelow(True)
        ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
        # Keep the legend in the lower-right empty area; the upper rows contain
        # the largest percentages and their value labels.
        ax.legend(frameon=False, ncols=2, loc="lower right")
        for idx, (light_value, high_value) in enumerate(zip(slight, high)):
            ax.annotate(f"{light_value:.2f}%", (light_value, y[idx] + point_offset),
                        xytext=(6, 0), textcoords="offset points", va="center", fontsize=8, color="#374151")
            ax.annotate(f"{high_value:.2f}%", (high_value, y[idx] - point_offset),
                        xytext=(6, 0), textcoords="offset points", va="center", fontsize=8, color="#374151")

        eligible = [(source.name, float(row.get("highly_toxic_percent", 0)), int(row.get("article_count", 0)))
                    for source, row in rows if int(row.get("article_count", 0)) > 0]
        if eligible:
            top_name, top_percent, top_count = max(eligible, key=lambda entry: entry[1])
            interpretation = (
                f"Part la plus élevée de textes très toxiques : {top_name} — "
                f"{top_percent:.2f}% ({top_count:,} articles).".replace(",", " ")
            )
        else:
            interpretation = "Aucun article classé pour le moment."
        fig.text(0.08, 0.045, interpretation, fontsize=10, weight="bold", color="#27324a")
        fig.text(
            0.08,
            0.018,
            "Résultats indicatifs du modèle; comparaison limitée aux articles collectés et classés dans ce test.",
            fontsize=8,
            color="#687386",
        )
        fig.tight_layout(rect=(0.04, 0.09, 0.98, 0.96))

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(OUTPUT_PATH, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        print(f"Dashboard généré : {OUTPUT_PATH}")
        print(interpretation)
    finally:
        client.close()


if __name__ == "__main__":
    main()
