import asyncio
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

_INDIAN = [
    "reliance", "tcs", "hdfcbank", "infy", "icicibank",
    "tatamotors", "sbin", "lt", "wipro", "itc",
    "bhartiartl", "maruti", "nestleind", "hindunilvr", "asianpaint",
    "sunpharma", "titan", "bajajfinance", "hcltech", "kotakbank",
    "axisbank", "ntpc", "tatasteel", "cipla", "ultratech",
]

_CRYPTO = ["btc", "eth"]

# Cached results
_breadth_cache = {}
_breadth_cache_ts = 0
_BREADTH_TTL = 3600  # 1 hour


def _yf_ticker(symbol: str) -> Optional[str]:
    s = symbol.lower()
    if s in _CRYPTO:
        return None
    if s in _INDIAN:
        return f"{s.upper()}.NS"
    return None


def _get_yf_price(symbol: str):
    """Get daily price & volume data from yfinance (synchronous helper)."""
    ticker = _yf_ticker(symbol)
    if not ticker:
        return None
    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period="1mo")
        if data.empty:
            return None
        return data
    except Exception:
        return None


def _analyze_volume(data) -> Dict:
    """Analyze volume patterns from yfinance daily data."""
    if data is None or len(data) < 5:
        return {}
    last = data.iloc[-1]
    avg_vol = data["Volume"].rolling(20).mean().iloc[-1]
    vol_ratio = last["Volume"] / avg_vol if avg_vol > 0 else 1
    price = last["Close"]
    prev_price = data["Close"].iloc[-2] if len(data) > 1 else price
    change_pct = ((price - prev_price) / prev_price) * 100

    # Volume surge (buying or selling)
    surge = vol_ratio > 1.5
    # Heavy volume selling (price down + volume surge)
    heavy_selling = surge and change_pct < -1
    # Heavy volume buying (price up + volume surge)
    heavy_buying = surge and change_pct > 1
    # Volume divergence (price up but volume declining over 5 days)
    recent_vols = data["Volume"].iloc[-5:].values
    vol_trend = "rising" if len(recent_vols) > 1 and recent_vols[-1] > recent_vols[0] else "declining"
    price_up_vol_down = change_pct > 0 and vol_trend == "declining"

    return {
        "price": round(price, 2),
        "change_pct": round(change_pct, 2),
        "volume": int(last["Volume"]),
        "avg_volume": int(avg_vol),
        "vol_ratio": round(vol_ratio, 2),
        "vol_surge": bool(surge),
        "heavy_buying": bool(heavy_buying),
        "heavy_selling": bool(heavy_selling),
        "price_up_vol_down": bool(price_up_vol_down),
    }


def _analyze_streaks(data) -> Dict:
    """Detect consecutive winning/losing days."""
    if data is None or len(data) < 3:
        return {}
    closes = data["Close"].values
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    streak = 0
    for c in reversed(changes):
        if streak == 0:
            streak = 1 if c > 0 else -1 if c < 0 else 0
        elif (streak > 0 and c > 0) or (streak < 0 and c < 0):
            streak += 1 if streak > 0 else -1
        else:
            break

    return {
        "streak_days": abs(streak),
        "streak_direction": "bullish" if streak > 0 else "bearish" if streak < 0 else "flat",
    }


def _analyze_levels(data) -> Dict:
    """Find key support/resistance levels."""
    if data is None or len(data) < 10:
        return {}
    last = data.iloc[-1]
    price = last["Close"]
    high_20 = data["High"].tail(20).max()
    low_20 = data["Low"].tail(20).min()
    high_52w = data["High"].max()
    low_52w = data["Low"].min()

    return {
        "price": round(price, 2),
        "high_20d": round(high_20, 2),
        "low_20d": round(low_20, 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "pct_from_20d_high": round(((price / high_20) - 1) * 100, 2),
        "pct_from_20d_low": round(((price / low_20) - 1) * 100, 2),
        "near_20d_high": bool(price >= high_20 * 0.98),
        "near_20d_low": bool(price <= low_20 * 1.02),
    }


def _analyze_rsi(data) -> Dict:
    """RSI divergence detection on daily timeframe."""
    if data is None or len(data) < 20:
        return {}
    closes = data["Close"].values
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))

    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14
    if avg_loss == 0:
        rsi = 100
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    # Check divergence: compare last 5 vs previous 5
    recent_prices = closes[-5:]
    prev_prices = closes[-10:-5]
    recent_rsi_val = rsi  # simplified
    prev_rsi_val = 50  # placeholder

    return {
        "rsi": round(rsi, 1),
        "oversold": rsi < 35,
        "overbought": rsi > 75,
    }


