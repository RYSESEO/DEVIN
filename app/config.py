from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./cancelkit.db"
    api_title: str = "CancelKit"
    api_description: str = (
        "REST API returning structured cancellation paths for any subscription service."
    )
    api_version: str = "0.1.0"
    default_daily_limit: int = 100
    default_monthly_limit: int = 3000
    cors_origins: list[str] = ["*"]

    model_config = {"env_prefix": "CANCELKIT_"}


settings = Settings()
