import os

from pydantic_settings import BaseSettings

TIER_LIMITS: dict[str, dict[str, int]] = {
    "free": {"daily": 100, "monthly": 3_000},
    "starter": {"daily": 500, "monthly": 10_000},
    "growth": {"daily": 5_000, "monthly": 100_000},
    "enterprise": {"daily": 50_000, "monthly": 1_000_000},
}


class Settings(BaseSettings):
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./cancelkit.db")
    api_title: str = "CancelKit"
    api_description: str = (
        "Subscription Intelligence API — cancel, pause, downgrade, refund, billing,"
        " contact, and churn signals for any subscription service."
    )
    api_version: str = "0.7.0"
    default_daily_limit: int = 100
    default_monthly_limit: int = 3000
    cors_origins: list[str] = ["*"]

    # Database connection pooling (Postgres only; ignored for SQLite)
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # Environment identifier for security behavior
    env: str = os.getenv("CANCELKIT_ENV", "development")

    model_config = {"env_prefix": "CANCELKIT_"}


settings = Settings()
