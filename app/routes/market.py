from fastapi import APIRouter, Query
from typing import Optional
from app.data.market_data import market_data
from app.models.schemas import Quote, OHLC, SymbolInfo

router = APIRouter(prefix="/api/v1/market", tags=["market"])


@router.get("/quote/{symbol}", response_model=Quote)
async def get_quote(
    symbol: str,
    exchange: str = Query(default="NASDAQ")
):
    """Get real-time quote for a symbol."""
    return await market_data.get_quote(symbol, exchange)


@router.get("/history/{symbol}", response_model=list[OHLC])
async def get_history(
    symbol: str,
    interval: str = Query(default="1h", regex="^(1m|5m|15m|1h|4h|1d|1w)$"),
    limit: int = Query(default=100, le=1000)
):
    """Get historical OHLCV data."""
    return await market_data.get_historical(symbol, interval, limit)


@router.get("/symbols", response_model=list[SymbolInfo])
async def get_symbols(market: str = Query(default="crypto")):
    """Get available symbols for a market."""
    return await market_data.get_symbols(market)