def scan_stock(symbol: str) -> Dict:
    """Full edge scan for a single stock."""
    data = _get_yf_price(symbol)
    if data is None:
        return {"symbol": symbol.upper(), "error": "No data"}

    vol = _analyze_volume(data)
    streaks = _analyze_streaks(data)
    levels = _analyze_levels(data)
    rsi_data = _analyze_rsi(data)

    # Composite edge score (0-10)
    score = 5
    signals = []

    if vol.get("heavy_buying"):
        score += 2
        signals.append("🚀 Heavy volume buying")
    if vol.get("heavy_selling"):
        score -= 2
        signals.append("⚠️ Heavy volume selling")
    if vol.get("vol_surge") and not vol.get("heavy_buying") and not vol.get("heavy_selling"):
        signals.append(f"📊 Volume {vol['vol_ratio']}x avg")
    if vol.get("price_up_vol_down"):
        score -= 1
        signals.append("⚡ Price up, volume declining (weak)")
    if streaks.get("streak_days", 0) >= 3:
        if streaks["streak_direction"] == "bullish":
            score += 1
            signals.append(f"📈 {streaks['streak_days']} green days")
        else:
            score -= 1
            signals.append(f"📉 {streaks['streak_days']} red days")
    if levels.get("near_20d_high"):
        score += 1
        signals.append(f"🎯 Near 20d high ({levels['pct_from_20d_high']}%)")
    if levels.get("near_20d_low"):
        score -= 1
        signals.append(f"⚠️ Near 20d low ({levels['pct_from_20d_low']}%)")
    if rsi_data.get("oversold"):
        score += 1
        signals.append("🔄 RSI oversold — bounce play")
    if rsi_data.get("overbought"):
        score -= 1
        signals.append("🔄 RSI overbought — caution")

    def _py(val):
        if isinstance(val, (np.bool_,)):
            return bool(val)
        if isinstance(val, (np.floating,)):
            return float(val)
        if isinstance(val, (np.integer,)):
            return int(val)
        return val

    import numpy as np

    return {
        "symbol": symbol.upper(),
        "price": _py(vol.get("price")),
        "change_pct": _py(vol.get("change_pct")),
        "score": _py(max(0, min(10, score))),
        "vol_ratio": _py(vol.get("vol_ratio")),
        "rsi": _py(rsi_data.get("rsi")),
        "near_high": _py(levels.get("near_20d_high")),
        "near_low": _py(levels.get("near_20d_low")),
        "streak": streaks.get("streak_direction"),
        "streak_days": _py(streaks.get("streak_days")),
        "signals": signals[:3],
        "error": None,
    }


async def scan_all_stocks() -> List[Dict]:
    """Scan all monitored stocks and return ranked by edge score."""
    loop = asyncio.get_event_loop()
    results = []
    for sym in _INDIAN:
        try:
            result = await loop.run_in_executor(None, scan_stock, sym)
            if result.get("error") is None:
                results.append(result)
        except Exception as e:
            print(f"Edge scan error {sym}: {e}")
        await asyncio.sleep(0.1)  # Rate limit
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


async def get_market_breadth() -> Dict:
    """Market breadth: percentage of Nifty 100 stocks above 20-day SMA."""
    global _breadth_cache, _breadth_cache_ts
    now = datetime.now().timestamp()
    if _breadth_cache and (now - _breadth_cache_ts) < _BREADTH_TTL:
        return _breadth_cache

    loop = asyncio.get_event_loop()
    above = 0
    total = 0
    for sym in _INDIAN:
        try:
            data = await loop.run_in_executor(None, _get_yf_price, sym)
            if data is not None and len(data) >= 20:
                sma20 = data["Close"].tail(20).mean()
                last = data["Close"].iloc[-1]
                if last > sma20:
                    above += 1
                total += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)

    result = {
        "above_sma20": above,
        "total": total,
        "pct_above": round((above / total * 100), 1) if total else 0,
        "timestamp": datetime.now().isoformat(),
    }
    _breadth_cache = result
    _breadth_cache_ts = now
    return result


_LAST_FII_DII = {
    "fii_buy": None,
    "fii_sell": None,
    "fii_net": None,
    "dii_buy": None,
    "dii_sell": None,
    "dii_net": None,
    "date": None,
    "source": "manual",
}

# Try to fetch from Investing.com India or similar
async def _try_fetch_fii_dii():
    """Attempt to auto-fetch FII/DII data (may fail, non-critical)."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            resp = await c.get(
                "https://www.nseindia.com/api/cot?index=FII",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                    "Referer": "https://www.nseindia.com/",
                },
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return None


async def get_fii_dii_summary() -> Dict:
    """Get FII/DII summary (auto-fetched or manually set)."""
    global _LAST_FII_DII
    data = await _try_fetch_fii_dii()
    if data:
        _LAST_FII_DII["auto_fetched"] = True
        _LAST_FII_DII["date"] = datetime.now().strftime("%Y-%m-%d")
    return _LAST_FII_DII


def set_fii_dii(fii_buy: float, fii_sell: float, dii_buy: float, dii_sell: float):
    """Manually set FII/DII data (via Telegram command)."""
    global _LAST_FII_DII
    _LAST_FII_DII = {
        "fii_buy": fii_buy,
        "fii_sell": fii_sell,
        "fii_net": round(fii_buy - fii_sell, 2),
        "dii_buy": dii_buy,
        "dii_sell": dii_sell,
        "dii_net": round(dii_buy - dii_sell, 2),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "source": "manual",
    }
    return _LAST_FII_DII
