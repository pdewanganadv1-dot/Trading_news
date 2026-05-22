import os
from pydantic_settings import BaseSettings
from typing import Optional


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PERSISTENT_DIR = os.path.join(_PROJECT_ROOT, 'data')


class Settings(BaseSettings):
    app_name: str = "Tradingview Integration"
    database_url: Optional[str] = None
    tradingview_api_key: Optional[str] = None
    secret_key: str = "dev-secret-key-change-in-production"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    signal_confidence_threshold: float = 0.65
    signal_check_interval_seconds: int = 600
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"
    persistent_dir: str = _DEFAULT_PERSISTENT_DIR
    dhan_client_id: Optional[str] = None
    dhan_access_token: Optional[str] = None
    render_api_key: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()