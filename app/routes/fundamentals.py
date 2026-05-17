from fastapi import APIRouter, HTTPException
from app.services.fundamentals import get_fundamentals

router = APIRouter(prefix="/api/v1/fundamentals", tags=["fundamentals"])


@router.get("/{ticker}")
async def fundamentals(ticker: str):
    data = get_fundamentals(ticker)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No data for {ticker.upper()}")
    return data
