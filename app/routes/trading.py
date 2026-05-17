from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.services.trading import trading_engine, SignalType

router = APIRouter(prefix="/api/v1/trading", tags=["trading"])


class AnalyzeRequest(BaseModel):
    symbol: str
    data: list


class SignalResponse(BaseModel):
    symbol: str
    signal: str
    price: float
    confidence: float
    strategy: str


@router.post("/analyze", response_model=SignalResponse)
async def analyze_symbol(request: AnalyzeRequest, strategy: str = "ma_crossover"):
    """Run trading strategy analysis on symbol data."""
    try:
        result = trading_engine.run(request.symbol, request.data, strategy)
        return SignalResponse(
            symbol=result.symbol,
            signal=result.signal.value,
            price=result.price,
            confidence=result.confidence,
            strategy=result.strategy
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategies")
async def list_strategies():
    """List available trading strategies."""
    return {"strategies": list(trading_engine.strategies.keys())}