from fastapi import APIRouter
from fastapi.responses import FileResponse
import os

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
async def get_dashboard():
    """Serve the main dashboard UI (all features combined)."""
    path = os.path.join(os.path.dirname(__file__), "../templates/dashboard_live.html")
    return FileResponse(path)


@router.get("/dashboard/news")
async def get_dashboard_with_news():
    """Serve the dashboard UI with news integration."""
    path = os.path.join(os.path.dirname(__file__), "../../dashboard/index_news.html")
    return FileResponse(path)


@router.get("/api/dashboard/symbols")
async def get_watchlist():
    """Get default watchlist symbols."""
    return {
        "symbols": [
            {"symbol": "BTCUSD", "name": "Bitcoin", "price": 67432.50},
            {"symbol": "ETHUSD", "name": "Ethereum", "price": 3521.80},
            {"symbol": "AAPL", "name": "Apple Inc", "price": 189.45},
            {"symbol": "EURUSD", "name": "Euro/US Dollar", "price": 1.0823},
            {"symbol": "TSLA", "name": "Tesla Inc", "price": 248.90}
        ]
    }