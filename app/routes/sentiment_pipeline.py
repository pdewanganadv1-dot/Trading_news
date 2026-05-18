from fastapi import APIRouter, Query
from app.services.news_sentiment_pipeline import (
    get_cached_sentiment, get_market_sentiment_overview, get_sector_sentiment
)

router = APIRouter(prefix="/api/v1/sentiment", tags=["sentiment-pipeline"])


@router.get("/market/overview")
async def market_sentiment_overview():
    overview = get_market_sentiment_overview()
    return {"status": "success", **overview}


@router.get("/symbol/{symbol}")
async def symbol_sentiment(symbol: str):
    data = get_cached_sentiment(symbol.lower())
    return {"status": "success", **data}


@router.get("/sectors")
async def sector_sentiment():
    sectors = get_sector_sentiment()
    return {"status": "success", "sectors": sectors}


@router.get("/all")
async def all_sentiments():
    data = get_cached_sentiment()
    return {"status": "success", "count": len(data), "symbols": data}


@router.get("/bullish")
async def bullish_stocks(min_score: float = Query(default=0.3, ge=0, le=1)):
    data = get_cached_sentiment()
    bullish = [
        {"symbol": k, "sentiment": v["sentiment"], "score": v["score"], "headlines": v.get("top_headlines", [])}
        for k, v in data.items()
        if v.get("sentiment") == "bullish" and v.get("score", 0) >= min_score
    ]
    bullish.sort(key=lambda x: x["score"], reverse=True)
    return {"status": "success", "count": len(bullish), "stocks": bullish}


@router.get("/bearish")
async def bearish_stocks(min_score: float = Query(default=0.3, ge=0, le=1)):
    data = get_cached_sentiment()
    bearish = [
        {"symbol": k, "sentiment": v["sentiment"], "score": v["score"], "headlines": v.get("top_headlines", [])}
        for k, v in data.items()
        if v.get("sentiment") == "bearish" and v.get("score", 0) >= min_score
    ]
    bearish.sort(key=lambda x: x["score"], reverse=True)
    return {"status": "success", "count": len(bearish), "stocks": bearish}