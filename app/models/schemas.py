from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class Quote(BaseModel):
    symbol: str
    exchange: str
    price: float
    change: Optional[float] = 0.0
    change_percent: Optional[float] = 0.0
    volume: Optional[int] = 0
    timestamp: datetime


class OHLC(BaseModel):
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class SymbolInfo(BaseModel):
    symbol: str
    name: str
    exchange: str
    type: str  # stock, crypto, forex, commodity
    description: Optional[str] = None


class WebhookAlert(BaseModel):
    id: Optional[int] = None
    symbol: str
    condition: str  # price_above, price_below, cross_over, cross_under
    value: float
    callback_url: str
    active: bool = True
    created_at: Optional[datetime] = None