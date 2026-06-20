from fastapi import APIRouter, Query
from app.services.crypto_strategy_service import crypto_strategy_service, ALL_SYMBOLS
from app.services.crypto_signal_tracker import crypto_signal_tracker

router = APIRouter(tags=["crypto"])

_VALID = set(ALL_SYMBOLS)

@router.get("/api/crypto/signals")
async def crypto_signals():
    signals = await crypto_strategy_service.get_all_signals()
    return {"status": "ok", "signals": signals}

@router.get("/api/crypto/signal")
async def crypto_signal(symbol: str = Query("btc")):
    sym = symbol.lower()
    if sym not in _VALID:
        return {"status": "error", "error": f"Supported: {', '.join(sorted(_VALID))}"}
    signal = await crypto_strategy_service.get_signal(sym)
    return {"status": "ok", "signal": signal}

@router.get("/api/crypto/backtest")
async def crypto_backtest(
    symbol: str = Query("btc"),
    trailing_sl_pct: float = Query(3.0, description="Trailing stop % from peak/trough (0 = disabled)"),
    rr_ratio: float = Query(2.0, description="Risk:Reward ratio for TP (0 = use fixed TP)"),
):
    sym = symbol.lower()
    if sym not in _VALID:
        return {"status": "error", "error": f"Supported: {', '.join(sorted(_VALID))}"}
    bt = await crypto_strategy_service.backtest(sym, trailing_sl_pct, rr_ratio)
    return {"status": "ok", "backtest": bt}

@router.get("/api/crypto/strategies")
async def crypto_strategies(symbol: str = Query("btc")):
    strategies = await crypto_strategy_service.get_best_strategies(symbol)
    return {"status": "ok", "strategies": strategies[:20]}

@router.get("/api/crypto/history")
async def crypto_history(limit: int = Query(50)):
    return {"status": "ok", "history": crypto_signal_tracker.get_history(limit)}

@router.get("/api/crypto/stats")
async def crypto_stats():
    return {"status": "ok", "stats": crypto_signal_tracker.get_stats()}
