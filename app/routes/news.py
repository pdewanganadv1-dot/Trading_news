import httpx
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime, timedelta
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


# Company name mappings for better news filtering
_COMPANY_NAMES = {
    "reliance": ["reliance", "reliance industries", "ril", "reliance industries ltd", "mukesh ambani"],
    "tcs": ["tcs", "tata consultancy", "tata consultancy services", "tcs ltd"],
    "hdfcbank": ["hdfc bank", "hdfcbank", "hdfc"],
    "infy": ["infosys", "infy", "infosys ltd", "nandan nilekani"],
    "icicibank": ["icici bank", "icicibank", "icici"],
    "tatamotors": ["tata motors", "tatamotors", "jaguar", "land rover", "tata ev"],
    "sbin": ["sbi", "state bank of india", "sbin"],
    "lt": ["larsen & toubro", "larsen and toubro", "l&t", "lt"],
    "wipro": ["wipro", "wipro ltd"],
    "itc": ["itc", "itc ltd", "itc hotels"],
    "bhartiartl": ["airtel", "bharti airtel", "bhartiartl"],
    "maruti": ["maruti", "maruti suzuki", "maruti suzuki india"],
    "nestleind": ["nestle", "nestle india", "nestlend"],
    "hindunilvr": ["hindustan unilever", "hul", "hindunilvr"],
    "asianpaint": ["asian paints", "asianpaint"],
    "sunpharma": ["sun pharma", "sun pharmaceutical", "sunpharma"],
    "titan": ["titan", "titan company", "titan watches"],
    "bajajfinance": ["bajaj finance", "bajajfinance"],
    "hcltech": ["hcl", "hcl tech", "hcl technologies", "hcltech"],
    "kotakbank": ["kotak", "kotak mahindra", "kotakbank"],
    "axisbank": ["axis bank", "axisbank"],
    "m&m": ["mahindra", "mahindra & mahindra", "m&m", "mahindra and mahindra"],
    "powergrid": ["power grid", "powergrid"],
    "ntpc": ["ntpc", "ntpc ltd"],
    "coalindia": ["coal india", "coalindia"],
    "bpcl": ["bpcl", "bharat petroleum"],
    "hindalco": ["hindalco", "hindalco industries"],
    "jswsteel": ["jsw steel", "jswsteel"],
    "tatasteel": ["tata steel", "tatasteel"],
    "ultratech": ["ultratech", "ultratech cement", "ultratech"],
    "grasim": ["grasim", "grasim industries"],
    "divislab": ["divi's", "divis lab", "divis laboratories"],
    "cipla": ["cipla", "cipla ltd"],
    "drreddy": ["dr reddy", "dr. reddy", "drreddy"],
    "hero moto": ["hero motocorp", "hero moto", "hero"],
    "eichermot": ["eicher", "eicher motors", "royal enfield"],
    "bajaj-auto": ["bajaj auto", "bajaj-auto"],
    "techm": ["tech mahindra", "techm"],
    "tata power": ["tata power", "tatapower"],
    "adani": ["adani", "adani group", "gautam adani"],
    "zomato": ["zomato", "zomato ltd", "blinkit"],
    "hal": ["hal", "hindustan aeronautics", "h.a.l."],
    "irfc": ["irfc", "indian railway finance"],
    "irctc": ["irctc", "indian railway catering"],
    "lici": ["lic", "lic india", "lici"],
    "zyduslife": ["zydus", "zydus life", "zydus lifesciences"],
}


async def _fetch_news_for_symbol(symbol: str) -> list:
    """Dynamically fetch relevant news based on symbol type."""
    symbol_lower = symbol.lower()
    symbol_base = symbol_lower.replace(".ns", "")

    news = []

    if symbol_base in ("btc", "eth"):
        news = await real_news_service.get_crypto_news()
    elif symbol_base in ("gold", "silver", "xau", "xag"):
        news = await real_news_service.get_metals_news()
    else:
        # Indian stocks → stock market RSS + Indian news RSS + Google News query
        stock_news = await real_news_service.get_stocks_news()
        news.extend(stock_news)
        forex_news = await real_news_service.get_forex_news()
        news.extend(forex_news[:5])
        commodities_news = await real_news_service.get_commodities_news()
        news.extend(commodities_news[:5])
        # yfinance ticker-specific news (may return empty for Indian stocks)
        yf_news = await real_news_service.get_stock_news_from_yfinance(f"{symbol_base.upper()}.NS")
        news.extend(yf_news)
        # Google News as fallback — use company name if available
        company_name = _COMPANY_NAMES.get(symbol_base, [symbol_base])[0]
        google_news = await _fetch_google_news(company_name)
        news.extend(google_news)

    return news


