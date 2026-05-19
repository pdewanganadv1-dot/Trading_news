from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from app.services.politician_service import politician_trades_service
import os

router = APIRouter(tags=["politician-trades"])


@router.get("/politician-trades")
async def get_politician_page():
    path = os.path.join(os.path.dirname(__file__), "../templates/politician_trades.html")
    return FileResponse(path)


@router.get("/api/politician-trades/dashboard")
async def get_politician_dashboard(period: str = Query("6M")):
    data = await politician_trades_service.get_politician_dashboard(period)
    return data


@router.get("/api/politician-trades/groups")
async def get_tracked_groups():
    return politician_trades_service.get_tracked_groups()


@router.get("/api/politician-trades/group-deals")
async def get_group_deals(period: str = Query("6M")):
    data = await politician_trades_service.get_bulk_deals_by_group(period)
    return data
