from fastapi import APIRouter, Query
from typing import Optional
from app.services.market_data_service import market_data_service

router = APIRouter(prefix="/api/v1/market", tags=["market"])


@router.get("/quote/{symbol}")
async def get_quote(symbol: str):
    """Get real-time quote for a symbol."""
    data = await market_data_service.get_price_data(symbol)
    if data:
        return {"symbol": symbol.upper(), "price": data["price"], "change": data.get("change", 0), "change_percent": data.get("change_pct", 0)}
    return {"symbol": symbol.upper(), "error": "No data available"}


@router.get("/history/{symbol}")
async def get_history(
    symbol: str,
    interval: str = Query(default="1h", pattern="^(1m|5m|15m|1h|4h|1d|1w)$"),
    limit: int = Query(default=100, le=1000)
):
    """Get historical OHLCV data."""
    prices = await market_data_service.get_5min_prices(symbol, min(limit, 500))
    if prices:
        return [{"time": p.get("time", ""), "open": p.get("open", 0), "high": p.get("high", 0), "low": p.get("low", 0), "close": p.get("close", 0), "volume": p.get("volume", 0)} for p in prices]
    return []


@router.get("/symbols")
async def get_symbols():
    """Get all monitored symbols."""
    from app.data.stocks import MONITORED_SYMBOLS
    return {"symbols": MONITORED_SYMBOLS}