def _parse_timestamp(item: dict) -> datetime:
    """Parse timestamps from various formats to offset-naive UTC datetime."""
    raw = item.get("published_at") or item.get("published_utc") or ""
    if not raw:
        return datetime.min
    cleaned = raw.strip()
    # Try ISO format with timezone -> convert to naive UTC
    try:
        dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None) - dt.utcoffset()
        return dt
    except (ValueError, AttributeError, TypeError):
        pass
    # Try RFC 2822 (e.g. Sun, 17 May 2026 13:36:59 GMT or +0000)
    months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
    try:
        parts = cleaned.replace(",", "").split()
        if len(parts) >= 6 and parts[2] in months:
            day, month, year = int(parts[1]), months[parts[2]], int(parts[3])
            time_parts = parts[4].split(":")
            h, m = int(time_parts[0]), int(time_parts[1])
            s = int(time_parts[2]) if len(time_parts) > 2 else 0
            # Strip trailing offset tokens (+0000, GMT, etc.)
            return datetime(year, month, day, h, m, s)
    except (ValueError, IndexError, KeyError):
        pass
    return datetime.min


def filter_news_by_symbol(news: list, symbol: str) -> list:
    """Filter news by specific symbol/asset; sort by recency + relevance, return top 8."""
    symbol_lower = symbol.lower().replace(".ns", "")

    keywords_map = {
        "btc": ["bitcoin", "btc", "satoshi", "halving", "btcusd", "xbt"],
        "eth": ["ethereum", "eth", "ether", "ethusd", "ethereum foundation", "vitalik"],
        "gold": ["gold", "xau", "goldman", "precious metal", "troy ounce", "gold price", "gold broker"],
        "silver": ["silver", "xag", "silver price", "silver doctors", "silver market"]
    }

    keywords = keywords_map.get(symbol_lower, [symbol_lower])
    company_keywords = _COMPANY_NAMES.get(symbol_lower, [])
    keywords.extend(company_keywords)

    cutoff = datetime.utcnow() - timedelta(hours=168)

    scored = []
    seen = set()
    for item in news:
        title = (item.get("title", "") or "").strip()
        desc = (item.get("description", "") or "")
        text = (title + " " + desc).lower()

        if not any(k in text for k in keywords):
            continue
        if not title or title in seen:
            continue
        seen.add(title)

        ts = _parse_timestamp(item)
        if ts < cutoff:
            continue

        # Score: title match > description match
        title_lower = title.lower()
        score = 0
        for k in keywords:
            if k in title_lower:
                score += 3
            elif k in desc.lower():
                score += 1

        scored.append((score, ts, item))

    # Sort by score desc, then timestamp desc
    scored.sort(key=lambda x: (-x[0], -x[1].timestamp()))
    return [item for _, _, item in scored[:8]]


async def _fetch_google_news(query: str) -> list:
    """Fetch news from Google News RSS for a search query."""
    try:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN"
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return []
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)
            items = []
            for item in root.iter("item"):
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                desc = item.findtext("description", "")[:200] if item.findtext("description") else ""
                pub = item.findtext("pubDate", "")
                source = ""
                source_el = item.find("source")
                if source_el is not None:
                    source = source_el.text or ""
                items.append({
                    "title": title,
                    "description": desc,
                    "url": link,
                    "source": source or "Google News",
                    "published_at": pub,
                    "category": "stocks"
                })
            return items[:10]
    except Exception as e:
        print(f"Google News error for {query}: {e}")
        return []


@router.get("/live/{symbol}")
async def get_live_symbol_news(symbol: str):
    """Get live news for specific symbol — automatically fetches relevant sources."""
    try:
        all_news = await _fetch_news_for_symbol(symbol)
        filtered_news = filter_news_by_symbol(all_news, symbol)

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