from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
from app.services.news_service import news_service, alert_service
from app.services.real_news import real_news_service

router = APIRouter(prefix="/api/v1/news", tags=["news"])


class NewsItemResponse(BaseModel):
    title: str
    description: str
    url: str
    source: str
    published_at: datetime
    symbols: List[str] = []


class RealNewsResponse(BaseModel):
    title: str
    description: str
    url: str
    source: str
    published_at: str


class CreatePriceAlertRequest(BaseModel):
    symbol: str
    condition: str
    value: float
    callback_url: str


class CreateNewsAlertRequest(BaseModel):
    keywords: List[str]
    callback_url: str


# Use mock or real based on configuration
USE_REAL_NEWS = True  # Set to True when API keys are configured


def format_news_item(item: dict) -> RealNewsResponse:
    """Format news item from various sources."""
    published = item.get("published_at") or item.get("published_utc") or datetime.utcnow().isoformat()
    if isinstance(published, str) and "T" not in published:
        published = published + "T00:00:00Z"

    return RealNewsResponse(
        title=item.get("title", ""),
        description=str(item.get("description", ""))[:300],
        url=item.get("url", "") or item.get("link", ""),
        source=item.get("source", "Unknown"),
        published_at=published
    )


@router.get("", response_model=List[RealNewsResponse])
async def get_market_news(use_real: bool = Query(default=True)):
    """Get general market news. Set use_real=false for mock data."""
    if use_real and USE_REAL_NEWS:
        crypto_news = await real_news_service.get_all_news()
        return [format_news_item(n) for n in crypto_news[:10]]

    # Return mock news with proper format
    news = await news_service.get_market_news()
    return [
        RealNewsResponse(
            title=n.title,
            description=n.description,
            url=n.url,
            source=n.source,
            published_at=n.published_at.isoformat(),
            symbols=n.symbols
        ) for n in news
    ]


@router.get("/symbol/{symbol}", response_model=List[RealNewsResponse])
async def get_symbol_news(symbol: str):
    """Get news specific to a symbol."""
    news = await news_service.get_symbol_news(symbol)
    return [format_news_item({
        "title": n.title,
        "description": n.description,
        "url": n.url,
        "source": n.source,
        "published_at": n.published_at.isoformat(),
        "symbols": n.symbols
    }) for n in news]


@router.get("/crypto", response_model=List[RealNewsResponse])
async def get_crypto_news(use_real: bool = Query(default=False)):
    """Get cryptocurrency news. Set use_real=true for live crypto headlines."""
    if use_real and USE_REAL_NEWS:
        news = await real_news_service.get_crypto_news()
        return [format_news_item(n) for n in news[:15]]

    news = await news_service.get_crypto_news()
    return [format_news_item({
        "title": n.title,
        "description": n.description,
        "url": n.url,
        "source": n.source,
        "published_at": n.published_at.isoformat()
    }) for n in news]


@router.get("/metals", response_model=List[RealNewsResponse])
async def get_metals_news(use_real: bool = Query(default=False)):
    """Get precious metals news (gold, silver). Set use_real=true for live data."""
    if use_real and USE_REAL_NEWS:
        news = await real_news_service.get_metals_news()
        return [format_news_item(n) for n in news[:10]]

    news = await news_service.get_commodities_news()
    return [format_news_item({
        "title": n.title,
        "description": n.description,
        "url": n.url,
        "source": n.source,
        "published_at": n.published_at.isoformat()
    }) for n in news if "gold" in n.title.lower() or "silver" in n.title.lower() or "XAU" in str(n.symbols) or "XAG" in str(n.symbols)]


@router.get("/gold", response_model=List[RealNewsResponse])
async def get_gold_news(use_real: bool = Query(default=False)):
    """Get gold-specific news."""
    return await get_metals_news(use_real)


@router.get("/silver", response_model=List[RealNewsResponse])
async def get_silver_news(use_real: bool = Query(default=False)):
    """Get silver-specific news."""
    return await get_metals_news(use_real)


