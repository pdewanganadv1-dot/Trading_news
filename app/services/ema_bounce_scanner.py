import asyncio
import time
import yfinance as yf
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.data.stocks import INDIAN_STOCKS

_INDIAN = INDIAN_STOCKS

_scan_cache: List[Dict] = []
_scan_cache_ts: float = 0
_SCAN_TTL = 300

_yf_last_call_ts: float = 0


def _yf_ticker(symbol: str) -> str:
    return f"{symbol.upper()}.NS"


def _calc_ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = (v - ema) * multiplier + ema
    return ema


def _find_support_resistance(closes: List[float], lookback: int = 50) -> Tuple[Optional[float], Optional[float]]:
    if len(closes) < lookback:
        return None, None
    recent = closes[-lookback:]
    return min(recent), max(recent)


def _detect_bounce(symbol: str) -> Optional[Dict]:
    global _yf_last_call_ts
    ticker = _yf_ticker(symbol)

    now = time.time()
    since_last = now - _yf_last_call_ts
    if since_last < 1.0:
        time.sleep(1.0 - since_last)
    _yf_last_call_ts = time.time()

    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period="2d", interval="1m")
        if data.empty or len(data) < 210:
            return None
    except Exception:
        return None

    closes = data["Close"].values.tolist()
    highs = data["High"].values.tolist()
    lows = data["Low"].values.tolist()
    volumes = data["Volume"].values.tolist()

    ema200 = _calc_ema(closes, 200)
    if ema200 is None:
        return None

    last_price = closes[-1]
    prev_price = closes[-2] if len(closes) >= 2 else last_price
    change_pct = ((last_price - prev_price) / prev_price) * 100

    # Check position relative to EMA 200 for last N candles
    n_check = 6
    above_flags = []
    valid = True
    for i in range(n_check):
        idx = -(i + 1)
        if len(closes) + idx < 0:
            valid = False
            break
        sub_closes = closes[:idx] if idx != 0 else closes
        ema = _calc_ema(sub_closes, 200)
        if ema is None:
            valid = False
            break
        above_flags.append(closes[idx] > ema)

    if not valid or len(above_flags) < 3:
        return None

    last_above = above_flags[0]
    prev_above = above_flags[1]

    support, resistance = _find_support_resistance(closes)

    avg_vol = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else 1.0
    last_vol = volumes[-1] if volumes else 1
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1

    direction = None
    strength = 0.0
    reason = ""

    # BUY: was below for >=3 candles, now crosses above
    if not prev_above and last_above:
        candles_below = sum(1 for f in above_flags if not f)
        if candles_below >= 2:
            direction = "BUY"
            strength = abs(last_price - ema200) / ema200 * 100
            near_support = support is not None and abs(last_price - support) / support < 0.005
            if near_support:
                strength += 1.0
                reason += "Near support level. "
            if vol_ratio > 1.5:
                strength += 0.5
                reason += "Above avg volume. "
            reason += f"Bounced above EMA200 (₹{ema200:.2f}) after {candles_below}c below."

    # SELL: was above for >=3 candles, now crosses below
    elif prev_above and not last_above:
        candles_above = sum(1 for f in above_flags if f)
        if candles_above >= 2:
            direction = "SELL"
            strength = abs(last_price - ema200) / ema200 * 100
            near_resistance = resistance is not None and abs(last_price - resistance) / resistance < 0.005
            if near_resistance:
                strength += 1.0
                reason += "Near resistance level. "
            if vol_ratio > 1.5:
                strength += 0.5
                reason += "Above avg volume. "
            reason += f"Broke below EMA200 (₹{ema200:.2f}) after {candles_above}c above."

    if direction is None:
        return None

    return {
        "symbol": symbol.upper(),
        "direction": direction,
        "price": round(last_price, 2),
        "ema200": round(ema200, 2),
        "change_pct": round(change_pct, 2),
        "strength": round(strength, 2),
        "vol_ratio": round(vol_ratio, 2),
        "support": round(support, 2) if support else None,
        "resistance": round(resistance, 2) if resistance else None,
        "reason": reason.strip(),
        "timestamp": datetime.now().isoformat(),
    }


async def scan_for_bounces() -> List[Dict]:
    global _scan_cache, _scan_cache_ts
    now = time.time()
    if _scan_cache and (now - _scan_cache_ts) < _SCAN_TTL:
        return list(_scan_cache)

    loop = asyncio.get_event_loop()
    signals = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_detect_bounce, sym): sym for sym in _INDIAN}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    signals.append(result)
            except Exception:
                pass

    signals.sort(key=lambda x: x.get("strength", 0), reverse=True)
    _scan_cache = signals
    _scan_cache_ts = time.time()
    return signals


async def get_recent_bounces(min_strength: float = 0.3) -> List[Dict]:
    signals = await scan_for_bounces()
    return [s for s in signals if s.get("strength", 0) >= min_strength]
