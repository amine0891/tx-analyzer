from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TX Analyzer"
    app_env: str = "development"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "tx_analyzer"
    host: str = "0.0.0.0"
    port: int = 8000
    request_delay_seconds: float = Field(default=0.5, ge=0, le=60)
    toxicity_light_threshold: float = Field(default=0.5, ge=0, le=1)
    toxicity_high_threshold: float = Field(default=0.75, ge=0, le=1)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def validate_toxicity_thresholds(self) -> "Settings":
        if not 0 <= self.toxicity_light_threshold < self.toxicity_high_threshold <= 1:
            raise ValueError("Les seuils doivent respecter 0 <= léger < élevé <= 1")
        return self


settings = Settings()