@router.get("/forex", response_model=List[RealNewsResponse])
async def get_forex_news(use_real: bool = Query(default=False)):
    """Get forex news. Set use_real=true for live data."""
    if use_real and USE_REAL_NEWS:
        news = await real_news_service.get_forex_news()
        return [format_news_item(n) for n in news[:10]]

    news = await news_service.get_forex_news()
    return [format_news_item({
        "title": n.title,
        "description": n.description,
        "url": n.url,
        "source": n.source,
        "published_at": n.published_at.isoformat()
    }) for n in news]


@router.get("/commodities", response_model=List[RealNewsResponse])
async def get_commodities_news():
    """Get commodities news (gold, silver, oil, etc.)."""
    news = await news_service.get_commodities_news()
    return [format_news_item({
        "title": n.title,
        "description": n.description,
        "url": n.url,
        "source": n.source,
        "published_at": n.published_at.isoformat()
    }) for n in news]


@router.post("/alerts/price")
async def create_price_alert(request: CreatePriceAlertRequest):
    """Create a price alert."""
    valid_conditions = ["above", "below", "cross_up", "cross_down"]
    if request.condition not in valid_conditions:
        raise HTTPException(status_code=400, detail=f"Invalid condition: {valid_conditions}")
    return alert_service.create_price_alert(
        request.symbol,
        request.condition,
        request.value,
        request.callback_url
    )


@router.post("/alerts/news")
async def create_news_alert(request: CreateNewsAlertRequest):
    """Create a news alert based on keywords."""
    return alert_service.create_news_alert(
        request.keywords,
        request.callback_url
    )


@router.get("/alerts")
async def get_alerts(type: str = Query(default="all")):
    """Get all alerts or filter by type."""
    return alert_service.get_alerts(type)


@router.delete("/alerts/{alert_type}/{alert_id}")
async def delete_alert(alert_type: str, alert_id: int):
    """Delete an alert."""
    if not alert_service.delete_alert(alert_type, alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "deleted", "alert_type": alert_type, "alert_id": alert_id}


@router.get("/live/crypto")
async def get_live_crypto_news():
    """Get live crypto news from CryptoCompare API (no key required)."""
    try:
        news = await real_news_service.get_crypto_news()
        return {
            "status": "success",
            "count": len(news),
            "news": [format_news_item(n) for n in news[:20]]
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "news": []}


@router.get("/live/metals")
async def get_live_metals_news():
    """Get live metals news from RSS feeds."""
    try:
        news = await real_news_service.get_metals_news()
        return {
            "status": "success",
            "count": len(news),
            "news": [format_news_item(n) for n in news[:15]]
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "news": []}


def filter_news_by_symbol(news: list, symbol: str) -> list:
    """Filter news by specific symbol/asset."""
    symbol_lower = symbol.lower()

    keywords_map = {
        "btc": ["bitcoin", "btc", "satoshi", "halving", "btcusd", "xbt"],
        "eth": ["ethereum", "eth", "ether", "ethusd", "ethereum foundation", "vitalik"],
        "gold": ["gold", "xau", "goldman", "precious metal", "troy ounce", "gold price", "gold broker"],
        "silver": ["silver", "xag", "silver price", "silver doctors", "silver market"]
    }

    keywords = keywords_map.get(symbol_lower, [symbol_lower])

    filtered = []
    for item in news:
        text = (item.get("title", "") + " " + item.get("description", "")).lower()
        if any(k in text for k in keywords):
            filtered.append(item)

    return filtered[:15]


@router.get("/live/{symbol}")
async def get_live_symbol_news(symbol: str):
    """Get live news for specific symbol (btc, eth, gold, silver)."""
    try:
        # Get all crypto and metals news
        crypto_news = await real_news_service.get_crypto_news()
        metals_news = await real_news_service.get_metals_news()

        all_news = crypto_news + metals_news
        filtered_news = filter_news_by_symbol(all_news, symbol)

        # Get sentiment for filtered news
        sentiment = real_news_service.get_market_sentiment(filtered_news)

        return {
            "status": "success",
            "symbol": symbol.upper(),
            "count": len(filtered_news),
            "news": [format_news_item(n) for n in filtered_news],
            "sentiment": sentiment
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "news": [], "sentiment": {}}