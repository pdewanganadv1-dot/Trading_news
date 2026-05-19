from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from app.services.sector_service import sector_rotation_service
import os

router = APIRouter(tags=["sector-rotation"])


@router.get("/sector-rotation")
async def get_sector_page():
    path = os.path.join(os.path.dirname(__file__), "../templates/sector_rotation.html")
    return FileResponse(path)


@router.get("/api/sector-rotation/performance")
async def get_sector_performance(period: str = Query("1W")):
    data = await sector_rotation_service.get_full_rotation_view(period)
    return data


@router.get("/api/sector-rotation/breakdown")
async def get_sector_breakdown():
    data = await sector_rotation_service.get_sector_breakdown()
    return {"sectors": data}


@router.get("/api/sector-rotation/sector-stocks")
async def get_stocks_by_sector():
    data = await sector_rotation_service.get_stocks_by_sector()
    return data
