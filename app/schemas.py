from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


ToxicityLevel = Literal["non_toxic", "slightly_toxic", "highly_toxic"]


class Article(BaseModel):
    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    section: str | None = None
    collected_at: datetime


class PredictionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    source_url: HttpUrl | None = None

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Le texte ne peut pas être vide ou composé d'espaces")
        return value


class PredictionResponse(BaseModel):
    prediction: Literal["toxic", "non_toxic"]
    toxicity_level: ToxicityLevel
    score: float = Field(ge=0, le=1)


class SiteStatistics(BaseModel):
    source_id: str
    source_name: str
    article_count: int = Field(ge=0)
    slightly_toxic_percent: float = Field(ge=0, le=100)
    highly_toxic_percent: float = Field(ge=0, le=100)
    analyzed_at: datetime
