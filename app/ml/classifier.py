from threading import Lock
from typing import Any

from app.core.config import settings
from app.schemas import ToxicityLevel

_MODEL_LOAD_LOCK = Lock()
_MODEL: Any | None = None


def _load_model() -> Any:
    # Le paquet charge le modele multilingue une seule fois, puis l'inference
    # s'effectue dans ce processus. Aucun appel d'inference distant n'est fait.
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOAD_LOCK:
            if _MODEL is None:
                from detoxify import Detoxify

                _MODEL = Detoxify("multilingual")
    return _MODEL


def _as_score_list(value: Any) -> list[float]:
    # Detoxify may return a scalar for one input, or a tensor/array for batches.
    if hasattr(value, "detach"):
        value = value.detach().cpu().tolist()
    elif hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return [float(score) for score in value]
    return [float(value)]


def _format_prediction(toxicity: float, severe_toxicity: float) -> dict[str, float | bool | ToxicityLevel]:
    if toxicity >= settings.toxicity_high_threshold:
        level: ToxicityLevel = "highly_toxic"
    elif toxicity >= settings.toxicity_light_threshold:
        level = "slightly_toxic"
    else:
        level = "non_toxic"

    is_toxic = level != "non_toxic"
    confidence = toxicity if is_toxic else 1.0 - toxicity
    return {
        "prediction": "toxic" if is_toxic else "non_toxic",
        "toxicity_level": level,
        "score": round(confidence, 6),
        "toxicity_score": round(toxicity, 6),
        "severe_toxicity_score": round(severe_toxicity, 6),
        "is_toxic": is_toxic,
        "light_threshold": settings.toxicity_light_threshold,
        "high_threshold": settings.toxicity_high_threshold,
    }


def classify_texts(texts: list[str]) -> list[dict[str, float | bool | ToxicityLevel]]:
    """Classify several texts in one model call for efficient dataset analysis."""
    if not texts:
        return []

    model_output = _load_model().predict(texts)
    toxicity_scores = _as_score_list(model_output["toxicity"])
    severe_scores = _as_score_list(model_output.get("severe_toxicity", [0.0] * len(texts)))
    if len(toxicity_scores) != len(texts) or len(severe_scores) != len(texts):
        raise ValueError("Le modèle a retourné un nombre de scores différent du nombre de textes")
    return [
        _format_prediction(toxicity, severe)
        for toxicity, severe in zip(toxicity_scores, severe_scores)
    ]


def classify_text(text: str) -> dict[str, float | bool | ToxicityLevel]:
    """Classify one text; the REST API uses the same rule as batch analysis."""
    return classify_texts([text])[0]
