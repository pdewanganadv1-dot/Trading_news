from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse
from app.services.options_trading import options_trading_service, dhan_source
from app.services.options_signal_service import options_signal_service
from app.services.position_tracker import position_tracker
from app.services.options_backtest.backtest_engine import OptionsBacktestEngine, BacktestConfig
import os

router = APIRouter(tags=["options-trading"])


@router.get("/options-trading")
async def get_options_trading_page():
    path = os.path.join(os.path.dirname(__file__), "../templates/options_trading.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(404, "Template not found")


@router.get("/api/options-trading/chain")
async def get_option_chain(
    symbol: str = Query("NIFTY"),
    expiry: str = Query(None),
):
    data = await options_trading_service.get_option_chain(symbol, expiry)
    if "error" in data:
        data["chain"] = []
        data["dhan_available"] = False
        data["total_pe_oi"] = 0
        data["total_ce_oi"] = 0
        data["total_pe_vol"] = 0
        data["total_ce_vol"] = 0
        data["pcr_oi"] = 0
        data["pcr_vol"] = 0
        data["max_pain"] = 0
        data["underlying"] = 0
        data["chain_length"] = 0
        data["expiry"] = "-"
    return data


@router.get("/api/options-trading/expiries")
async def get_expiry_dates(symbol: str = Query("NIFTY")):
    return await options_trading_service.get_all_expiries(symbol)


@router.get("/api/options-trading/pcr-summary")
async def get_pcr_summary():
    rows = await options_trading_service.get_pcr_summary()
    return {"rows": rows}


@router.get("/api/options-trading/ltp")
async def get_ltp(symbol: str = Query("NIFTY")):
    ltp = await options_trading_service.get_ltp(symbol)
    if ltp is None:
        raise HTTPException(400, f"Cannot fetch LTP for {symbol}")
    return {"symbol": symbol, "ltp": ltp}


@router.get("/api/options-trading/search")
async def search_symbols(query: str = Query(min_length=1)):
    results = await options_trading_service.get_ltp_batch([query])
    matches = dhan_source.search_symbols(query)
    return {"query": query, "matches": matches}


@router.get("/api/options/signals")
async def get_live_signals(
    symbol: str = Query("NIFTY"),
    strategy: str = Query("mega"),
    trailing_stop: float = Query(20.0),
    fixed_target: float = Query(40.0),
    max_hold: int = Query(5),
    max_positions: int = Query(2),
    lot_size: int = Query(50),
):
    try:
        result = await options_signal_service.get_live_signals(
            symbol=symbol.upper(),
            strategy_name=strategy,
            trailing_stop_pct=trailing_stop,
            fixed_target_pct=fixed_target,
            max_hold_days=max_hold,
            max_positions_per_day=max_positions,
            lot_size=lot_size,
        )
        return result
    except Exception as e:
        raise HTTPException(500, f"Signal generation failed: {e}")


@router.get("/api/options/signals/refresh")
async def refresh_signal_data(symbol: str = Query("NIFTY")):
    try:
        await options_signal_service.refresh(symbol)
        return {
            "status": "ok",
            "symbol": symbol,
            "history_days": len(options_signal_service._snapshots),
            "fiidii_days": len(options_signal_service._fiidii_cache),
        }
    except Exception as e:
        raise HTTPException(500, f"Refresh failed: {e}")


@router.get("/api/options/positions")
async def get_positions():
    return {
        "open": position_tracker.get_open(),
        "closed": position_tracker.get_closed(limit=50),
        "running_pnl": position_tracker.running_pnl(),
    }


@router.post("/api/options/positions/close")
async def close_position(pos_id: str = Query(...), reason: str = "Manual"):
    dhan_data = await options_trading_service.get_option_chain("NIFTY")
    if "error" in dhan_data:
        raise HTTPException(400, "Cannot fetch current price")
    pos = position_tracker._find(pos_id)
    if not pos:
        raise HTTPException(404, f"Position {pos_id} not found")
    strike = pos["strike"]
    optype = pos["option_type"]
    price = None
    for r in dhan_data.get("chain", []):
        if r["strike"] == strike:
            key = "ce_ltp" if optype == "CE" else "pe_ltp"
            price = r.get(key, 0)
            break
    if not price or price <= 0:
        raise HTTPException(400, "Cannot determine current price")
    result = position_tracker.close(pos_id, price, dhan_data.get("underlying", 0), reason)
    if not result:
        raise HTTPException(400, f"Position {pos_id} not open")
    return result


@router.get("/api/options/positions/history")
async def position_history(limit: int = Query(50)):
    return {"trades": position_tracker.get_closed(limit=limit)}


@router.post("/api/options/positions/open")
async def open_position(
    action: str = Query(...),
    strike: int = Query(...),
    option_type: str = Query(...),
    expiry: str = Query(...),
    entry_price: float = Query(...),
    qty: int = Query(50),
    entry_reason: str = Query("Manual"),
):
    symbol = "NIFTY"
    dhan_data = await options_trading_service.get_option_chain(symbol)
    underlying = dhan_data.get("underlying", 0) if "error" not in dhan_data else 0
    pos = position_tracker.open(
        symbol=symbol, action=action, strike=strike,
        option_type=option_type, expiry=expiry,
        entry_price=entry_price, qty=qty,
        entry_reason=entry_reason, underlying_entry=underlying,
        trailing_stop_pct=0.2, fixed_target_pct=0.4,
        max_hold_days=3,
    )
    if not pos:
        raise HTTPException(400, "Position already open or invalid")
    return pos


@router.post("/api/options/positions/update")
async def update_positions(symbol: str = Query("NIFTY")):
    return await options_signal_service.update_positions(symbol)


@router.get("/api/options/backtest")
async def run_backtest(
    symbol: str = Query("NIFTY"),
    strategy: str = Query("mega"),
    from_date: str = Query("04-03-2026"),
    to_date: str = Query("03-06-2026"),
    trailing_stop: float = Query(20.0),
    fixed_target: float = Query(40.0),
    max_hold: int = Query(3),
    decay_stop: float = Query(70.0),
    entry_type: str = Query("close"),
    max_loss: float = Query(20000.0),
    trade_qty: int = Query(0),
    lot_size: int = Query(50),
):
    try:
        engine = OptionsBacktestEngine()
        config = BacktestConfig(
            symbol=symbol.upper(),
            strategy_name=strategy,
            from_date=from_date,
            to_date=to_date,
            trailing_stop_pct=trailing_stop,
            fixed_target_pct=fixed_target,
            max_hold_days=max_hold,
            decay_stop_pct=decay_stop,
            entry_type=entry_type,
            max_loss_per_trade=max_loss,
            trade_qty=trade_qty,
            lot_size=lot_size,
        )
        result = engine.run(config)
        trades = []
        for t in result.trades:
            is_short = t.get("is_short", False)
            trades.append({
                "entry_date": t["entry_date"],
                "exit_date": t["exit_date"],
                "action": "SELL" if is_short else "BUY",
                "option_type": t.get("option_type", ""),
                "strike": t["strike"],
                "qty": t.get("qty", lot_size),
                "entry_price": round(t["entry_price"], 1),
                "exit_price": round(t["exit_price"], 1) if t.get("exit_price") else None,
                "pnl": round(t["pnl"], 0),
                "pnl_pct": round(t.get("pnl_pct", 0), 1),
                "exit_reason": t.get("exit_reason", ""),
                "days_held": t.get("days_held", 0),
            })
        return {
            "strategy": strategy,
            "symbol": symbol,
            "from_date": from_date,
            "to_date": to_date,
            "trades": trades,
            "summary": {
                "total_trades": result.total_trades,
                "win_rate": round(result.win_rate * 100, 1),
                "total_pnl": round(result.total_pnl, 0),
                "profit_factor": round(result.profit_factor, 2),
                "max_drawdown_pct": round(result.max_drawdown_pct, 1),
                "sharpe_ratio": round(result.sharpe_ratio, 2),
                "avg_days_held": round(result.avg_days_held, 1),
            },
            "equity_curve": [round(e, 0) for e in result.equity_curve],
            "daily_pnl": result.daily_pnl,
        }
    except Exception as e:
        raise HTTPException(500, f"Backtest failed: {e}")
