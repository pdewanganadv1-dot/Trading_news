from fastapi import APIRouter
from datetime import datetime
from app.services.market_data_service import market_data_service, TechnicalIndicators, TradingSignals

router = APIRouter(prefix="/api/v1/market", tags=["market-realtime"])


@router.get("/realtime/{symbol}")
async def get_realtime_data(symbol: str):
    """Get real-time market data with calculated indicators for a symbol."""
    # Get current price
    price_data = await market_data_service.get_price_data(symbol)

    if not price_data:
        return {
            "status": "error",
            "message": "Could not fetch price data",
            "symbol": symbol.upper()
        }

    # Get historical data for indicators
    historical_prices = await market_data_service.get_historical_prices(symbol, 50)

    # Calculate indicators
    indicators = TechnicalIndicators.calculate_all(historical_prices)

    # Determine trend based on indicators
    trend = "neutral"

    sma_trend = "neutral"
    if indicators['sma']['sma20'] and indicators['sma']['sma50']:
        if indicators['sma']['sma20'] > indicators['sma']['sma50']:
            sma_trend = "bullish"
        else:
            sma_trend = "bearish"

    rsi_trend = "neutral"
    if indicators['rsi']:
        if indicators['rsi'] > 70:
            rsi_trend = "sell"
        elif indicators['rsi'] < 30:
            rsi_trend = "buy"
        else:
            rsi_trend = "neutral"

    macd_trend = "neutral"
    if indicators['macd']['histogram'] > 0:
        macd_trend = "bullish"
    elif indicators['macd']['histogram'] < 0:
        macd_trend = "bearish"

    # Overall trend
    bullish_count = sum(1 for t in [sma_trend, rsi_trend, macd_trend] if t in ['bullish', 'buy'])
    if bullish_count >= 2:
        trend = "bullish"
    elif bullish_count <= 1 and sma_trend == "bearish":
        trend = "bearish"

    return {
        "status": "success",
        "symbol": symbol.upper(),
        "price": price_data,
        "indicators": {
            "sma": {
                "sma20": round(indicators['sma']['sma20'], 2) if indicators['sma']['sma20'] else None,
                "sma50": round(indicators['sma']['sma50'], 2) if indicators['sma']['sma50'] else None,
                "sma200": round(indicators['sma']['sma200'], 2) if indicators['sma']['sma200'] else None,
                "trend": sma_trend
            },
            "rsi": round(indicators['rsi'], 2) if indicators['rsi'] else None,
            "rsi_signal": rsi_trend,
            "macd": indicators['macd'],
            "macd_signal": macd_trend,
            "bb": indicators['bb']
        },
        "trend": trend,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/signals/{symbol}")
async def get_5min_signals(symbol: str):
    """Get trading signals based on 5-minute timeframe data."""
    # Get current price
    price_data = await market_data_service.get_price_data(symbol)
    if not price_data:
        return {"status": "error", "message": "Could not fetch price data"}

    # Get 5-minute interval prices
    prices_5m = await market_data_service.get_5min_prices(symbol, 100)

    # Generate signals
    signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])

    return {
        "status": "success",
        "symbol": symbol.upper(),
        "timeframe": "5m",
        "current_price": price_data['price'],
        "signal": signal_data['signal'],
        "confidence": signal_data['confidence'],
        "reasons": signal_data['reasons'],
        "indicators": signal_data['indicators'],
        "timestamp": datetime.now().isoformat()
    }


@router.get("/signals")
async def get_all_signals():
    """Get trading signals for all symbols."""
    symbols = ['btc', 'eth', 'gold', 'silver']
    results = {}

    for symbol in symbols:
        price_data = await market_data_service.get_price_data(symbol)
        if price_data:
            prices_5m = await market_data_service.get_5min_prices(symbol, 100)
            signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])
            results[symbol] = {
                "signal": signal_data['signal'],
                "confidence": signal_data['confidence'],
                "price": price_data['price']
            }

    return results


@router.get("/realtime")
async def get_all_realtime():
    """Get real-time data for all supported symbols."""
    symbols = ['btc', 'eth', 'gold', 'silver']
    results = {}

    for symbol in symbols:
        data = await get_realtime_data(symbol)
        results[symbol] = data

    return results
