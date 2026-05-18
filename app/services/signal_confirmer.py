import asyncio
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from app.services.market_data_service import market_data_service, TechnicalIndicators, TradingSignals
from app.services.market_edge_service import scan_stock, get_market_breadth, get_fii_dii_summary
from app.services.signal_monitor import _INDIAN_STOCKS
from app.services.real_news import real_news_service
from app.services.social_sentiment import fetch_stocktwits, fetch_reddit


async def get_multiframe_prices(symbol: str) -> Tuple[List[float], List[float], List[float], List[float]]:
    """Fetch 5m, 15m, 1h, 1d prices for multi-timeframe analysis."""
    prices_5m = await market_data_service.get_5min_prices(symbol, 100)
    prices_15m = await market_data_service._get_klines(symbol, '15m', 60)
    prices_1h = await market_data_service._get_klines(symbol, '1h', 48)
    prices_1d = await market_data_service.get_historical_prices(symbol, 30)
    return prices_5m, prices_15m, prices_1h, prices_1d


def _tf_signal(prices: List[float], current_price: float) -> Tuple[str, float, List[str]]:
    """Get signal for a single timeframe. Returns (signal, confidence, reasons)."""
    if not prices or len(prices) < 20:
        return ("HOLD", 0, ["Insufficient data"])
    sig = TradingSignals.generate_signal(prices, current_price)
    return (sig["signal"], sig["confidence"], sig.get("reasons", []))


