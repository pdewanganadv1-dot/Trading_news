from fastapi import APIRouter
from datetime import datetime
from app.services.market_data_service import market_data_service, TechnicalIndicators, TradingSignals
from app.services.signal_explainer import signal_explainer
from app.services.signal_monitor import _MONITORED_SYMBOLS

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
    sma = indicators.get('sma', {})
    if sma.get('sma20') and sma.get('sma50'):
        if sma['sma20'] > sma['sma50']:
            sma_trend = "bullish"
        else:
            sma_trend = "bearish"

    rsi_trend = "neutral"
    rsi_val = indicators.get('rsi')
    if rsi_val is not None:
        if rsi_val > 70:
            rsi_trend = "sell"
        elif rsi_val < 30:
            rsi_trend = "buy"

    macd_trend = "neutral"
    macd = indicators.get('macd', {})
    if macd.get('histogram', 0) > 0:
        macd_trend = "bullish"
    elif macd.get('histogram', 0) < 0:
        macd_trend = "bearish"

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
                "sma20": round(sma['sma20'], 2) if sma.get('sma20') else None,
                "sma50": round(sma['sma50'], 2) if sma.get('sma50') else None,
                "sma200": round(sma['sma200'], 2) if sma.get('sma200') else None,
                "trend": sma_trend
            },
            "rsi": round(rsi_val, 2) if rsi_val is not None else None,
            "rsi_signal": rsi_trend,
            "macd": macd if macd else {"macd": 0, "signal": 0, "histogram": 0},
            "macd_signal": macd_trend,
            "bb": indicators.get('bb', {"upper": 0, "middle": 0, "lower": 0})
        },
        "trend": trend,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/yftest")
async def yf_test():
    import yfinance as yf
    try:
        t = yf.Ticker("RELIANCE.NS")
        info = t.info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        return {"price": price, "market_state": info.get("marketState"), "ok": price is not None}
    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}


@router.get("/signals/{symbol}")
async def get_5min_signals(symbol: str):
    """Get trading signals based on 5-minute timeframe data."""
    price_data = await market_data_service.get_price_data(symbol)
    if not price_data:
        return {"status": "error", "message": "Could not fetch price data"}

    price = price_data['price']

    # Fetch multi-timeframe prices
    p5 = await market_data_service.get_5min_prices(symbol, 100)
    p15 = await market_data_service._get_klines(symbol, '15m', 60)
    p1h = await market_data_service._get_klines(symbol, '1h', 48)
    p1d = await market_data_service.get_historical_prices(symbol, 60)

    # Generate signals + indicators for each timeframe
    sig5 = TradingSignals.generate_signal(p5, price) if p5 and len(p5) >= 20 else None
    sig15 = TradingSignals.generate_signal(p15, price) if p15 and len(p15) >= 20 else None
    sig1h = TradingSignals.generate_signal(p1h, price) if p1h and len(p1h) >= 20 else None
    daily_price = p1d[-1] if p1d and len(p1d) > 0 else price
    sig1d = TradingSignals.generate_signal(p1d, daily_price) if p1d and len(p1d) >= 20 else None

    def _pick(sig, key):
        return sig.get(key) if sig else None

    mtf = {
        "5m": {
            "signal": _pick(sig5, "signal"),
            "confidence": _pick(sig5, "confidence"),
            "supertrend": _pick(_pick(sig5, "indicators"), "supertrend"),
            "ichimoku": _pick(_pick(sig5, "indicators"), "ichimoku"),
            "rsi": _pick(_pick(sig5, "indicators"), "rsi"),
            "macd": _pick(_pick(sig5, "indicators"), "macd"),
        },
        "15m": {
            "signal": _pick(sig15, "signal"),
            "confidence": _pick(sig15, "confidence"),
            "supertrend": _pick(_pick(sig15, "indicators"), "supertrend"),
            "ichimoku": _pick(_pick(sig15, "indicators"), "ichimoku"),
            "rsi": _pick(_pick(sig15, "indicators"), "rsi"),
        },
        "1h": {
            "signal": _pick(sig1h, "signal"),
            "confidence": _pick(sig1h, "confidence"),
            "supertrend": _pick(_pick(sig1h, "indicators"), "supertrend"),
            "ichimoku": _pick(_pick(sig1h, "indicators"), "ichimoku"),
            "rsi": _pick(_pick(sig1h, "indicators"), "rsi"),
        },
        "1d": {
            "signal": _pick(sig1d, "signal"),
            "confidence": _pick(sig1d, "confidence"),
            "supertrend": _pick(_pick(sig1d, "indicators"), "supertrend"),
            "ichimoku": _pick(_pick(sig1d, "indicators"), "ichimoku"),
            "rsi": _pick(_pick(sig1d, "indicators"), "rsi"),
        },
    }

    explanation = None
    if sig5:
        explanation = signal_explainer.explain(
            symbol.upper(),
            sig5.get("signal", "HOLD"),
            sig5.get("confidence", 0),
            sig5.get("reasons", []),
            sig5.get("indicators", {}),
            mtf=mtf,
            price=price,
        )

    return {
        "status": "success",
        "symbol": symbol.upper(),
        "timeframe": "5m",
        "current_price": price,
        "mtf": mtf,
        "signal": _pick(sig5, "signal"),
        "confidence": _pick(sig5, "confidence"),
        "reasons": _pick(sig5, "reasons"),
        "indicators": _pick(sig5, "indicators"),
        "explanation": explanation,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/signals")
async def get_all_signals():
    """Get trading signals for all symbols."""
    results = {}

    for symbol in _MONITORED_SYMBOLS:
        try:
            price_data = await market_data_service.get_price_data(symbol)
            if price_data:
                prices_5m = await market_data_service.get_5min_prices(symbol, 100)
                signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])
                results[symbol] = {
                    "signal": signal_data['signal'],
                    "confidence": signal_data['confidence'],
                    "price": price_data['price']
                }
        except Exception:
            pass

    return results


@router.get("/realtime")
async def get_all_realtime():
    """Get real-time data for all supported symbols."""
    symbols = _MONITORED_SYMBOLS
    results = {}

    for symbol in symbols:
        try:
            data = await get_realtime_data(symbol)
            results[symbol] = data
        except Exception:
            pass

    return results
