from fastapi import APIRouter, Query
from app.services.signal_monitor import get_signal_log

router = APIRouter(prefix="/api/v1/signals", tags=["signals-log"])


@router.get("/log")
async def signal_log(limit: int = Query(50, ge=1, le=100)):
    return {"status": "success", "count": limit, "signals": get_signal_log(limit)}
