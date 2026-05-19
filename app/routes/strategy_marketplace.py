from fastapi import APIRouter, Query, Body
from fastapi.responses import FileResponse
from app.services.strategy_marketplace import strategy_marketplace_service
import os

router = APIRouter(tags=["strategy-marketplace"])


@router.get("/strategy-marketplace")
async def get_strategy_page():
    path = os.path.join(os.path.dirname(__file__), "../templates/strategy_marketplace.html")
    return FileResponse(path)


@router.get("/api/strategy-marketplace/strategies")
async def get_strategies(type_filter: str = Query(None)):
    strategies = strategy_marketplace_service.get_strategies(type_filter)
    return {"strategies": strategies, "total": len(strategies)}


@router.get("/api/strategy-marketplace/types")
async def get_strategy_types():
    types = strategy_marketplace_service.get_types()
    return {"types": types}


@router.get("/api/strategy-marketplace/strategy/{strategy_id}")
async def get_strategy(strategy_id: str):
    strategy = strategy_marketplace_service.get_strategy(strategy_id)
    return strategy or {"error": "Strategy not found"}


@router.post("/api/strategy-marketplace/backtest")
async def run_backtest(
    strategy_id: str = Body(...),
    symbol: str = Body("NIFTY"),
    days: int = Body(365),
):
    result = strategy_marketplace_service.run_backtest(strategy_id, symbol, days)
    return result
