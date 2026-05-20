import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from app.services.real_news import real_news_service

_SENTIMENT_CACHE: Dict[str, Dict] = {}
_SENTIMENT_CACHE_TS: float = 0

_COMPANY_NAMES = {
    "reliance": ["reliance", "reliance industries", "ril"],
    "tcs": ["tcs", "tata consultancy services"],
    "hdfcbank": ["hdfc bank", "hdfc"],
    "infy": ["infosys", "infy"],
    "icicibank": ["icici bank", "icici"],
    "sbin": ["sbi", "state bank of india"],
    "lt": ["larsen & toubro", "larsen and toubro", "l&t"],
    "wipro": ["wipro"],
    "itc": ["itc"],
    "bhartiartl": ["airtel", "bharti airtel"],
    "maruti": ["maruti suzuki", "maruti"],
    "nestleind": ["nestle india", "nestle"],
    "hindunilvr": ["hindustan unilever", "hul"],
    "asianpaint": ["asian paints"],
    "sunpharma": ["sun pharma", "sun pharmaceutical"],
    "titan": ["titan"],
    "bajajfinsv": ["bajaj finserv"],
    "hcltech": ["hcl technologies", "hcl tech", "hcl"],
    "kotakbank": ["kotak mahindra", "kotak"],
    "axisbank": ["axis bank"],
    "ntpc": ["ntpc"],
    "tatasteel": ["tata steel"],
    "cipla": ["cipla"],
    "ultracemco": ["ultratech cement", "ultratech"],
    "adani": ["adani"],
    "zomato": ["zomato", "blinkit"],
    "tata": ["tata motors", "tata power", "tata"],
    "bajaj": ["bajaj finance", "bajaj auto"],
}

_NIFTY_STOCKS = list(_COMPANY_NAMES.keys())

_BULLISH_KEYWORDS = [
    "surge", "soar", "rally", "gain", "jump", "rise", "higher", "growth",
    "bullish", "positive", "record", "breakthrough", "strong", "boom",
    "upgrade", "approval", "profit", "revenue", "beat", "outperform",
]

_BEARISH_KEYWORDS = [
    "fall", "drop", "crash", "plunge", "decline", "bearish", "negative",
    "sell", "selling", "weak", "loss", "cut", "downgrade", "risk",
    "volatile", "slump", "pressure", "crisis", "ban", "regulation",
]

_SECTOR_MAP = {
    "it": ["tcs", "infy", "hcltech", "wipro"],
    "banking": ["hdfcbank", "icicibank", "kotakbank", "axisbank", "sbin"],
    "auto": ["maruti", "tata"],
    "pharma": ["sunpharma", "cipla"],
    "fmcg": ["hindunilvr", "nestleind", "itc"],
    "energy": ["reliance", "ntpc"],
    "metal": ["tatasteel"],
    "finance": ["bajajfinsv", "bajaj"],
}


def _compute_sentiment(title: str, description: str) -> Dict:
    text = (title + " " + (description or "")).lower()
    bullish_count = sum(1 for k in _BULLISH_KEYWORDS if k in text)
    bearish_count = sum(1 for k in _BEARISH_KEYWORDS if k in text)
    if bullish_count > bearish_count:
        sentiment = "bullish"
        score = min(1.0, (bullish_count - bearish_count) / max(bullish_count, 1))
    elif bearish_count > bullish_count:
        sentiment = "bearish"
        score = min(1.0, (bearish_count - bullish_count) / max(bearish_count, 1))
    else:
        sentiment = "neutral"
        score = 0.0
    return {"sentiment": sentiment, "score": round(score, 2), "bullish_count": bullish_count, "bearish_count": bearish_count}


async def _update_symbol_sentiment(symbol: str) -> Dict:
    symbol_lower = symbol.lower()
    news = await real_news_service.get_all_news()
    keywords = _COMPANY_NAMES.get(symbol_lower, [symbol_lower])
    relevant = []
    for item in news:
        text = ((item.get("title") or "") + " " + (item.get("description") or "")).lower()
        if any(k.lower() in text for k in keywords):
            relevant.append(item)
    if not relevant:
        return {"symbol": symbol.upper(), "sentiment": "neutral", "score": 0, "article_count": 0}
    scores = [_compute_sentiment(n.get("title", ""), n.get("description", "")) for n in relevant]
    bullish = sum(1 for s in scores if s["sentiment"] == "bullish")
    bearish = sum(1 for s in scores if s["sentiment"] == "bearish")
    neutral = len(scores) - bullish - bearish
    avg_score = sum(s["score"] for s in scores) / len(scores) if scores else 0
    if bullish > bearish:
        overall = "bullish"
    elif bearish > bullish:
        overall = "bearish"
    else:
        overall = "neutral"
    return {
        "symbol": symbol.upper(),
        "sentiment": overall,
        "score": round(avg_score, 2),
        "bullish_articles": bullish,
        "bearish_articles": bearish,
        "neutral_articles": neutral,
        "article_count": len(news),
        "updated_at": datetime.now().isoformat(),
        "top_headlines": [n.get("title", "") for n in relevant[:3]],
    }


