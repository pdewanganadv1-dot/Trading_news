from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse
from app.services.options_trading import options_trading_service, dhan_source
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
