from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings
from app.db.mongo import create_mongo_client, get_database
from app.db.repository import ensure_indexes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = create_mongo_client()
    app.state.mongo_client = client
    app.state.mongo_database = get_database(client)
    try:
        ensure_indexes(app.state.mongo_database)
        yield
    finally:
        client.close()


app = FastAPI(
    title=settings.app_name,
    description="Collecte et analyse de contenus textuels.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
