import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "Tradingview Integration"
    database_url: Optional[str] = None
    tradingview_api_key: Optional[str] = None
    secret_key: str = "dev-secret-key-change-in-production"
    telegram_bot_token: str = "7424796820:AAFwug5i-Q1CoAe-4qkIHfRPLdwAysFc-pY"
    telegram_chat_id: str = "5163568145"
    signal_confidence_threshold: float = 0.65
    signal_check_interval_seconds: int = 600
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"
    persistent_dir: str = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()