"""Application settings loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the MindSurve API."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "MindSurve API"
    APP_ENV: str = "development"
    DEBUG: bool = False

    DATABASE_URL: str | None = None

    FRONTEND_URL: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Shared with Unilever staging (same JWT secret → tokens work on both apps)
    JWT_SECRET_KEY: str = Field(
        default="dev-only-change-me-use-a-long-random-secret",
        min_length=8,
    )
    JWT_ALGORITHM: str = "HS256"
    # Aligned with Unilever defaults (7-day access tokens)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    REFRESH_COOKIE_NAME: str = "refresh_token"
    REFRESH_COOKIE_PATH: str = "/api/v1/auth"
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    # Optional: Unilever study engine API (no trailing slash)
    UNILEVER_API_BASE_URL: str | None = None
    # Optional WebSocket origin for Unilever job progress (defaults from API URL)
    UNILEVER_WS_BASE_URL: str | None = None

    # OpenAI — study brief conversation
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Azure Blob Storage — chat / study asset uploads
    AZURE_STORAGE_CONNECTION_STRING: str | None = None
    AZURE_STORAGE_CONTAINER_NAME: str | None = None

    # Share / preview hosts for created studies
    STUDY_SHARE_BASE_URL: str = "https://mindsurve.com"
    STUDY_PREVIEW_BASE_URL: str = "https://mindsurve.com/home/create-study/preview"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        """Prefer psycopg3 driver URL for SQLAlchemy."""
        if isinstance(value, str) and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() in {"production", "prod"}

    @property
    def cors_origins(self) -> list[str]:
        origins: list[str] = []
        for part in self.FRONTEND_URL.split(","):
            origin = part.strip().rstrip("/")
            if origin and origin not in origins:
                origins.append(origin)
        return origins


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear settings cache (tests / runtime reconfiguration)."""
    get_settings.cache_clear()
