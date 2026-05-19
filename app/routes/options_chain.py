from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from app.services.options_chain_service import options_chain_service
import os

router = APIRouter(tags=["options-chain"])


@router.get("/options-chain")
async def get_options_page():
    path = os.path.join(os.path.dirname(__file__), "../templates/options_chain.html")
    return FileResponse(path)


@router.get("/api/options-chain/data")
async def get_option_chain_data(
    symbol: str = Query("NIFTY"),
    expiry: str = Query(None),
):
    data = await options_chain_service.get_option_chain(symbol, expiry)
    return data


@router.get("/api/options-chain/expiries")
async def get_expiry_dates(symbol: str = Query("NIFTY")):
    data = await options_chain_service.get_option_chain(symbol)
    if "error" in data:
        return {"expiries": []}
    return {"expiries": data.get("expiry_dates", []), "current": data.get("expiry_date")}


@router.get("/api/options-chain/top-stocks")
async def get_top_fo_stocks(limit: int = Query(20)):
    stocks = await options_chain_service.get_top_fo_stocks(limit)
    return {"stocks": stocks}


@router.get("/api/options-chain/pcr-summary")
async def get_pcr_summary():
    rows = await options_chain_service.get_pcr_summary()
    return {"rows": rows}