async def refresh_all_sentiments():
    global _SENTIMENT_CACHE, _SENTIMENT_CACHE_TS
    # Pre-warm news cache with one call before parallel processing
    await real_news_service.get_all_news()
    tasks = {}
    for symbol in _NIFTY_STOCKS:
        tasks[symbol] = asyncio.create_task(_update_symbol_sentiment(symbol))
    results = {}
    for symbol, task in tasks.items():
        try:
            results[symbol] = await task
        except Exception as e:
            print(f"[SentimentPipeline] Error for {symbol}: {e}")
    _SENTIMENT_CACHE = results
    _SENTIMENT_CACHE_TS = datetime.now().timestamp()


def get_cached_sentiment(symbol: Optional[str] = None) -> Dict:
    if symbol:
        key = symbol.lower()
        data = _SENTIMENT_CACHE.get(key)
        if data:
            return data
        return {"symbol": symbol.upper(), "sentiment": "unknown", "score": 0, "article_count": 0}
    return dict(_SENTIMENT_CACHE)


def get_market_sentiment_overview() -> Dict:
    now = datetime.now().timestamp()
    age = int(now - _SENTIMENT_CACHE_TS) if _SENTIMENT_CACHE_TS else -1
    total = len(_SENTIMENT_CACHE)
    if total == 0:
        return {"status": "empty", "total_symbols": 0}
    bullish = sum(1 for v in _SENTIMENT_CACHE.values() if v.get("sentiment") == "bullish")
    bearish = sum(1 for v in _SENTIMENT_CACHE.values() if v.get("sentiment") == "bearish")
    neutral = total - bullish - bearish
    avg_score = sum(v.get("score", 0) for v in _SENTIMENT_CACHE.values()) / total
    bullish_stocks = sorted(
        [(sym, v.get("score", 0)) for sym, v in _SENTIMENT_CACHE.items() if v.get("sentiment") == "bullish"],
        key=lambda x: x[1], reverse=True
    )
    bearish_stocks = sorted(
        [(sym, v.get("score", 0)) for sym, v in _SENTIMENT_CACHE.items() if v.get("sentiment") == "bearish"],
        key=lambda x: x[1], reverse=True
    )
    return {
        "status": "ok",
        "total_symbols": total,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "avg_score": round(avg_score, 2),
        "bullish_pct": round(bullish / total * 100, 1) if total else 0,
        "bearish_pct": round(bearish / total * 100, 1) if total else 0,
        "bullish_stocks": bullish_stocks,
        "bearish_stocks": bearish_stocks,
        "cache_age_seconds": age,
        "updated_at": datetime.fromtimestamp(_SENTIMENT_CACHE_TS).isoformat() if _SENTIMENT_CACHE_TS else None,
    }


def get_sector_sentiment() -> Dict:
    sectors = {}
    for sector_name, members in _SECTOR_MAP.items():
        sentiments = [_SENTIMENT_CACHE.get(sym, {}).get("sentiment", "neutral") for sym in members]
        scores = [_SENTIMENT_CACHE.get(sym, {}).get("score", 0) for sym in members]
        if not sentiments:
            continue
        bullish = sentiments.count("bullish")
        bearish = sentiments.count("bearish")
        avg_score = sum(scores) / len(scores) if scores else 0
        total = len(sentiments)
        sectors[sector_name] = {
            "sentiment": "bullish" if bullish > bearish else "bearish" if bearish > bullish else "neutral",
            "score": round(avg_score, 2),
            "bullish": bullish,
            "bearish": bearish,
            "total": total,
            "bullish_pct": round(bullish / total * 100, 1) if total else 0,
        }
    return sectors


async def sentiment_pipeline_loop():
    await asyncio.sleep(30)
    while True:
        try:
            await refresh_all_sentiments()
        except Exception as e:
            print(f"[SentimentPipeline] Loop error: {e}")
        await asyncio.sleep(600)