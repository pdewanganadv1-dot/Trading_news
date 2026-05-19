from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from app.services.insider_service import insider_trading_service
import os

router = APIRouter(tags=["insider-trading"])


@router.get("/insider-trading")
async def get_insider_page():
    path = os.path.join(os.path.dirname(__file__), "../templates/insider_trading.html")
    return FileResponse(path)


@router.get("/api/insider-trading/bulk-deals")
async def get_bulk_deals(period: str = Query("1M")):
    data = await insider_trading_service.get_bulk_deals(period)
    return {"deals": data, "total": len(data) if isinstance(data, list) else 0}


@router.get("/api/insider-trading/block-deals")
async def get_block_deals(period: str = Query("1M")):
    data = await insider_trading_service.get_block_deals(period)
    return {"deals": data, "total": len(data) if isinstance(data, list) else 0}


@router.get("/api/insider-trading/summary")
async def get_insider_summary(period: str = Query("1M")):
    data = await insider_trading_service.get_insider_summary(period)
    return data
