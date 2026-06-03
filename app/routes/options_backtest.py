from fastapi import APIRouter, Query, HTTPException
from app.services.options_backtest.backtest_engine import OptionsBacktestEngine, BacktestConfig
from app.services.options_backtest import OptionsDataLoader

router = APIRouter(tags=["options-backtest"])


@router.get("/api/options-backtest/run")
async def run_backtest(
    symbol: str = Query("NIFTY"),
    strategy_name: str = Query("combined"),
    period_days: int = Query(45),
    trailing_stop_pct: float = Query(30.0),
    fixed_target_pct: float = Query(0.0),
    stop_loss_pct: float = Query(0.0),
    max_hold_days: int = Query(3),
    max_positions_per_day: int = Query(2),
    lot_size: int = Query(50),
):
    engine = OptionsBacktestEngine()
    config = BacktestConfig(
        symbol=symbol.upper(),
        strategy_name=strategy_name,
        period_days=period_days,
        trailing_stop_pct=trailing_stop_pct,
        fixed_target_pct=fixed_target_pct,
        stop_loss_pct=stop_loss_pct,
        max_hold_days=max_hold_days,
        max_positions_per_day=max_positions_per_day,
        lot_size=lot_size,
    )
    result = engine.run(config)
    return result.to_dict()


@router.get("/api/options-backtest/strategies")
async def list_strategies():
    return {
        "strategies": [
            {"id": "mega", "name": "MEGA (FII filter + Ultra selective + Short premium)"},
            {"id": "fii_filtered", "name": "FII/DII Flow Filtered Directional"},
            {"id": "short_premium", "name": "Short Premium (Sell OTM, collect theta)"},
            {"id": "ultra_selective", "name": "Ultra Selective Trend (rare entries)"},
        ]
    }
