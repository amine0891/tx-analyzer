from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.repository import save_prediction
from app.ml.classifier import classify_text
from app.schemas import PredictionRequest, PredictionResponse

router = APIRouter()


@router.get("/health", tags=["system"], summary="Vérifier l'état de l'API et de MongoDB")
def health(request: Request) -> dict[str, str]:
    client = request.app.state.mongo_client
    try:
        client.admin.command("ping")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MongoDB indisponible") from exc
    return {"status": "ok", "service": settings.app_name, "database": "ok"}


@router.post(
    "/predict",
    response_model=PredictionResponse,
    tags=["prediction"],
    summary="Prédire le niveau de toxicité d'un texte",
)
async def predict(payload: PredictionRequest, request: Request) -> PredictionResponse:
    try:
        result = await run_in_threadpool(classify_text, payload.text)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Le modele de classification est indisponible") from exc

    record = {
        **result,
        "text": payload.text,
        "source_url": str(payload.source_url) if payload.source_url else None,
    }
    try:
        await run_in_threadpool(save_prediction, request.app.state.mongo_database, record)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MongoDB indisponible, prediction non enregistree") from exc
    return PredictionResponse.model_validate(result)
