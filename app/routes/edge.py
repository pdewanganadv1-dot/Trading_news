from fastapi import APIRouter, Query
from app.services.market_edge_service import (
    scan_all_stocks, scan_stock, get_market_breadth,
    get_fii_dii_summary, set_fii_dii, get_fii_dii_history
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


@router.get("/fiidii/history")
async def fii_dii_history(days: int = Query(default=10, le=30)):
    """Historical FII/DII data for trend analysis."""
    history = get_fii_dii_history(days)
    return {"status": "success", "count": len(history), "history": history}


@router.get("/fiidii/trend")
async def fii_dii_trend():
    """FII/DII trend analysis (direction, strength)."""
    history = get_fii_dii_history(15)
    if len(history) < 2:
        return {"status": "success", "trend": "insufficient_data"}
    fii_nets = [h.get("fii_net", 0) for h in history if h.get("fii_net") is not None]
    dii_nets = [h.get("dii_net", 0) for h in history if h.get("dii_net") is not None]
    if len(fii_nets) < 2:
        return {"status": "success", "trend": "insufficient_data"}
    fii_trend = "rising" if fii_nets[0] > fii_nets[-1] else "falling"
    dii_trend = "rising" if dii_nets[0] > dii_nets[-1] else "falling"
    avg_fii = round(sum(fii_nets) / len(fii_nets), 2)
    avg_dii = round(sum(dii_nets) / len(dii_nets), 2)
    return {
        "status": "success",
        "fii_trend": fii_trend,
        "dii_trend": dii_trend,
        "avg_fii_net": avg_fii,
        "avg_dii_net": avg_dii,
        "latest_fii_net": fii_nets[0] if fii_nets else 0,
        "latest_dii_net": dii_nets[0] if dii_nets else 0,
        "data_points": len(fii_nets),
    }