async def confirm_signal(
    symbol: str,
    base_signal: str,
    base_confidence: float,
    base_reasons: List[str],
    price: float,
) -> Dict:
    """
    Multi-conformation signal check.
    Returns composite signal with adjusted confidence.
    """
    symbol_lower = symbol.lower()

    # 1. Multi-timeframe alignment
    tf_signals = {}
    try:
        p5, p15, p1h, p1d = await get_multiframe_prices(symbol_lower)
        tf_signals["5m"] = _tf_signal(p5, price) if p5 and len(p5) >= 20 else ("HOLD", 0, [])
        tf_signals["15m"] = _tf_signal(p15, price) if p15 and len(p15) >= 20 else ("HOLD", 0, [])
        tf_signals["1h"] = _tf_signal(p1h, price) if p1h and len(p1h) >= 20 else ("HOLD", 0, [])
        # 1d uses daily close as current price
        daily_price = p1d[-1] if p1d is not None and len(p1d) > 0 else price
        tf_signals["1d"] = _tf_signal(p1d, daily_price) if p1d and len(p1d) >= 20 else ("HOLD", 0, [])
    except Exception as e:
        print(f"MTF error for {symbol}: {e}")

    # 2. Edge score (volume + levels + RSI context)
    edge = None
    try:
        edge = scan_stock(symbol_lower)
    except Exception:
        pass

    # 3. Market breadth regime
    breadth = None
    try:
        breadth = await get_market_breadth()
    except Exception:
        pass

    # 4. News sentiment alignment
    news_sentiment = None
    try:
        news = await real_news_service.get_all_news()
        sentiment_data = real_news_service.get_market_sentiment(news)
        news_sentiment = sentiment_data.get("sentiment", "neutral")
    except Exception:
        pass

    # 5. Social sentiment
    social_direction = None
    try:
        stocktwits, reddit = await asyncio.gather(
            fetch_stocktwits(symbol_lower.upper()),
            fetch_reddit(symbol_lower.upper()),
            return_exceptions=True,
        )
        if isinstance(stocktwits, dict) and isinstance(reddit, dict):
            tw_bull = stocktwits.get("bullish", 0) or 0
            tw_bear = stocktwits.get("bearish", 0) or 0
            if tw_bull > 0 or tw_bear > 0:
                social_direction = "bullish" if tw_bull > tw_bear else "bearish"
    except Exception:
        pass

    # --- Composite scoring ---
    score = 0
    max_score = 0
    confirmations = []
    warnings = []

    # Base signal contribution
    max_score += 3
    if base_signal in ("BUY", "SELL"):
        base_dir = 1 if base_signal == "BUY" else -1
        score += base_dir * 3 * base_confidence
        confirmations.append(f"Base {base_signal} ({base_confidence:.0%})")

    # MTF alignment (3 timeframes: 5m, 15m, 1h)
    for tf_name, (tf_sig, tf_conf, tf_reasons) in tf_signals.items():
        max_score += 2
        if tf_sig == base_signal:
            score += base_dir * 2 * tf_conf
            confirmations.append(f"{tf_name} confirms {tf_sig}")
        elif tf_sig == "HOLD":
            score += 0  # Neutral is ok
        else:
            score -= base_dir * 1
            warnings.append(f"{tf_name} disagrees ({tf_sig})")

    # Volume confirmation from edge score
    if edge and not edge.get("error"):
        max_score += 2
        vol_ratio = edge.get("vol_ratio", 0) or 0
        vol_surge = bool(edge.get("vol_surge"))
        heavy_buy = bool(edge.get("heavy_buying"))
        heavy_sell = bool(edge.get("heavy_selling"))

        if base_signal == "BUY" and (vol_surge or heavy_buy):
            score += 2
            confirmations.append(f"Volume surge {vol_ratio}x")
        elif base_signal == "SELL" and (vol_surge or heavy_sell):
            score += 2
            confirmations.append(f"Volume surge {vol_ratio}x")
        elif vol_surge:
            score += 1
            confirmations.append(f"Volume {vol_ratio}x avg")

        # RSI agreement
        rsi = edge.get("rsi")
        if rsi is not None:
            if base_signal == "BUY" and rsi < 40:
                score += 1
                confirmations.append(f"RSI {rsi} supports BUY")
            elif base_signal == "SELL" and rsi > 60:
                score += 1
                confirmations.append(f"RSI {rsi} supports SELL")

    # Market breadth regime filter
    if breadth:
        pct_above = breadth.get("pct_above", 50)
        max_score += 2
        if pct_above > 60:
            score += 1
            confirmations.append("Market strong (>60% above SMA)")
        elif pct_above < 30:
            if base_signal == "SELL":
                score += 1
                confirmations.append("Weak market supports SELL")
            else:
                score -= 1
                warnings.append(f"Market weak ({pct_above}% above SMA)")
        elif pct_above < 20:
            score -= 1
            warnings.append(f"Market oversold ({pct_above}%)")

    # News sentiment alignment
    if news_sentiment:
        max_score += 1
        if (base_signal == "BUY" and news_sentiment == "bullish") or \
           (base_signal == "SELL" and news_sentiment == "bearish"):
            score += 1
            confirmations.append(f"News sentiment: {news_sentiment}")
        elif (base_signal == "BUY" and news_sentiment == "bearish") or \
             (base_signal == "SELL" and news_sentiment == "bullish"):
            score -= 1
            warnings.append(f"News sentiment conflicts ({news_sentiment})")

    # Social sentiment
    if social_direction:
        max_score += 1
        if (base_signal == "BUY" and social_direction == "bullish") or \
           (base_signal == "SELL" and social_direction == "bearish"):
            score += 1
            confirmations.append(f"Social sentiment: {social_direction}")
        else:
            score -= 1
            warnings.append(f"Social sentiment conflicts ({social_direction})")

    # 6. Combined FII + DII institutional flow (Indian stocks only)
    is_indian = symbol_lower in _INDIAN_STOCKS
    if is_indian:
        try:
            fiidii = await get_fii_dii_summary()
            fii_net = fiidii.get("fii_net")
            dii_net = fiidii.get("dii_net")
            if fii_net is not None and dii_net is not None:
                max_score += 2
                total_inst = fii_net + dii_net
                inst_bullish = total_inst > 0
                direction = "BUY" if inst_bullish else "SELL"
                if base_signal == direction:
                    score += 2
                    confirmations.append(
                        f"FII+DII net {direction} ({total_inst:+,.0f}Cr)"
                    )
                else:
                    score -= 1
                    warnings.append(
                        f"Inst. flow {direction} ({total_inst:+,.0f}Cr) conflicts with {base_signal}"
                    )
        except Exception as e:
            print(f"FII/DII check error for {symbol}: {e}")

    # --- Final composite ---
    if max_score == 0:
        composite_conf = 0
    else:
        # Normalize to 0-1 range and adjust base confidence
        normalized = score / max_score  # -1 to 1
        # Blend base confidence with composite
        composite_conf = 0.4 * base_confidence + 0.6 * max(0, normalized)

    if normalized >= 0.3:
        composite_signal = base_signal
    elif normalized <= -0.3:
        composite_signal = "SELL" if base_signal == "BUY" else "BUY" if base_signal == "SELL" else "HOLD"
    else:
        composite_signal = "HOLD"

    composite_conf = min(0.95, round(composite_conf, 2))
    if composite_signal == "HOLD":
        composite_conf = round(composite_conf * 0.5, 2)

    return {
        "signal": composite_signal,
        "confidence": composite_conf,
        "reasons": confirmations[:4] + warnings[:2],
        "confirmations": confirmations,
        "warnings": warnings,
        "composite_score": round(normalized, 2),
        "mtf": {k: {"signal": v[0], "confidence": v[1]} for k, v in tf_signals.items()},
    }
