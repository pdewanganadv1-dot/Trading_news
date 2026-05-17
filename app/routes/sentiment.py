from fastapi import APIRouter
from app.services.sentiment import sentiment_monitor
from app.services.news_service import news_service
from app.services.social_sentiment import analyze_social_sentiment

router = APIRouter(prefix="/api/v1/sentiment", tags=["sentiment"])


@router.get("/market")
async def get_market_sentiment():
    """Get overall market sentiment based on latest news."""
    news = await news_service.get_market_news()
    sentiment = await sentiment_monitor.get_market_sentiment([
        {"title": n.title, "description": n.description, "symbols": n.symbols}
        for n in news
    ])
    signal = sentiment_monitor.generate_trading_signal(sentiment)
    return {
        "sentiment": sentiment,
        "trading_signal": signal
    }


@router.get("/symbol/{symbol}")
async def get_symbol_sentiment(symbol: str):
    """Get sentiment for a specific symbol."""
    news = await news_service.get_symbol_news(symbol)
    sentiment = await sentiment_monitor.get_symbol_sentiment(symbol, [
        {"title": n.title, "description": n.description, "symbols": n.symbols}
        for n in news
    ])
    signal = sentiment_monitor.generate_trading_signal(sentiment)
    return {
        "symbol": symbol,
        "sentiment": sentiment,
        "trading_signal": signal
    }


@router.get("/crypto")
async def get_crypto_sentiment():
    """Get crypto market sentiment."""
    news = await news_service.get_crypto_news()
    sentiment = await sentiment_monitor.get_market_sentiment([
        {"title": n.title, "description": n.description, "symbols": n.symbols}
        for n in news
    ])
    signal = sentiment_monitor.generate_trading_signal(sentiment)
    return {
        "market": "crypto",
        "sentiment": sentiment,
        "trading_signal": signal
    }


@router.get("/commodities")
async def get_commodities_sentiment():
    """Get commodities market sentiment (gold, silver)."""
    news = await news_service.get_commodities_news()
    sentiment = await sentiment_monitor.get_market_sentiment([
        {"title": n.title, "description": n.description, "symbols": n.symbols}
        for n in news
    ])
    signal = sentiment_monitor.generate_trading_signal(sentiment)
    return {
        "market": "commodities",
        "sentiment": sentiment,
        "trading_signal": signal
    }


@router.get("/social/{ticker}")
async def social_sentiment(ticker: str = "BTC"):
    """Get StockTwits + Reddit sentiment for a ticker."""
    return await analyze_social_sentiment(ticker.upper())