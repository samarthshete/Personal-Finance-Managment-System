from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "BudgetFlow"
    API_V1_STR: str = "/api/v1"

    APP_ENV: str = "local"  # local | development | production

    SECRET_KEY: str = "development_secret_key_change_in_production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    DATABASE_URL: Optional[str] = None
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_USER: str = "budgetflow_user"
    POSTGRES_PASSWORD: str = "budgetflow_password"
    POSTGRES_DB: str = "budgetflow_db"
    POSTGRES_PORT: str = "5432"

    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = ""  # comma-separated list

    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "budgetflow-reports"
    S3_REGION: str = "us-east-1"
    S3_FORCE_PATH_STYLE: bool = True
    S3_USE_SSL: bool = False

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    ADVISOR_ENABLED: bool = True

    LOG_LEVEL: str = "INFO"

    @field_validator("APP_ENV", mode="before")
    @classmethod
    def normalize_app_env(cls, v: str) -> str:
        return (v or "local").strip().lower()

    @property
    def is_production(self) -> bool:
        return self.APP_ENV in {"prod", "production"}

    @property
    def effective_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            "postgresql+asyncpg://"
            f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def cors_origins(self) -> list[str]:
        if self.CORS_ORIGINS.strip():
            return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

        if self.is_production:
            return [self.FRONTEND_URL] if self.FRONTEND_URL else []

        defaults = {
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        }
        if self.FRONTEND_URL:
            defaults.add(self.FRONTEND_URL)
        return sorted(defaults)

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.is_production:
            if not self.DATABASE_URL:
                raise ValueError("DATABASE_URL is required when APP_ENV=production")
            if not self.SECRET_KEY or self.SECRET_KEY == "development_secret_key_change_in_production":
                raise ValueError("SECRET_KEY must be set to a strong production value")
            if not self.cors_origins:
                raise ValueError("Set FRONTEND_URL or CORS_ORIGINS for production CORS")
        return self

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
