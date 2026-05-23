"""
Market Structure Analysis — SMC/ICT concepts with numba JIT.
Provides swing point detection, liquidity sweeps, BOS/CHoCH,
trendline bounce, FVG, and order block identification.
"""
import numpy as np
from numba import jit


# ─── Swing Point Detection ──────────────────────────────────────────

@jit(nopython=True)
def swing_highs(h, left=3, right=3):
    """Identify pivot highs: bar i where h[i] is highest in [i-left, i+right].
    Returns array: 1 = swing high, 0 = otherwise."""
    n = len(h)
    result = np.zeros(n)
    for i in range(left, n - right):
        is_high = True
        for j in range(i - left, i + right + 1):
            if j == i:
                continue
            if h[j] >= h[i]:
                is_high = False
                break
        if is_high:
            result[i] = 1
    return result


@jit(nopython=True)
def swing_lows(l, left=3, right=3):
    """Identify pivot lows: bar i where l[i] is lowest in [i-left, i+right].
    Returns array: 1 = swing low, 0 = otherwise."""
    n = len(l)
    result = np.zeros(n)
    for i in range(left, n - right):
        is_low = True
        for j in range(i - left, i + right + 1):
            if j == i:
                continue
            if l[j] <= l[i]:
                is_low = False
                break
        if is_low:
            result[i] = 1
    return result


# ─── Liquidity Sweep ────────────────────────────────────────────────

@jit(nopython=True)
def liquidity_sweep(o, h, l, c, lookback=10):
    """Detect liquidity sweeps (stop hunts).
    Bullish sweep: price dips below recent swing low, closes back above.
    Bearish sweep: price spikes above recent swing high, closes back below.
    Returns: 1 = bullish sweep, -1 = bearish sweep, 0 = none."""
    n = len(c)
    result = np.zeros(n)
    if n < lookback + 5:
        return result

    for i in range(lookback + 1, n):
        # Find recent swing high and low in [i-lookback, i-1]
        recent_high = h[i - lookback]
        recent_low = l[i - lookback]
        for j in range(i - lookback, i):
            recent_high = max(recent_high, h[j])
            recent_low = min(recent_low, l[j])

        # Bearish sweep: price goes above recent high, closes below it
        if h[i] > recent_high and c[i] < recent_high:
            result[i] = -1

        # Bullish sweep: price goes below recent low, closes above it
        if l[i] < recent_low and c[i] > recent_low:
            result[i] = 1

    return result


# ─── Market Structure: BOS / CHoCH ─────────────────────────────────

@jit(nopython=True)
def market_structure(h, l, left=3, right=3):
    """Break of Structure (BOS) and Change of Character (CHoCH) detection.
    Tracks sequence of swing highs/lows to determine:
    BOS (bullish): price breaks above previous swing high in uptrend
    BOS (bearish): price breaks below previous swing low in downtrend
    CHoCH: structure shift (e.g., series of higher highs ends with lower low)
    Returns: 1 = bullish BOS, -1 = bearish BOS, 2 = bullish CHoCH, -2 = bearish CHoCH, 0 = none"""
    n = len(l)
    result = np.zeros(n)
    sh = swing_highs(h, left, right)
    sl = swing_lows(l, left, right)

    # Collect swing points
    swing_high_prices = []
    swing_high_indices = []
    swing_low_prices = []
    swing_low_indices = []

    for i in range(n):
        if sh[i] == 1:
            swing_high_prices.append(h[i])
            swing_high_indices.append(i)
        if sl[i] == 1:
            swing_low_prices.append(l[i])
            swing_low_indices.append(i)

    if len(swing_high_prices) < 3 or len(swing_low_prices) < 3:
        return result

    # Detect structure per bar
    for i in range(n):
        # Need at least 2 prior swing points
        high_idx = [idx for idx in swing_high_indices if idx < i]
        low_idx = [idx for idx in swing_low_indices if idx < i]

        if len(high_idx) < 2 or len(low_idx) < 2:
            continue

        # Get last 2 swing highs
        h1 = swing_high_prices[len(high_idx) - 2]
        h2 = swing_high_prices[len(high_idx) - 1]
        # Get last 2 swing lows
        l1 = swing_low_prices[len(low_idx) - 2]
        l2 = swing_low_prices[len(low_idx) - 1]

        # Uptrend: higher highs + higher lows
        uptrend = h2 > h1 and l2 > l1
        # Downtrend: lower highs + lower lows
        downtrend = h2 < h1 and l2 < l1

        # Check current price relative to structure
        if uptrend:
            # Bullish BOS: price breaks above last swing high
            if h[i] > h2 and h[i] > h[i-1] if i > 0 else True:
                result[i] = 1
        elif downtrend:
            # Bearish BOS: price breaks below last swing low
            if l[i] < l2 and l[i] < l[i-1] if i > 0 else True:
                result[i] = -1
        else:
            # CHoCH detection
            if h2 > h1 and l2 < l1:  # Higher high but lower low -> bearish CHoCH
                result[i] = -2
            elif h2 < h1 and l2 > l1:  # Lower high but higher low -> bullish CHoCH
                result[i] = 2

    return result


# ─── Fair Value Gap (FVG) ──────────────────────────────────────────

@jit(nopython=True)
def fair_value_gap(o, h, l, c):
    """Detect Fair Value Gaps (3-candle imbalance).
    Bullish FVG: low[i] > high[i-2] (gap between consecutive candles)
    Bearish FVG: high[i] < low[i-2]
    Returns: 1 = bullish FVG, -1 = bearish FVG, 0 = none."""
    n = len(c)
    result = np.zeros(n)
    if n < 3:
        return result
    for i in range(2, n):
        if l[i] > h[i - 2]:
            result[i] = 1  # Bullish FVG
        elif h[i] < l[i - 2]:
            result[i] = -1  # Bearish FVG
    return result


