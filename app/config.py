from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "Tradingview Integration"
    database_url: Optional[str] = None
    tradingview_api_key: Optional[str] = None
    secret_key: str = "dev-secret-key-change-in-production"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()