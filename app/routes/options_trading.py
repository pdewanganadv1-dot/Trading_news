from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse
from app.services.options_trading import options_trading_service, dhan_source
from app.services.options_signal_service import options_signal_service
from app.services.position_tracker import position_tracker
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
        raise HTTPException(400, data["error"])
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


@router.post("/api/options/positions/update")
async def update_positions(symbol: str = Query("NIFTY")):
    return await options_signal_service.update_positions(symbol)