# ─── Order Blocks ──────────────────────────────────────────────────

@jit(nopython=True)
def order_blocks(o, h, l, c, v):
    """Detect Order Blocks (last candle before a strong impulse move).
    A bullish order block is the last bearish (or neutral) candle before
    a strong bullish impulse (close > open, body > avg_body * 1.5).
    A bearish OB is the last bullish candle before a strong bearish impulse.
    Returns: 1 = bullish OB zone active, -1 = bearish OB zone active, 0 = none."""
    n = len(c)
    result = np.zeros(n)
    if n < 5:
        return result

    for i in range(3, n - 1):
        # Impulse candle criteria
        body = abs(c[i] - o[i])
        body_pct = body / (o[i] + 1e-10) * 100

        if body_pct < 0.3:  # Not an impulse candle
            continue

        prev_body = abs(c[i-1] - o[i-1])

        # Bullish impulse
        if c[i] > o[i] and body > prev_body * 1.5:
            # Previous candle base is the order block
            ob_high = max(o[i-1], c[i-1])
            ob_low = min(o[i-1], c[i-1])
            # Check if price returns into OB zone on subsequent bars
            for j in range(i + 1, min(i + 5, n)):
                if l[j] <= ob_high and h[j] >= ob_low:
                    result[j] = 1
                    break

        # Bearish impulse
        elif c[i] < o[i] and body > prev_body * 1.5:
            ob_high = max(o[i-1], c[i-1])
            ob_low = min(o[i-1], c[i-1])
            for j in range(i + 1, min(i + 5, n)):
                if l[j] <= ob_high and h[j] >= ob_low:
                    result[j] = -1
                    break

    return result


# ─── Trendline Bounce ──────────────────────────────────────────────

@jit(nopython=True)
def _line_value(x1, y1, x2, y2, x):
    """Calculate y value on line at x."""
    if x2 == x1:
        return y1
    return y1 + (y2 - y1) * (x - x1) / (x2 - x1)


@jit(nopython=True)
def trendline_bounce(h, l, c, lookback=15, touch_tolerance=0.001):
    """Detect price bouncing off trendlines.
    Uptrend line: connects 2 swing lows, price touches it and bounces.
    Downtrend line: connects 2 swing highs, price touches it and bounces.
    Returns: 1 = bullish bounce, -1 = bearish bounce, 0 = none."""
    n = len(l)
    result = np.zeros(n)
    if n < lookback:
        return result

    sl = swing_lows(l, 2, 2)
    sh = swing_highs(h, 2, 2)

    for i in range(lookback, n):
        recent_low_indices = []
        recent_low_prices = []
        recent_high_indices = []
        recent_high_prices = []

        for j in range(i - lookback, i):
            if sl[j] == 1:
                recent_low_indices.append(j)
                recent_low_prices.append(l[j])
            if sh[j] == 1:
                recent_high_indices.append(j)
                recent_high_prices.append(h[j])

        # Uptrend line bounce (connect 2 swing lows)
        if len(recent_low_indices) >= 2:
            x1, x2 = recent_low_indices[-2], recent_low_indices[-1]
            y1, y2 = recent_low_prices[-2], recent_low_prices[-1]
            if x2 > x1 and y2 >= y1:
                expected = _line_value(x1, y1, x2, y2, i)
                touch_range = expected * touch_tolerance
                if abs(l[i] - expected) <= touch_range and c[i] > expected:
                    result[i] = 1

        # Downtrend line bounce (connect 2 swing highs)
        if len(recent_high_indices) >= 2:
            x1, x2 = recent_high_indices[-2], recent_high_indices[-1]
            y1, y2 = recent_high_prices[-2], recent_high_prices[-1]
            if x2 > x1 and y2 <= y1:
                expected = _line_value(x1, y1, x2, y2, i)
                touch_range = expected * touch_tolerance
                if abs(h[i] - expected) <= touch_range and c[i] < expected:
                    result[i] = -1

    return result


# ─── Composite Direction ────────────────────────────────────────────

def structure_direction(o, h, l, c, v, lookback=10):
    """Combine all structure signals into a single direction series.
    Returns numpy array: 1=LONG, -1=SHORT, 0=NEUTRAL."""
    n = len(h)
    dirs = np.zeros(n)

    liq = liquidity_sweep(o, h, l, c, lookback)
    ms = market_structure(h, l, 3, 3)
    fvg = fair_value_gap(o, h, l, c)
    ob = order_blocks(o, h, l, c, v)
    tl = trendline_bounce(h, l, c, 15)

    for i in range(n):
        score = 0.0
        if liq[i] == 1:
            score += 3
        elif liq[i] == -1:
            score -= 3
        if ms[i] == 1:
            score += 2
        elif ms[i] == -1:
            score -= 2
        elif ms[i] == 2:
            score += 1.5
        elif ms[i] == -2:
            score -= 1.5
        if fvg[i] == 1:
            score += 1
        elif fvg[i] == -1:
            score -= 1
        if ob[i] == 1:
            score += 1.5
        elif ob[i] == -1:
            score -= 1.5
        if tl[i] == 1:
            score += 2
        elif tl[i] == -1:
            score -= 2

        if score >= 2:
            dirs[i] = 1
        elif score <= -2:
            dirs[i] = -1

    return dirs


