from fastapi import APIRouter
from app.services.market_edge_service import (
    scan_all_stocks, scan_stock, get_market_breadth, get_fii_dii_summary, set_fii_dii
)

router = APIRouter(prefix="/api/v1/edge", tags=["market-edge"])


@router.get("/scan")
async def edge_scan_all():
    """Scan all Nifty 100 stocks, ranked by edge score."""
    results = await scan_all_stocks()
    return {"status": "success", "count": len(results), "results": results}


@router.get("/scan/{symbol}")
async def edge_scan_symbol(symbol: str):
    """Edge scan for a single symbol."""
    result = scan_stock(symbol)
    return {"status": "success" if not result.get("error") else "error", **result}


@router.get("/breadth")
async def market_breadth():
    """Market breadth: % of stocks above 20-day SMA."""
    breadth = await get_market_breadth()
    return {"status": "success", **breadth}


@router.get("/fiidii")
async def fii_dii_summary():
    """FII/DII institutional flow summary."""
    data = await get_fii_dii_summary()
    return {"status": "success", **data}


@router.post("/fiidii")
async def update_fii_dii(fii_buy: float, fii_sell: float, dii_buy: float, dii_sell: float):
    """Manually update FII/DII data."""
    data = set_fii_dii(fii_buy, fii_sell, dii_buy, dii_sell)
    return {"status": "success", **data}
