"""
DIY Custom Strategy Builder [ZP] - v1
Python port of TradingView Pine Script strategy builder.
Computes 37+ leading indicators, 30+ confirmation filters, and
generates combined BUY/SELL signals from live WebSocket feed data.
"""
import asyncio
import json
import math
import sqlite3
import os
import time
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Callable

from app.config import settings
from app.services.ohlc_builder import ohlc_builder

DB_PATH = os.path.join(settings.persistent_dir, 'strategy_signals.db')

# ─── Database ───────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            leading_name TEXT,
            leading_direction TEXT,
            confirmations TEXT,
            confirmation_count INTEGER,
            signal_threshold INTEGER,
            final_signal TEXT,
            price REAL,
            expiry_bars INTEGER,
            metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_state (
            symbol TEXT PRIMARY KEY,
            active_signal TEXT,
            entry_price REAL,
            entry_time TEXT,
            bars_held INTEGER DEFAULT 0,
            leading_direction TEXT
        )
    """)
    conn.commit()
    return conn


def _save_signal(symbol: str, leading_name: str, leading_dir: str,
                 confirmations: list, conf_count: int, threshold: int,
                 final_signal: str, price: float, expiry_bars: int, meta: dict = None):
    conn = _get_db()
    conn.execute(
        """INSERT INTO strategy_signals
           (symbol, timestamp, leading_name, leading_direction, confirmations,
            confirmation_count, signal_threshold, final_signal, price, expiry_bars, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (symbol.upper(), datetime.now().isoformat(), leading_name, leading_dir,
         json.dumps(confirmations), conf_count, threshold, final_signal,
         price, expiry_bars, json.dumps(meta or {})),
    )
    conn.commit()
    conn.close()


def _get_active_state(symbol: str) -> Optional[Dict]:
    conn = _get_db()
    row = conn.execute("SELECT * FROM strategy_state WHERE symbol = ?", (symbol.upper(),)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def _set_active_state(symbol: str, signal: str, price: float, leading_dir: str):
    conn = _get_db()
    conn.execute(
        """INSERT OR REPLACE INTO strategy_state
           (symbol, active_signal, entry_price, entry_time, bars_held, leading_direction)
           VALUES (?, ?, ?, ?, 0, ?)""",
        (symbol.upper(), signal, price, datetime.now().isoformat(), leading_dir),
    )
    conn.commit()
    conn.close()


def _clear_active_state(symbol: str):
    conn = _get_db()
    conn.execute("DELETE FROM strategy_state WHERE symbol = ?", (symbol.upper(),))
    conn.commit()
    conn.close()


def _increment_bars_held(symbol: str):
    conn = _get_db()
    conn.execute("UPDATE strategy_state SET bars_held = bars_held + 1 WHERE symbol = ?", (symbol.upper(),))
    conn.commit()
    conn.close()


# ─── 1-Minute OHLC Bars (delegated to ohlc_builder) ─────────────────

def get_ohlc_bars(symbol: str, min_bars: int = 20):
    """Get 1-minute OHLC bar data for a symbol from the live OHLC builder."""
    return ohlc_builder.to_lists(symbol, min_bars=min_bars)


# ─── Utility Functions ──────────────────────────────────────────────

def ema(values: List[float], period: int) -> List[float]:
    """Exponential Moving Average."""
    if not values or period < 1:
        return []
    result = []
    k = 2.0 / (period + 1)
    ema_val = values[0]
    for v in values:
        ema_val = v * k + ema_val * (1 - k)
        result.append(ema_val)
    return result


def sma(values: List[float], period: int) -> List[float]:
    """Simple Moving Average."""
    if not values or period < 1:
        return []
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(values[i])
        else:
            result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def wma(values: List[float], period: int) -> List[float]:
    """Weighted Moving Average (linear weights)."""
    if not values or period < 1:
        return []
    result = []
    wsum = period * (period + 1) / 2
    for i in range(len(values)):
        if i < period - 1:
            result.append(values[i])
        else:
            window = values[i - period + 1:i + 1]
            weighted = sum(w * (j + 1) for j, w in enumerate(window))
            result.append(weighted / wsum)
    return result


def rma(values: List[float], period: int) -> List[float]:
    """Moving average used by RSI (Wilder smoothing)."""
    if not values or period < 1:
        return []
    result = []
    alpha = 1.0 / period
    rma_val = values[0]
    for v in values:
        rma_val = v * alpha + rma_val * (1 - alpha)
        result.append(rma_val)
    return result


def highest(values: List[float], period: int, offset: int = 0) -> float:
    """Highest value over the last `period` bars."""
    start = len(values) - period - offset
    if start < 0:
        start = 0
    end = len(values) - offset
    if end <= start:
        return 0.0
    return max(values[start:end])


def lowest(values: List[float], period: int, offset: int = 0) -> float:
    start = len(values) - period - offset
    if start < 0:
        start = 0
    end = len(values) - offset
    if end <= start:
        return 0.0
    return min(values[start:end])


def stdev(values: List[float], period: int) -> List[float]:
    """Standard deviation over period."""
    if not values or period < 1:
        return []
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(0.0)
        else:
            window = values[i - period + 1:i + 1]
            avg = sum(window) / period
            variance = sum((x - avg) ** 2 for x in window) / period
            result.append(math.sqrt(variance))
    return result


def linreg(values: List[float], period: int) -> List[float]:
    """Linear regression forecast (last value)."""
    if len(values) < period:
        return values
    x = list(range(period))
    x_sum = sum(x)
    x2_sum = sum(xi ** 2 for xi in x)
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(values[i])
        else:
            window = values[i - period + 1:i + 1]
            y_sum = sum(window)
            xy_sum = sum(x[j] * window[j] for j in range(period))
            slope = (period * xy_sum - x_sum * y_sum) / (period * x2_sum - x_sum ** 2)
            intercept = (y_sum - slope * x_sum) / period
            result.append(intercept + slope * (period - 1))
    return result


def roc(values: List[float], period: int) -> List[float]:
    """Rate of Change."""
    if len(values) < period:
        return [0.0] * len(values)
    result = []
    for i in range(len(values)):
        if i < period:
            result.append(0.0)
        elif values[i - period] != 0:
            result.append((values[i] - values[i - period]) / values[i - period] * 100)
        else:
            result.append(0.0)
    return result


def cross_over(a: List[float], b: List[float]) -> bool:
    """Did `a` cross above `b` on the most recent bar?"""
    if len(a) < 2 or len(b) < 2:
        return False
    return a[-1] > b[-1] and a[-2] <= b[-2]


def cross_under(a: List[float], b: List[float]) -> bool:
    if len(a) < 2 or len(b) < 2:
        return False
    return a[-1] < b[-1] and a[-2] >= b[-2]


def true_range(o: float, h: float, l: float, c: float, prev_c: float) -> float:
    return max(h - l, abs(h - prev_c), abs(l - prev_c))


def atr(highs: List[float], lows: List[float], closes: List[float], period: int) -> List[float]:
    """Average True Range."""
    if len(closes) < 2:
        return [0.0] * len(closes)
    tr_values = []
    for i in range(len(closes)):
        if i == 0:
            tr_values.append(highs[i] - lows[i])
        else:
            tr_values.append(true_range(0, highs[i], lows[i], closes[i], closes[i - 1]))
    return rma(tr_values, period)


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def nz(val: float, default: float = 0.0) -> float:
    return val if val == val and val is not None else default


# ─── Leading Indicators (37 total) ──────────────────────────────────

# Each leading indicator function receives (o, h, l, c, v) as lists
# and returns a dict with at minimum:
#   {"direction": "LONG" | "SHORT" | "NEUTRAL", "value": float, ...}

def leading_speedyRange(o, h, l, c, v, period=14, mult=1.5):
    """Speedy Range Filter (faster variant, shorter period & mult)."""
    return leading_rangefilter(o, h, l, c, v, period, mult)


def leading_rangefilter(o, h, l, c, v, period=20, mult=2.0):
    """Range Filter (Jurik-inspired)."""
    if len(c) < period + 5:
        return {"direction": "NEUTRAL", "value": 0}
    smooth = ema(c, period // 2 if period // 2 >= 2 else 2)
    if not smooth:
        return {"direction": "NEUTRAL", "value": 0}
    hi_band = [s + mult * stdev(c, period)[i] if i < len(stdev(c, period)) else s for i, s in enumerate(smooth)]
    lo_band = [s - mult * stdev(c, period)[i] if i < len(stdev(c, period)) else s for i, s in enumerate(smooth)]
    rng = [hi_band[i] - lo_band[i] for i in range(len(smooth))]
    rngf = ema(rng, period)
    if not rngf:
        return {"direction": "NEUTRAL", "value": 0}
    filt = [c[0]]
    for i in range(1, len(c)):
        if i >= len(rngf):
            filt.append(filt[-1])
            continue
        r = rngf[i]
        if abs(c[i] - filt[-1]) > r:
            filt.append(c[i])
        else:
            filt.append(filt[-1])
    direction = "LONG" if len(filt) >= 2 and c[-1] > filt[-1] else "SHORT" if len(filt) >= 2 and c[-1] < filt[-1] else "NEUTRAL"
    return {"direction": direction, "value": (c[-1] - filt[-1]) / (rngf[-1] + 1e-10) if len(filt) >= 2 else 0,
            "filter": filt[-1] if filt else 0}


def leading_superTrend(o, h, l, c, v, period=10, mult=3.0):
    """SuperTrend indicator."""
    if len(c) < period + 2:
        return {"direction": "NEUTRAL", "value": 0}
    atr_vals = atr(h, l, c, period)
    if not atr_vals:
        return {"direction": "NEUTRAL", "value": 0}
    hl2 = [(h[i] + l[i]) / 2 for i in range(len(c))]
    up = [hl2[i] - mult * atr_vals[i] for i in range(len(c))]
    dn = [hl2[i] + mult * atr_vals[i] for i in range(len(c))]
    trend_up = [True] * len(c)
    trend_dn = [False] * len(c)
    st_up = [0.0] * len(c)
    st_dn = [0.0] * len(c)
    for i in range(1, len(c)):
        st_up[i] = up[i] if up[i] > st_up[i - 1] or c[i - 1] <= st_up[i - 1] else st_up[i - 1]
        st_dn[i] = dn[i] if dn[i] < st_dn[i - 1] or c[i - 1] >= st_dn[i - 1] else st_dn[i - 1]
        trend_up[i] = c[i] > st_up[i]
        trend_dn[i] = c[i] < st_dn[i]
    direction = "LONG" if trend_up[-1] else "SHORT" if trend_dn[-1] else "NEUTRAL"
    super_val = st_up[-1] if trend_up[-1] else st_dn[-1] if trend_dn[-1] else 0
    return {"direction": direction, "value": (c[-1] - super_val) / (c[-1] + 1e-10), "supertrend": super_val}


def leading_halfTrend(o, h, l, c, v, amplitude=2, channel_dev=1, atr_period=3):
    """HalfTrend indicator."""
    if len(c) < 10:
        return {"direction": "NEUTRAL", "value": 0}
    atr_vals = atr(h, l, c, atr_period)
    if not atr_vals:
        return {"direction": "NEUTRAL", "value": 0}
    trend = "up"
    next_trend = "up"
    max_low = l[0]
    min_high = h[0]
    up_trend_1 = 0.0
    down_trend_1 = 0.0
    up_trend_2 = 0.0
    down_trend_2 = 0.0
    trend_line = [0.0] * len(c)
    for i in range(len(c)):
        if i == 0:
            continue
        if next_trend == "up":
            if c[i] < down_trend_1 and trend == "up":
                trend = "down"
                next_trend = "down"
                min_high = h[i]
        else:
            if c[i] > up_trend_1 and trend == "down":
                trend = "up"
                next_trend = "up"
                max_low = l[i]
        if i == 1:
            if trend == "up":
                up_trend_1 = c[i] - amplitude * atr_vals[i]
                down_trend_1 = c[i] + amplitude * atr_vals[i]
            else:
                up_trend_1 = c[i] - amplitude * atr_vals[i]
                down_trend_1 = c[i] + amplitude * atr_vals[i]
        if trend == "up":
            max_low = max(max_low, l[i])
            up_trend_1 = max(up_trend_1, c[i] - amplitude * atr_vals[i])
            up_trend_2 = up_trend_1
            down_trend_1 = c[i] + amplitude * atr_vals[i]
            down_trend_2 = down_trend_1
            trend_line[i] = up_trend_2
        else:
            min_high = min(min_high, h[i])
            down_trend_1 = min(down_trend_1, c[i] + amplitude * atr_vals[i])
            down_trend_2 = down_trend_1
            up_trend_1 = c[i] - amplitude * atr_vals[i]
            up_trend_2 = up_trend_1
            trend_line[i] = down_trend_2
    direction = "LONG" if trend == "up" else "SHORT" if trend == "down" else "NEUTRAL"
    return {"direction": direction, "value": (c[-1] - trend_line[-1]) / (c[-1] + 1e-10), "trend_line": trend_line[-1]}


def leading_rsi(o, h, l, c, v, period=14):
    """RSI V2."""
    if len(c) < period + 2:
        return {"direction": "NEUTRAL", "value": 50}
    changes = [c[i] - c[i - 1] for i in range(1, len(c))]
    gains = [max(ch, 0) for ch in changes]
    losses = [max(-ch, 0) for ch in changes]
    avg_gain = rma(gains, period)
    avg_loss = rma(losses, period)
    if not avg_gain or not avg_loss:
        return {"direction": "NEUTRAL", "value": 50}
    rs = [ag / (al + 1e-10) for ag, al in zip(avg_gain, avg_loss)]
    rsi_vals = [100 - 100 / (1 + r) for r in rs]
    rsi_val = rsi_vals[-1] if rsi_vals else 50
    direction = "LONG" if rsi_val > 50 else "SHORT" if rsi_val < 50 else "NEUTRAL"
    return {"direction": direction, "value": rsi_val, "series": rsi_vals}


def leading_stochastic(o, h, l, c, v, k_period=14, d_period=3):
    """Stochastic V2."""
    if len(c) < k_period + d_period:
        return {"direction": "NEUTRAL", "value": 50}
    k_vals = []
    for i in range(len(c)):
        if i < k_period - 1:
            k_vals.append(50.0)
        else:
            hi = max(h[i - k_period + 1:i + 1])
            lo = min(l[i - k_period + 1:i + 1])
            if hi != lo:
                k_vals.append((c[i] - lo) / (hi - lo) * 100)
            else:
                k_vals.append(50.0)
    d_vals = sma(k_vals, d_period) if len(k_vals) >= d_period else k_vals
    k = k_vals[-1] if k_vals else 50
    d = d_vals[-1] if d_vals else 50
    direction = "LONG" if k > d and k < 80 else "SHORT" if k < d and k > 20 else "NEUTRAL"
    return {"direction": direction, "value": k, "signal": d}


def leading_cci(o, h, l, c, v, period=20):
    """CCI V2."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    tp = [(h[i] + l[i] + c[i]) / 3 for i in range(len(c))]
    sma_tp = sma(tp, period)
    md_vals = []
    for i in range(len(tp)):
        if i < period - 1:
            md_vals.append(1.0)
        else:
            window = tp[i - period + 1:i + 1]
            avg = sma_tp[i]
            md = sum(abs(w - avg) for w in window) / period
            md_vals.append(md if md != 0 else 0.001)
    cci_vals = [(tp[i] - sma_tp[i]) / (0.015 * md_vals[i]) if md_vals[i] > 0 else 0 for i in range(len(tp))]
    cci = cci_vals[-1] if cci_vals else 0
    direction = "LONG" if cci > 100 else "SHORT" if cci < -100 else "NEUTRAL"
    return {"direction": direction, "value": cci}


def leading_williams(o, h, l, c, v, period=14):
    """Williams %R V2."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": -50}
    hi = max(h[-period:])
    lo = min(l[-period:])
    wr = ((hi - c[-1]) / (hi - lo)) * -100 if hi != lo else -50
    direction = "LONG" if wr < -80 else "SHORT" if wr > -20 else "NEUTRAL"
    return {"direction": direction, "value": wr}


def leading_tsi(o, h, l, c, v, long_period=25, short_period=13):
    """True Strength Index."""
    if len(c) < long_period + short_period + 2:
        return {"direction": "NEUTRAL", "value": 0}
    changes = [c[i] - c[i - 1] for i in range(1, len(c))]
    abs_changes = [abs(ch) for ch in changes]
    smooth1 = ema(changes, long_period)
    if not smooth1:
        return {"direction": "NEUTRAL", "value": 0}
    smooth2 = [abs(s) for s in smooth1]
    abs_smooth1 = ema(abs_changes, long_period)
    if not abs_smooth1:
        return {"direction": "NEUTRAL", "value": 0}
    smooth3 = ema(smooth1, short_period)
    abs_smooth3 = ema(abs_smooth1, short_period)
    if not smooth3 or not abs_smooth3:
        return {"direction": "NEUTRAL", "value": 0}
    tsi = (smooth3[-1] / abs_smooth3[-1]) * 100 if abs_smooth3[-1] != 0 else 0
    direction = "LONG" if tsi > 0 else "SHORT" if tsi < 0 else "NEUTRAL"
    return {"direction": direction, "value": tsi}


def leading_tdfi(o, h, l, c, v, period=14, displacement=5):
    """Time Displaced Force Index."""
    if len(c) < period + displacement:
        return {"direction": "NEUTRAL", "value": 0}
    fi = [(c[i] - c[i - 1]) * v[i] for i in range(1, len(c))]
    fi_smooth = ema(fi, period)
    if not fi_smooth:
        return {"direction": "NEUTRAL", "value": 0}
    idx = -1 - displacement
    tdfi_val = fi_smooth[idx] if abs(idx) <= len(fi_smooth) else fi_smooth[-1]
    direction = "LONG" if tdfi_val > 0 else "SHORT" if tdfi_val < 0 else "NEUTRAL"
    return {"direction": direction, "value": tdfi_val}


def leading_fisher(o, h, l, c, v, period=10):
    """Fisher Transform V2."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    hl2 = [(h[i] + l[i]) / 2 for i in range(len(c))]
    fish = [0.0] * len(c)
    for i in range(len(c)):
        if i < period - 1:
            continue
        hi = max(hl2[i - period + 1:i + 1])
        lo = min(hl2[i - period + 1:i + 1])
        val = 0.5 * 2 * ((hl2[i] - lo) / (hi - lo + 1e-10) - 0.5)
        val = max(min(val, 0.999), -0.999)
        fish[i] = 0.5 * math.log((1 + val) / (1 - val))
    f = fish[-1] if fish else 0
    direction = "LONG" if f > 0 else "SHORT" if f < 0 else "NEUTRAL"
    return {"direction": direction, "value": f}


def leading_invFisher(o, h, l, c, v, period=10):
    """Inverse Fisher Transform."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    hl2 = [(h[i] + l[i]) / 2 for i in range(len(c))]
    rsi_vals = leading_rsi(o, h, l, c, v, period)
    rsi_value = rsi_vals["value"]
    norm = (rsi_value / 50) - 1
    norm = max(min(norm, 0.999), -0.999)
    inv_fish = (math.exp(2 * norm) - 1) / (math.exp(2 * norm) + 1)
    direction = "LONG" if inv_fish > 0 else "SHORT" if inv_fish < 0 else "NEUTRAL"
    return {"direction": direction, "value": inv_fish}


def leading_coppock(o, h, l, c, v, roc_period=14, wma_period=10):
    """Coppock Curve."""
    if len(c) < roc_period + wma_period + 10:
        return {"direction": "NEUTRAL", "value": 0}
    roc1 = roc(c, roc_period)
    roc2 = roc(c, roc_period * 2)
    rc = [roc1[i] + roc2[i] for i in range(len(roc1))]
    wma_vals = []
    for i in range(len(rc)):
        if i < wma_period - 1:
            wma_vals.append(rc[i])
        else:
            window = rc[i - wma_period + 1:i + 1]
            weights = sum(w * (j + 1) for j, w in enumerate(window))
            wsum = wma_period * (wma_period + 1) / 2
            wma_vals.append(weights / wsum)
    coppock = wma_vals[-1] if wma_vals else 0
    direction = "LONG" if coppock > 0 else "SHORT" if coppock < 0 else "NEUTRAL"
    return {"direction": direction, "value": coppock}


def leading_macd(o, h, l, c, v, fast=12, slow=26, signal=9):
    """MACD."""
    if len(c) < slow + signal:
        return {"direction": "NEUTRAL", "value": 0, "histogram": 0}
    ema_fast = ema(c, fast)
    ema_slow = ema(c, slow)
    if not ema_fast or not ema_slow:
        return {"direction": "NEUTRAL", "value": 0, "histogram": 0}
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(min(len(ema_fast), len(ema_slow)))]
    sig_line = ema(macd_line, signal)
    if not sig_line:
        return {"direction": "NEUTRAL", "value": 0, "histogram": 0}
    hist = macd_line[-1] - sig_line[-1] if len(macd_line) == len(sig_line) else 0
    direction = "LONG" if macd_line[-1] > sig_line[-1] else "SHORT" if macd_line[-1] < sig_line[-1] else "NEUTRAL"
    return {"direction": direction, "value": macd_line[-1], "signal": sig_line[-1], "histogram": hist}


def leading_awesome(o, h, l, c, v, fast=5, slow=34):
    """Awesome Oscillator."""
    if len(c) < slow:
        return {"direction": "NEUTRAL", "value": 0}
    mp = [(h[i] + l[i]) / 2 for i in range(len(c))]
    sma_fast = sma(mp, fast)
    sma_slow = sma(mp, slow)
    if not sma_fast or not sma_slow:
        return {"direction": "NEUTRAL", "value": 0}
    ao_vals = [sma_fast[i] - sma_slow[i] for i in range(min(len(sma_fast), len(sma_slow)))]
    ao = ao_vals[-1] if ao_vals else 0
    direction = "LONG" if len(ao_vals) >= 3 and ao_vals[-1] > ao_vals[-2] > ao_vals[-3] else \
                "SHORT" if len(ao_vals) >= 3 and ao_vals[-1] < ao_vals[-2] < ao_vals[-3] else "NEUTRAL"
    return {"direction": direction, "value": ao}


def leading_momentum(o, h, l, c, v, period=14):
    """Momentum."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    mom = c[-1] - c[-period] if period <= len(c) else 0
    direction = "LONG" if mom > 0 else "SHORT" if mom < 0 else "NEUTRAL"
    return {"direction": direction, "value": mom}


def leading_roc_indicator(o, h, l, c, v, period=14):
    """Rate of Change."""
    if len(c) <= period:
        return {"direction": "NEUTRAL", "value": 0}
    vals = roc(c, period)
    r = vals[-1] if vals else 0
    direction = "LONG" if r > 0 else "SHORT" if r < 0 else "NEUTRAL"
    return {"direction": direction, "value": r}


def leading_trix(o, h, l, c, v, period=14):
    """TRIX (Triple EMA)."""
    if len(c) < period * 3:
        return {"direction": "NEUTRAL", "value": 0}
    e1 = ema(c, period)
    if not e1:
        return {"direction": "NEUTRAL", "value": 0}
    e2 = ema(e1, period)
    if not e2:
        return {"direction": "NEUTRAL", "value": 0}
    e3 = ema(e2, period)
    if not e3 or len(e3) < 2:
        return {"direction": "NEUTRAL", "value": 0}
    trix_val = ((e3[-1] - e3[-2]) / e3[-2]) * 100 if e3[-2] != 0 else 0
    direction = "LONG" if trix_val > 0 else "SHORT" if trix_val < 0 else "NEUTRAL"
    return {"direction": direction, "value": trix_val}


def leading_vortex(o, h, l, c, v, period=14):
    """Vortex Indicator."""
    if len(c) < period + 1:
        return {"direction": "NEUTRAL", "value": 0}
    vm_plus = []
    vm_minus = []
    for i in range(1, len(c)):
        vm_plus.append(abs(h[i] - l[i - 1]))
        vm_minus.append(abs(l[i] - h[i - 1]))
    atr_vals = atr(h, l, c, period)
    if not atr_vals:
        return {"direction": "NEUTRAL", "value": 0}
    vi_plus = 0
    vi_minus = 0
    if len(vm_plus) >= period and len(atr_vals) > len(vm_plus):
        idx = len(vm_plus) - period
        sum_vp = sum(vm_plus[-period:])
        sum_vm = sum(vm_minus[-period:])
        atr_sum = sum(atr_vals[-period - 1:-1])  # align index
        vi_plus = sum_vp / atr_sum if atr_sum != 0 else 0
        vi_minus = sum_vm / atr_sum if atr_sum != 0 else 0
    direction = "LONG" if vi_plus > vi_minus else "SHORT" if vi_plus < vi_minus else "NEUTRAL"
    return {"direction": direction, "value": vi_plus - vi_minus, "vi_plus": vi_plus, "vi_minus": vi_minus}


def leading_kama(o, h, l, c, v, period=10, fast=2, slow=30):
    """KAMA (Kaufman Adaptive Moving Average)."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    kama_vals = [c[0]]
    for i in range(1, len(c)):
        change = abs(c[i] - c[i - period]) if i >= period else abs(c[i] - c[0])
        volatility = sum(abs(c[j] - c[j - 1]) for j in range(max(1, i - period + 1), i + 1))
        er = change / volatility if volatility != 0 else 0
        sc = (er * (2.0 / (fast + 1) - 2.0 / (slow + 1)) + 2.0 / (slow + 1)) ** 2
        kama_vals.append(kama_vals[-1] + sc * (c[i] - kama_vals[-1]))
    direction = "LONG" if c[-1] > kama_vals[-1] else "SHORT" if c[-1] < kama_vals[-1] else "NEUTRAL"
    return {"direction": direction, "value": kama_vals[-1]}


def leading_chandelier(o, h, l, c, v, period=22, mult=3.0):
    """Chandelier Exit."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    atr_vals = atr(h, l, c, period)
    if not atr_vals:
        return {"direction": "NEUTRAL", "value": 0}
    highest_high = highest(h, period)
    lowest_low = lowest(l, period)
    long_stop = highest_high - mult * atr_vals[-1]
    short_stop = lowest_low + mult * atr_vals[-1]
    direction = "LONG" if c[-1] > long_stop else "SHORT" if c[-1] < short_stop else "NEUTRAL"
    return {"direction": direction, "value": (c[-1] - long_stop) / (short_stop - long_stop + 1e-10)}


def leading_diy_ma(o, h, l, c, v, period=20, ma_type="ema"):
    """DIY Moving Average (EMA, SMA, WMA, HMA selectable)."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    if ma_type == "ema":
        ma_vals = ema(c, period)
    elif ma_type == "sma":
        ma_vals = sma(c, period)
    elif ma_type == "wma":
        ma_vals = []
        for i in range(len(c)):
            if i < period - 1:
                ma_vals.append(c[i])
            else:
                window = c[i - period + 1:i + 1]
                weights = sum(w * (j + 1) for j, w in enumerate(window))
                wsum = period * (period + 1) / 2
                ma_vals.append(weights / wsum)
    elif ma_type == "hma":
        half = period // 2
        wma_half = wma(c, max(half, 2))
        wma_full = wma(c, max(period, 2))
        if not wma_half or not wma_full:
            return {"direction": "NEUTRAL", "value": 0}
        diff = [2 * wma_half[i] - wma_full[i] for i in range(min(len(wma_half), len(wma_full)))]
        ma_vals = wma(diff, max(int(math.sqrt(period)), 2))
    else:
        ma_vals = ema(c, period)
    if not ma_vals:
        return {"direction": "NEUTRAL", "value": 0}
    direction = "LONG" if c[-1] > ma_vals[-1] else "SHORT" if c[-1] < ma_vals[-1] else "NEUTRAL"
    return {"direction": direction, "value": ma_vals[-1]}


def leading_double_ema(o, h, l, c, v, period=20):
    """Double EMA."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    e1 = ema(c, period)
    if not e1:
        return {"direction": "NEUTRAL", "value": 0}
    e2 = ema(e1, period)
    if not e2:
        return {"direction": "NEUTRAL", "value": 0}
    dema = [2 * e1[i] - e2[i] for i in range(len(e2))]
    direction = "LONG" if c[-1] > dema[-1] else "SHORT" if c[-1] < dema[-1] else "NEUTRAL"
    return {"direction": direction, "value": dema[-1]}


def leading_triple_ema(o, h, l, c, v, period=20):
    """Triple EMA."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    e1 = ema(c, period)
    if not e1:
        return {"direction": "NEUTRAL", "value": 0}
    e2 = ema(e1, period)
    if not e2:
        return {"direction": "NEUTRAL", "value": 0}
    e3 = ema(e2, period)
    if not e3:
        return {"direction": "NEUTRAL", "value": 0}
    tema = [3 * e1[i] - 3 * e2[i] + e3[i] for i in range(min(len(e1), len(e2), len(e3)))]
    direction = "LONG" if c[-1] > tema[-1] else "SHORT" if c[-1] < tema[-1] else "NEUTRAL"
    return {"direction": direction, "value": tema[-1]}


def leading_laguerre_rsi(o, h, l, c, v, gamma=0.5, period=10):
    """Laguerre RSI."""
    if len(c) < 5:
        return {"direction": "NEUTRAL", "value": 50}
    l0 = l1 = l2 = l3 = 0.0
    lrs = []
    for i in range(len(c)):
        price = c[i]
        l0_prev = l0
        l1_prev = l1
        l2_prev = l2
        l3_prev = l3
        l0 = (1 - gamma) * price + gamma * l0_prev
        l1 = -gamma * l0 + l0_prev + gamma * l1_prev
        l2 = -gamma * l1 + l1_prev + gamma * l2_prev
        l3 = -gamma * l2 + l2_prev + gamma * l3_prev
        cu = max(l0 - l1, 0) + max(l1 - l2, 0) + max(l2 - l3, 0)
        cd = max(l1 - l0, 0) + max(l2 - l1, 0) + max(l3 - l2, 0)
        lrsi = (cu / (cu + cd + 1e-10)) * 100
        lrs.append(lrsi)
    lrsi_val = lrs[-1] if lrs else 50
    direction = "LONG" if lrsi_val > 50 else "SHORT" if lrsi_val < 50 else "NEUTRAL"
    return {"direction": direction, "value": lrsi_val}


def leading_rsi_333(o, h, l, c, v):
    """RSI 3/3/3 — fast RSI with period 3."""
    return leading_rsi(o, h, l, c, v, period=3)


def leading_linreg(o, h, l, c, v, period=14):
    """Linear Regression."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    vals = linreg(c, period)
    if len(vals) >= 2:
        direction = "LONG" if vals[-1] > vals[-2] else "SHORT" if vals[-1] < vals[-2] else "NEUTRAL"
    else:
        direction = "NEUTRAL"
    return {"direction": direction, "value": vals[-1]}


def leading_swing_index(o, h, l, c, v, period=14):
    """Swing Index."""
    if len(c) < 2:
        return {"direction": "NEUTRAL", "value": 0}
    si_vals = []
    for i in range(1, len(c)):
        k = max(h[i] - c[i - 1], l[i] - c[i - 1])
        tr_val = true_range(o[i], h[i], l[i], c[i], c[i - 1])
        hc = h[i] - c[i - 1]
        lc = l[i] - c[i - 1]
        if hc >= lc and hc > 0:
            r = hc
        elif lc > hc and lc > 0:
            r = lc
        else:
            r = hc - 0.5 * lc + 0.25 * (c[i - 1] - o[i - 1])
        si = 50 * (c[i] - c[i - 1] + 0.5 * (c[i - 1] - o[i - 1]) + 0.25 * (c[i] - o[i])) / (r + 1e-10) * k / (tr_val + 1e-10)
        si_vals.append(si)
    if len(si_vals) < period:
        return {"direction": "NEUTRAL", "value": 0}
    si_sum = sum(si_vals[-period:])
    direction = "LONG" if si_sum > 0 else "SHORT" if si_sum < 0 else "NEUTRAL"
    return {"direction": direction, "value": si_sum}


def leading_rainbow_ma(o, h, l, c, v, period=10):
    """Rainbow MA — average of 8 smoothed MAs."""
    if len(c) < period * 2:
        return {"direction": "NEUTRAL", "value": 0}
    mults = [1, 2, 3, 4, 5, 6, 7, 8]
    mas = []
    for m in mults:
        mas.append(sma(c, max(period + m - 1, 2)))
    if not all(mas):
        return {"direction": "NEUTRAL", "value": 0}
    rainbow = [sum(m[i] for m in mas) / len(mas) for i in range(min(len(m) for m in mas))]
    direction = "LONG" if c[-1] > rainbow[-1] else "SHORT" if c[-1] < rainbow[-1] else "NEUTRAL"
    return {"direction": direction, "value": rainbow[-1] if rainbow else 0}


def leading_aroon(o, h, l, c, v, period=14):
    """Aroon."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    bars_since_high = 0
    bars_since_low = 0
    hi = max(h[-period:])
    lo = min(l[-period:])
    for i in range(-period, 0):
        if h[i] == hi:
            bars_since_high = abs(i)
        if l[i] == lo:
            bars_since_low = abs(i)
    aroon_up = ((period - bars_since_high) / period) * 100
    aroon_down = ((period - bars_since_low) / period) * 100
    direction = "LONG" if aroon_up > aroon_down else "SHORT" if aroon_up < aroon_down else "NEUTRAL"
    return {"direction": direction, "value": aroon_up - aroon_down, "aroon_up": aroon_up, "aroon_down": aroon_down}


def leading_psar(o, h, l, c, v, step=0.02, max_step=0.2):
    """Parabolic SAR."""
    if len(c) < 3:
        return {"direction": "NEUTRAL", "value": 0}
    psar = [l[0]]
    ep = h[0]
    af = step
    uptrend = True
    for i in range(1, len(c)):
        if uptrend:
            psar.append(psar[-1] + af * (ep - psar[-1]))
            psar[-1] = min(psar[-1], l[i - 1] if i > 1 else l[i - 1])
            if h[i] > ep:
                ep = h[i]
                af = min(af + step, max_step)
            if l[i] < psar[-1]:
                uptrend = False
                psar[-1] = ep
                ep = l[i]
                af = step
        else:
            psar.append(psar[-1] + af * (ep - psar[-1]))
            psar[-1] = max(psar[-1], h[i - 1] if i > 1 else h[i - 1])
            if l[i] < ep:
                ep = l[i]
                af = min(af + step, max_step)
            if h[i] > psar[-1]:
                uptrend = True
                psar[-1] = ep
                ep = h[i]
                af = step
    direction = "LONG" if uptrend else "SHORT"
    return {"direction": direction, "value": psar[-1] if psar else 0}


def leading_zlema(o, h, l, c, v, period=14):
    """ZLEMA (Zero-Lag EMA)."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    lag = (period - 1) // 2
    zlema_vals = [c[i] + (c[i] - c[i - lag]) if i >= lag else c[i] for i in range(len(c))]
    zlema_vals = ema(zlema_vals, period)
    if not zlema_vals:
        return {"direction": "NEUTRAL", "value": 0}
    direction = "LONG" if c[-1] > zlema_vals[-1] else "SHORT" if c[-1] < zlema_vals[-1] else "NEUTRAL"
    return {"direction": direction, "value": zlema_vals[-1]}


def leading_hma(o, h, l, c, v, period=14):
    """Hull Moving Average (uses WMA internally)."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    half = period // 2
    sqrt_period = int(math.sqrt(period))
    wma_half = wma(c, max(half, 2))
    wma_full = wma(c, max(period, 2))
    if not wma_half or not wma_full:
        return {"direction": "NEUTRAL", "value": 0}
    diff = [2 * wma_half[i] - wma_full[i] for i in range(min(len(wma_half), len(wma_full)))]
    hma_vals = wma(diff, max(sqrt_period, 2))
    if not hma_vals:
        return {"direction": "NEUTRAL", "value": 0}
    direction = "LONG" if c[-1] > hma_vals[-1] else "SHORT" if c[-1] < hma_vals[-1] else "NEUTRAL"
    return {"direction": direction, "value": hma_vals[-1]}


def leading_alma(o, h, l, c, v, period=9, sigma=6, offset=0.85):
    """Arnaud Legoux Moving Average."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    m = offset * (period - 1)
    s = period / sigma
    wsum = 0
    weights = []
    for i in range(period):
        w = math.exp(-((i - m) ** 2) / (2 * s * s))
        weights.append(w)
        wsum += w
    weights = [w / wsum for w in weights]
    alma_vals = []
    for i in range(len(c)):
        if i < period - 1:
            alma_vals.append(c[i])
        else:
            alma_vals.append(sum(c[i - period + 1 + j] * weights[j] for j in range(period)))
    direction = "LONG" if c[-1] > alma_vals[-1] else "SHORT" if c[-1] < alma_vals[-1] else "NEUTRAL"
    return {"direction": direction, "value": alma_vals[-1]}


def leading_speedy_alma(o, h, l, c, v):
    """Composite: Speedy Range + ALMA — both must agree."""
    sr = leading_speedyRange(o, h, l, c, v)
    al = leading_alma(o, h, l, c, v)
    if sr["direction"] == "NEUTRAL" or al["direction"] == "NEUTRAL":
        return {"direction": "NEUTRAL", "value": 0}
    if sr["direction"] == al["direction"]:
        return {"direction": sr["direction"], "value": (sr.get("value", 0) + al.get("value", 0)) / 2}
    return {"direction": "NEUTRAL", "value": 0}


def leading_jjma(o, h, l, c, v, period=7, phase=0):
    """Jurik Moving Average (simplified approximation)."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    volty = 0
    v_sum = 0
    jma_vals = [c[0]]
    for i in range(1, len(c)):
        d = c[i] - c[i - 1] if i > 0 else 0
        volty += abs(d)
        v_sum += volty
        kv = 1 if v_sum == 0 else math.log(1 + period / max(v_sum / period, 1))
        ma = jma_vals[-1] + kv * (c[i] - jma_vals[-1])
        jma_vals.append(ma)
    direction = "LONG" if c[-1] > jma_vals[-1] else "SHORT" if c[-1] < jma_vals[-1] else "NEUTRAL"
    return {"direction": direction, "value": jma_vals[-1]}


def leading_tillson_t3(o, h, l, c, v, period=5, v_factor=0.7):
    """Tillson T3 Moving Average."""
    if len(c) < period:
        return {"direction": "NEUTRAL", "value": 0}
    e1 = ema(c, period)
    if not e1:
        return {"direction": "NEUTRAL", "value": 0}
    e2 = ema(e1, period)
    if not e2:
        return {"direction": "NEUTRAL", "value": 0}
    e3 = ema(e2, period)
    if not e3:
        return {"direction": "NEUTRAL", "value": 0}
    e4 = ema(e3, period)
    if not e4:
        return {"direction": "NEUTRAL", "value": 0}
    e5 = ema(e4, period)
    if not e5:
        return {"direction": "NEUTRAL", "value": 0}
    e6 = ema(e5, period)
    if not e6:
        return {"direction": "NEUTRAL", "value": 0}
    c1 = -v_factor ** 3
    c2 = 3 * v_factor ** 2 + 3 * v_factor ** 3
    c3 = -6 * v_factor ** 2 - 3 * v_factor - 3 * v_factor ** 3
    c4 = 1 + 3 * v_factor + v_factor ** 3 + 3 * v_factor ** 2
    min_len = min(len(e1), len(e2), len(e3), len(e4), len(e5), len(e6))
    t3 = [c1 * e6[i] + c2 * e5[i] + c3 * e4[i] + c4 * e3[i] for i in range(min_len)]
    direction = "LONG" if c[-1] > t3[-1] else "SHORT" if c[-1] < t3[-1] else "NEUTRAL"
    return {"direction": direction, "value": t3[-1] if t3 else 0}


def leading_dema(o, h, l, c, v, period=14):
    """DEMA (Double EMA) — same as Double EMA but using standard formula."""
    return leading_double_ema(o, h, l, c, v, period)


# Map of leading indicator names to functions
LEADING_INDICATORS: Dict[str, Callable] = {
    "Range Filter": leading_rangefilter,
    "Speedy Range": leading_speedyRange,
    "SuperTrend": leading_superTrend,
    "HalfTrend": leading_halfTrend,
    "RSI V2": leading_rsi,
    "Stochastic V2": leading_stochastic,
    "CCI V2": leading_cci,
    "Williams %R V2": leading_williams,
    "TSI": leading_tsi,
    "TDFI": leading_tdfi,
    "Fisher V2": leading_fisher,
    "Inv Fisher": leading_invFisher,
    "Coppock": leading_coppock,
    "MACD": leading_macd,
    "Awesome Osc": leading_awesome,
    "Momentum": leading_momentum,
    "ROC": leading_roc_indicator,
    "TRIX": leading_trix,
    "Vortex": leading_vortex,
    "KAMA": leading_kama,
    "Chandelier": leading_chandelier,
    "DIY MA": leading_diy_ma,
    "DEMA": leading_double_ema,
    "TEMA": leading_triple_ema,
    "Laguerre RSI": leading_laguerre_rsi,
    "RSI 3/3/3": leading_rsi_333,
    "LinReg": leading_linreg,
    "Swing Index": leading_swing_index,
    "Rainbow MA": leading_rainbow_ma,
    "Aroon": leading_aroon,
    "PSAR": leading_psar,
    "ZLEMA": leading_zlema,
    "HMA": leading_hma,
    "ALMA": leading_alma,
    "Speedy+ALMA": leading_speedy_alma,
    "JJMA": leading_jjma,
    "Tillson T3": leading_tillson_t3,
}

LEADING_NAMES = list(LEADING_INDICATORS.keys())

# ─── Confirmation Indicators (30+) ──────────────────────────────────

# Each confirmation function receives (o, h, l, c, v) as lists
# and returns a dict: {"confirmed": True/False, "direction": "LONG"/"SHORT", "value": ...}


def confirm_ema_20(o, h, l, c, v):
    """Price above/below EMA 20."""
    if len(c) < 20:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    e = ema(c, 20)
    if not e:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    above = c[-1] > e[-1]
    return {"confirmed": True, "direction": "LONG" if above else "SHORT", "value": e[-1]}


def confirm_ema_50(o, h, l, c, v):
    if len(c) < 50:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    e = ema(c, 50)
    if not e:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    above = c[-1] > e[-1]
    return {"confirmed": True, "direction": "LONG" if above else "SHORT", "value": e[-1]}


def confirm_ema_100(o, h, l, c, v):
    if len(c) < 100:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    e = ema(c, 100)
    if not e:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    above = c[-1] > e[-1]
    return {"confirmed": True, "direction": "LONG" if above else "SHORT", "value": e[-1]}


def confirm_ema_200(o, h, l, c, v):
    if len(c) < 200:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    e = ema(c, 200)
    if not e:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    above = c[-1] > e[-1]
    return {"confirmed": True, "direction": "LONG" if above else "SHORT", "value": e[-1]}


def confirm_sma_20(o, h, l, c, v):
    if len(c) < 20:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    s = sma(c, 20)
    if not s:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    above = c[-1] > s[-1]
    return {"confirmed": True, "direction": "LONG" if above else "SHORT", "value": s[-1]}


def confirm_sma_50(o, h, l, c, v):
    if len(c) < 50:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    s = sma(c, 50)
    if not s:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    above = c[-1] > s[-1]
    return {"confirmed": True, "direction": "LONG" if above else "SHORT", "value": s[-1]}


def confirm_bollinger(o, h, l, c, v, period=20, mult=2.0):
    """Bollinger Bands — price relative to bands."""
    if len(c) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    s = sma(c, period)
    if not s:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    std = stdev(c, period)
    if not std:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    upper = s[-1] + mult * std[-1]
    lower = s[-1] - mult * std[-1]
    bb_width = (upper - lower) / s[-1] if s[-1] != 0 else 0
    squeezed = bb_width < 0.05  # 5% width squeeze
    position = (c[-1] - lower) / (upper - lower) if upper != lower else 0.5
    # Overbought/oversold based on %b
    if position < 0.05:
        return {"confirmed": True, "direction": "LONG", "value": position, "squeeze": squeezed}
    elif position > 0.95:
        return {"confirmed": True, "direction": "SHORT", "value": position, "squeeze": squeezed}
    return {"confirmed": True, "direction": "NEUTRAL", "value": position, "squeeze": squeezed}


def confirm_keltner(o, h, l, c, v, period=20, mult=1.5):
    """Keltner Channels."""
    if len(c) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    e = ema(c, period)
    if not e:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    atr_vals = atr(h, l, c, period)
    if not atr_vals:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    upper = e[-1] + mult * atr_vals[-1]
    lower = e[-1] - mult * atr_vals[-1]
    if c[-1] > upper:
        return {"confirmed": True, "direction": "SHORT", "value": (c[-1] - upper) / (upper - lower + 1e-10)}
    elif c[-1] < lower:
        return {"confirmed": True, "direction": "LONG", "value": (c[-1] - lower) / (upper - lower + 1e-10)}
    return {"confirmed": True, "direction": "NEUTRAL", "value": (c[-1] - e[-1]) / (atr_vals[-1] + 1e-10)}


def confirm_adx(o, h, l, c, v, period=14):
    """ADX — trend strength."""
    if len(c) < period + 1:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    atr_vals = atr(h, l, c, period)
    if not atr_vals:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    plus_dm = [max(h[i] - h[i - 1], 0) if i > 0 and h[i] - h[i - 1] > max(l[i - 1] - l[i], 0) else 0 for i in range(len(c))]
    minus_dm = [max(l[i - 1] - l[i], 0) if i > 0 and l[i - 1] - l[i] > max(h[i] - h[i - 1], 0) else 0 for i in range(len(c))]
    pdi = rma(plus_dm, period)
    mdi = rma(minus_dm, period)
    if not pdi or not mdi:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    dx = [abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i] + 1e-10) * 100 for i in range(min(len(pdi), len(mdi)))]
    adx_vals = rma(dx, period) if dx else []
    adx_value = adx_vals[-1] if adx_vals else 0
    trend = pdi[-1] > mdi[-1] if len(pdi) > 0 and len(mdi) > 0 else False
    direction = "LONG" if trend and adx_value > 20 else "SHORT" if not trend and adx_value > 20 else "NEUTRAL"
    return {"confirmed": adx_value > 20, "direction": direction, "value": adx_value}


def confirm_atr_trail(o, h, l, c, v, period=10, mult=2.0):
    """ATR Trailing Stop."""
    if len(c) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    atr_vals = atr(h, l, c, period)
    if not atr_vals:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    hl2 = [(h[i] + l[i]) / 2 for i in range(len(c))]
    trail = [0.0] * len(c)
    trend_up = [True] * len(c)
    for i in range(1, len(c)):
        trail[i] = hl2[i] - mult * atr_vals[i] if trend_up[i - 1] else hl2[i] + mult * atr_vals[i]
        trail[i] = max(trail[i], trail[i - 1]) if trend_up[i - 1] else min(trail[i], trail[i - 1])
        trend_up[i] = c[i] > trail[i]
    direction = "LONG" if trend_up[-1] else "SHORT"
    return {"confirmed": True, "direction": direction, "value": trail[-1]}


def confirm_donchian(o, h, l, c, v, period=20):
    """Donchian Channels."""
    if len(c) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    hi = highest(h, period)
    lo = lowest(l, period)
    mid = (hi + lo) / 2
    if c[-1] >= hi:
        return {"confirmed": True, "direction": "LONG", "value": (c[-1] - lo) / (hi - lo + 1e-10)}
    elif c[-1] <= lo:
        return {"confirmed": True, "direction": "SHORT", "value": (c[-1] - lo) / (hi - lo + 1e-10)}
    return {"confirmed": True, "direction": "NEUTRAL", "value": (c[-1] - mid) / (hi - lo + 1e-10)}


def confirm_macd_conf(o, h, l, c, v):
    """MACD confirmation (histogram direction)."""
    r = leading_macd(o, h, l, c, v)
    hist = r.get("histogram", 0)
    direction = "LONG" if hist > 0 else "SHORT" if hist < 0 else "NEUTRAL"
    return {"confirmed": hist != 0, "direction": direction, "value": hist}


def confirm_rsi_conf(o, h, l, c, v, period=14, oversold=30, overbought=70):
    """RSI overbought/oversold confirmation."""
    r = leading_rsi(o, h, l, c, v, period)
    rsi_val = r["value"]
    if rsi_val < oversold:
        return {"confirmed": True, "direction": "LONG", "value": rsi_val}
    elif rsi_val > overbought:
        return {"confirmed": True, "direction": "SHORT", "value": rsi_val}
    return {"confirmed": True, "direction": "NEUTRAL", "value": rsi_val}


def confirm_stoch_conf(o, h, l, c, v):
    """Stochastic cross confirmation."""
    r = leading_stochastic(o, h, l, c, v)
    k = r.get("value", 50)
    d = r.get("signal", 50)
    direction = "LONG" if k > d else "SHORT" if k < d else "NEUTRAL"
    return {"confirmed": k != d, "direction": direction, "value": k - d}


def confirm_volume(o, h, l, c, v, period=14):
    """Volume confirmation — volume above average."""
    if len(v) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    avg_vol = sum(v[-period:]) / period
    vol_ratio = v[-1] / avg_vol if avg_vol > 0 else 1
    direction = "LONG" if vol_ratio > 1.5 and c[-1] > (c[-2] if len(c) > 1 else c[-1]) else \
                "SHORT" if vol_ratio > 1.5 and c[-1] < (c[-2] if len(c) > 1 else c[-1]) else "NEUTRAL"
    return {"confirmed": vol_ratio > 1.3, "direction": direction, "value": vol_ratio}


def confirm_price_action(o, h, l, c, v):
    """Price action — bullish/bearish engulfing, doji, pin bar."""
    if len(c) < 3:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    body1 = abs(c[-3] - o[-3])
    body2 = abs(c[-2] - o[-2])
    body = abs(c[-1] - o[-1])
    upper_wick = h[-1] - max(c[-1], o[-1])
    lower_wick = min(c[-1], o[-1]) - l[-1]
    # Bullish engulfing
    if c[-1] > o[-1] and c[-2] < o[-2] and c[-1] > o[-2] and o[-1] < c[-2]:
        return {"confirmed": True, "direction": "LONG", "value": 2, "pattern": "bullish_engulfing"}
    # Bearish engulfing
    if c[-1] < o[-1] and c[-2] > o[-2] and c[-1] < o[-2] and o[-1] > c[-2]:
        return {"confirmed": True, "direction": "SHORT", "value": -2, "pattern": "bearish_engulfing"}
    # Doji
    if body < (h[-1] - l[-1]) * 0.1 and h[-1] - l[-1] > 0:
        return {"confirmed": True, "direction": "NEUTRAL", "value": 0, "pattern": "doji"}
    # Bullish pin bar
    if lower_wick > body * 2 and upper_wick < body:
        return {"confirmed": True, "direction": "LONG", "value": 1, "pattern": "bullish_pin"}
    # Bearish pin bar
    if upper_wick > body * 2 and lower_wick < body:
        return {"confirmed": True, "direction": "SHORT", "value": -1, "pattern": "bearish_pin"}
    return {"confirmed": False, "direction": "NEUTRAL", "value": 0}


def confirm_mfi(o, h, l, c, v, period=14):
    """Money Flow Index."""
    if len(c) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 50}
    tp = [(h[i] + l[i] + c[i]) / 3 for i in range(len(c))]
    mf = [tp[i] * v[i] for i in range(len(c))]
    pmf = [mf[i] if tp[i] > tp[i - 1] else 0 for i in range(1, len(c))]
    nmf = [mf[i] if tp[i] < tp[i - 1] else 0 for i in range(1, len(c))]
    if len(pmf) < period or len(nmf) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 50}
    mfr = sum(pmf[-period:]) / (sum(nmf[-period:]) + 1e-10)
    mfi_val = 100 - 100 / (1 + mfr)
    if mfi_val < 20:
        return {"confirmed": True, "direction": "LONG", "value": mfi_val}
    elif mfi_val > 80:
        return {"confirmed": True, "direction": "SHORT", "value": mfi_val}
    return {"confirmed": True, "direction": "NEUTRAL", "value": mfi_val}


def confirm_obv(o, h, l, c, v, period=14):
    """On-Balance Volume confirmation."""
    if len(c) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    obv_vals = [0]
    for i in range(1, len(c)):
        if c[i] > c[i - 1]:
            obv_vals.append(obv_vals[-1] + v[i])
        elif c[i] < c[i - 1]:
            obv_vals.append(obv_vals[-1] - v[i])
        else:
            obv_vals.append(obv_vals[-1])
    obv_ema = ema(obv_vals, period)
    if not obv_ema:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    rising = obv_ema[-1] > obv_ema[-2] if len(obv_ema) >= 2 else False
    direction = "LONG" if rising else "SHORT" if len(obv_ema) >= 2 and obv_ema[-1] < obv_ema[-2] else "NEUTRAL"
    return {"confirmed": True, "direction": direction, "value": obv_ema[-1]}


def confirm_williams(o, h, l, c, v):
    """Williams %R confirmation."""
    r = leading_williams(o, h, l, c, v)
    wr = r["value"]
    if wr < -80:
        return {"confirmed": True, "direction": "LONG", "value": wr}
    elif wr > -20:
        return {"confirmed": True, "direction": "SHORT", "value": wr}
    return {"confirmed": True, "direction": "NEUTRAL", "value": wr}


def confirm_heikin_ashi(o, h, l, c, v):
    """Heikin Ashi trend confirmation."""
    if len(c) < 3:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    ha_c = [(o[i] + h[i] + l[i] + c[i]) / 4 for i in range(len(c))]
    ha_o_list = [o[0]]
    for i in range(1, len(c)):
        ha_o_list.append((ha_o_list[-1] + ha_c[i - 1]) / 2)
    ha_h = [max(h[i], ha_o_list[i], ha_c[i]) for i in range(len(c))]
    ha_l = [min(l[i], ha_o_list[i], ha_c[i]) for i in range(len(c))]
    trend = "LONG" if ha_c[-1] > ha_o_list[-1] else "SHORT" if ha_c[-1] < ha_o_list[-1] else "NEUTRAL"
    strength = abs(ha_c[-1] - ha_o_list[-1]) / (ha_h[-1] - ha_l[-1] + 1e-10)
    return {"confirmed": strength > 0.3, "direction": trend, "value": strength}


def confirm_vwap(o, h, l, c, v, period=20):
    """Volume-Weighted Average Price."""
    if len(c) < period:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    tp = [(h[i] + l[i] + c[i]) / 3 for i in range(len(c))]
    pv = [tp[i] * v[i] for i in range(len(c))]
    vwap_vals = []
    cum_pv = 0
    cum_v = 0
    for i in range(len(c)):
        cum_pv += pv[i]
        cum_v += v[i]
        vwap_vals.append(cum_pv / cum_v if cum_v > 0 else c[i])
    direction = "LONG" if c[-1] > vwap_vals[-1] else "SHORT" if c[-1] < vwap_vals[-1] else "NEUTRAL"
    return {"confirmed": True, "direction": direction, "value": vwap_vals[-1]}


def confirm_pivot(o, h, l, c, v):
    """Pivot point confirmation (swing high/low)."""
    if len(c) < 5:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    swing_high = h[-3] > h[-4] and h[-3] > h[-2] and h[-3] > h[-1]
    swing_low = l[-3] < l[-4] and l[-3] < l[-2] and l[-3] < l[-1]
    break_above = c[-1] > h[-3] and swing_high
    break_below = c[-1] < l[-3] and swing_low
    if break_above:
        return {"confirmed": True, "direction": "LONG", "value": 1}
    elif break_below:
        return {"confirmed": True, "direction": "SHORT", "value": -1}
    return {"confirmed": False, "direction": "NEUTRAL", "value": 0}


def confirm_divergence(o, h, l, c, v, period=14):
    """RSI divergence detection using full RSI series."""
    if len(c) < period * 2:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    r = leading_rsi(o, h, l, c, v, period)
    rsi_series = r.get("series", [r["value"]])
    if len(rsi_series) < period * 2:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    # Bullish divergence: price makes lower low, RSI makes higher low
    price_ll = lowest(l, period)
    price_prev = lowest(l, period, period)
    rsi_period = rsi_series[-period:]
    rsi_prev_period = rsi_series[-period * 2:-period]
    rsi_ll = min(rsi_period)
    rsi_prev_low = min(rsi_prev_period)
    bullish_div = price_ll < price_prev and rsi_ll > rsi_prev_low
    # Bearish divergence: price makes higher high, RSI makes lower high
    price_hh = highest(h, period)
    price_prev_hh = highest(h, period, period)
    rsi_hh = max(rsi_period)
    rsi_prev_high = max(rsi_prev_period)
    bearish_div = price_hh > price_prev_hh and rsi_hh < rsi_prev_high
    if bullish_div:
        return {"confirmed": True, "direction": "LONG", "value": 1, "pattern": "bullish_div"}
    elif bearish_div:
        return {"confirmed": True, "direction": "SHORT", "value": -1, "pattern": "bearish_div"}
    return {"confirmed": False, "direction": "NEUTRAL", "value": 0}


def confirm_market_trend(o, h, l, c, v):
    """Check broader market direction using all symbols in OHLC builder.
    If most stocks are going up → LONG, most going down → SHORT.
    Acts as a hard override in update() to block signals against the trend."""
    from app.services.ohlc_builder import ohlc_builder
    symbols = ohlc_builder.get_all_symbols_with_bars(min_bars=5)
    up = down = 0
    for sym in symbols:
        bars = ohlc_builder.get_bars(sym, 2)
        if len(bars) >= 2:
            if bars[-1]["close"] > bars[-2]["close"]:
                up += 1
            elif bars[-1]["close"] < bars[-2]["close"]:
                down += 1
    total = up + down
    if total < 10:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    bull_pct = up / total
    bear_pct = down / total
    if bull_pct > 0.55:
        direction = "LONG"
    elif bear_pct > 0.55:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"
    return {"confirmed": direction != "NEUTRAL", "direction": direction, "value": bull_pct - bear_pct}


def confirm_liquidity_sweep(o, h, l, c, v, lookback=15):
    """Detect liquidity sweeps — price takes out a recent swing high/low
    then reverses back inside. Indicates stop hunts / liquidity grabs."""
    if len(c) < lookback + 3:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    recent_high = max(h[-lookback:-1])
    recent_low = min(l[-lookback:-1])
    bearish_sweep = h[-1] > recent_high and c[-1] < recent_high
    bullish_sweep = l[-1] < recent_low and c[-1] > recent_low
    if bullish_sweep:
        return {"confirmed": True, "direction": "LONG", "value": 1, "pattern": "liquidity_sweep_bullish"}
    elif bearish_sweep:
        return {"confirmed": True, "direction": "SHORT", "value": -1, "pattern": "liquidity_sweep_bearish"}
    return {"confirmed": False, "direction": "NEUTRAL", "value": 0}


def confirm_market_structure(o, h, l, c, v, lookback=12):
    """Detect market structure trend via sequence of highs/lows.
    Uptrend = higher highs + higher lows, Downtrend = lower highs + lower lows."""
    if len(c) < lookback * 2:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    highs = h[-lookback:]
    lows = l[-lookback:]
    up_score = sum(1 for i in range(1, len(highs)) if highs[i] >= highs[i-1]) + \
               sum(1 for i in range(1, len(lows)) if lows[i] >= lows[i-1])
    down_score = sum(1 for i in range(1, len(highs)) if highs[i] <= highs[i-1]) + \
                 sum(1 for i in range(1, len(lows)) if lows[i] <= lows[i-1])
    total = up_score + down_score
    if total == 0:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}
    up_pct = up_score / total
    if up_pct > 0.65:
        return {"confirmed": True, "direction": "LONG", "value": up_pct, "pattern": "uptrend"}
    elif up_pct < 0.35:
        return {"confirmed": True, "direction": "SHORT", "value": 1 - up_pct, "pattern": "downtrend"}
    return {"confirmed": False, "direction": "NEUTRAL", "value": 0}


CONFIRMATION_FILTERS: Dict[str, Callable] = {
    "EMA 20": confirm_ema_20,
    "EMA 50": confirm_ema_50,
    "EMA 100": confirm_ema_100,
    "EMA 200": confirm_ema_200,
    "SMA 20": confirm_sma_20,
    "SMA 50": confirm_sma_50,
    "Bollinger": confirm_bollinger,
    "Keltner": confirm_keltner,
    "ADX": confirm_adx,
    "ATR Trail": confirm_atr_trail,
    "Donchian": confirm_donchian,
    "MACD": confirm_macd_conf,
    "RSI": confirm_rsi_conf,
    "Stochastic": confirm_stoch_conf,
    "Volume": confirm_volume,
    "Price Action": confirm_price_action,
    "MFI": confirm_mfi,
    "OBV": confirm_obv,
    "Williams %R": confirm_williams,
    "Heikin Ashi": confirm_heikin_ashi,
    "VWAP": confirm_vwap,
    "Pivot": confirm_pivot,
    "Divergence": confirm_divergence,
    "Market Trend": confirm_market_trend,
    "Liquidity Sweep": confirm_liquidity_sweep,
    "Market Structure": confirm_market_structure,
}

CONFIRMATION_NAMES = list(CONFIRMATION_FILTERS.keys())

# ─── Overlay Calculations ───────────────────────────────────────────

def calc_fibonacci_levels(high: float, low: float) -> Dict[str, float]:
    """Fibonacci retracement & extension levels."""
    diff = high - low
    return {
        "0.0": high,
        "0.236": high - 0.236 * diff,
        "0.382": high - 0.382 * diff,
        "0.500": high - 0.5 * diff,
        "0.618": high - 0.618 * diff,
        "0.786": high - 0.786 * diff,
        "1.0": low,
        "1.272": low + 0.272 * diff,
        "1.414": low + 0.414 * diff,
        "1.618": low + 0.618 * diff,
        "2.0": low + diff,
        "2.272": low + 1.272 * diff,
        "2.618": low + 1.618 * diff,
    }


def calc_fvg(o, h, l, c, v) -> List[Dict]:
    """Fair Value Gap detection (3-bar pattern)."""
    fvg_list = []
    if len(c) < 3:
        return fvg_list
    for i in range(2, len(c)):
        # Bullish FVG: low[i] > high[i-2]
        if l[i] > h[i - 2]:
            fvg_list.append({"type": "bullish", "top": l[i], "bottom": h[i - 2], "index": i})
        # Bearish FVG: high[i] < low[i-2]
        if h[i] < l[i - 2]:
            fvg_list.append({"type": "bearish", "top": l[i - 2], "bottom": h[i], "index": i})
    return fvg_list[-5:] if fvg_list else []  # last 5 FVGs


def calc_supply_demand(opens, highs, lows, closes) -> Dict[str, List]:
    """Identify supply and demand zones."""
    zones = {"supply": [], "demand": []}
    if len(closes) < 5:
        return zones
    for i in range(2, len(closes) - 1):
        # Demand zone: low[i] is lowest of bar i-2 to i+1, then break above
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1] and lows[i] < lows[i - 2]:
            zones["demand"].append({"price": lows[i], "high": min(opens[i], closes[i]), "index": i})
        # Supply zone: high[i] is highest of bar i-2 to i+1, then break below
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1] and highs[i] > highs[i - 2]:
            zones["supply"].append({"price": highs[i], "low": max(opens[i], closes[i]), "index": i})
    return {k: v[-3:] for k, v in zones.items()}  # last 3 zones each


# ─── Strategy Builder Engine ────────────────────────────────────────

class StrategyBuilder:
    """
    Main strategy builder engine.
    Computes selected leading indicator + confirmation filters on 1-minute OHLC bars
    from the live Dhan WebSocket feed.
    """

    def __init__(self):
        self.active_signals: Dict[str, Dict] = {}
        self.signal_expiry = 5  # 1-min bars before signal expires
        self.alt_signal_mode = False
        self.alt_counter: Dict[str, int] = {}
        self.selected_leading = "Speedy+ALMA"  # default leading indicator
        self.selected_confirmations: List[str] = ["EMA 20", "EMA 50", "MACD", "RSI", "Volume", "Price Action", "Market Trend", "Liquidity Sweep", "Market Structure"]
        self.signal_threshold = 3  # min confirmations needed
        self.buy_only = True  # only generate BUY signals, block SELL
        self.min_bars = 20  # minimum 1-min bars required

    def select_leading(self, name: str) -> bool:
        if name in LEADING_NAMES or name in LEADING_INDICATORS:
            self.selected_leading = name
            return True
        return False

    def set_confirmations(self, names: List[str]):
        self.selected_confirmations = [n for n in names if n in CONFIRMATION_NAMES or n in CONFIRMATION_FILTERS]

    def set_threshold(self, t: int):
        self.signal_threshold = max(1, min(t, len(self.selected_confirmations)))

    def set_expiry(self, bars: int):
        self.signal_expiry = max(1, bars)

    def update(self, symbol: str) -> Optional[Dict]:
        """Compute signals from 1-min OHLC bars. Returns signal dict or None."""
        sym = symbol.upper()

        # Get 1-minute OHLC bars from the live feed tick aggregator
        ohlc = ohlc_builder.to_lists(sym, min_bars=self.min_bars)
        if not ohlc:
            return None
        opens, highs, lows, closes, volumes = ohlc

        # Use latest close as current price
        current_price = closes[-1]

        # 1. Leading indicator
        leading_func = LEADING_INDICATORS.get(self.selected_leading)
        if not leading_func:
            leading_func = leading_superTrend
        try:
            ld = leading_func(opens, highs, lows, closes, volumes)
        except Exception:
            return None
        leading_dir = ld.get("direction", "NEUTRAL")
        leading_val = ld.get("value", 0)

        if leading_dir == "NEUTRAL":
            return None

        # 2. Confirmation filters
        confirmations = []
        conf_long = 0
        conf_short = 0
        for name in self.selected_confirmations:
            func = CONFIRMATION_FILTERS.get(name)
            if not func:
                continue
            try:
                result = func(opens, highs, lows, closes, volumes)
            except Exception:
                continue
            if not result.get("confirmed", False):
                continue
            confirmations.append({
                "name": name,
                "direction": result.get("direction", "NEUTRAL"),
                "value": result.get("value", 0),
            })
            if result["direction"] == "LONG":
                conf_long += 1
            elif result["direction"] == "SHORT":
                conf_short += 1

        # 3. Determine final signal
        long_votes = 1 + conf_long
        short_votes = 1 + conf_short
        total_possible = 1 + len(self.selected_confirmations)

        if leading_dir == "LONG":
            final_signal = "BUY" if long_votes >= self.signal_threshold else "HOLD"
        elif leading_dir == "SHORT":
            final_signal = "SELL" if short_votes >= self.signal_threshold else "HOLD"
        else:
            final_signal = "HOLD"

        # 4. Market trend hard override — block signals against the broader market
        if "Market Trend" in self.selected_confirmations:
            mt_func = CONFIRMATION_FILTERS.get("Market Trend")
            if mt_func:
                try:
                    mt_result = mt_func(opens, highs, lows, closes, volumes)
                    mt_dir = mt_result.get("direction", "NEUTRAL")
                    if final_signal == "BUY" and mt_dir == "SHORT":
                        final_signal = "HOLD"
                    elif final_signal == "SELL" and mt_dir == "LONG":
                        final_signal = "HOLD"
                except Exception:
                    pass

        # 5. Buy-only mode — block all SELL signals
        if self.buy_only and final_signal == "SELL":
            final_signal = "HOLD"

        # 6. Alternate signal mode
        if self.alt_signal_mode and final_signal != "HOLD":
            self.alt_counter[sym] = self.alt_counter.get(sym, 0) + 1
            if self.alt_counter[sym] % 3 != 0:
                return None

        # 6. Build result
        result = {
            "symbol": sym,
            "price": current_price,
            "timeframe": "1m",
            "timestamp": datetime.now().isoformat(),
            "leading_name": self.selected_leading,
            "leading_direction": leading_dir,
            "leading_value": float(leading_val),
            "confirmations": confirmations,
            "confirmation_count": len(confirmations),
            "total_possible": total_possible,
            "long_votes": long_votes,
            "short_votes": short_votes,
            "signal_threshold": self.signal_threshold,
            "final_signal": final_signal,
            "expiry_bars": self.signal_expiry,
        }

        # 6. Persist
        _save_signal(
            symbol=sym,
            leading_name=self.selected_leading,
            leading_dir=leading_dir,
            confirmations=[c["name"] + ":" + c["direction"] for c in confirmations],
            conf_count=len(confirmations),
            threshold=self.signal_threshold,
            final_signal=final_signal,
            price=current_price,
            expiry_bars=self.signal_expiry,
            meta={"long_votes": long_votes, "short_votes": short_votes, "timeframe": "1m"},
        )

        if final_signal in ("BUY", "SELL"):
            _set_active_state(sym, final_signal, current_price, leading_dir)
        elif final_signal == "HOLD":
            _increment_bars_held(sym)
            state = _get_active_state(sym)
            if state and state.get("bars_held", 0) >= self.signal_expiry:
                _clear_active_state(sym)

        return result

    def scan_all(self) -> List[Dict]:
        """Run update on all symbols with 1-min OHLC data (parallel via threads)."""
        symbols = ohlc_builder.get_all_symbols_with_bars(min_bars=self.min_bars)
        if not symbols:
            return []
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            fut_map = {pool.submit(self.update, sym): sym for sym in symbols}
            for fut in as_completed(fut_map):
                try:
                    r = fut.result()
                    if r and r["final_signal"] in ("BUY", "SELL"):
                        results.append(r)
                except Exception:
                    continue
        return results

    def get_active_signals(self) -> List[Dict]:
        """Get all active (unexpired) BUY/SELL signals."""
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM strategy_signals WHERE final_signal IN ('BUY', 'SELL') ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def build_dashboard(self, top_n: int = 15) -> str:
        """Build a formatted dashboard string of active signals."""
        signals = self.get_active_signals()
        if not signals:
            return f"📊 *Strategy Builder — No Active Signals*\n\nNo BUY signals on 1-min timeframe.\n⏳ Waiting for more data..."
        lines = [f"📊 *Strategy Builder — 1-min Signals*", ""]
        lines.append(f"Leading: `{self.selected_leading}` | Threshold: `{self.signal_threshold}` | Expiry: `{self.signal_expiry}` bars")
        lines.append(f"Confirmations: `{len(self.selected_confirmations)}` active | Timeframe: `1-min`")
        lines.append(f"Mode: `{'BUY ONLY' if self.buy_only else 'BUY+SELL'}` | Alt: `{'ON' if self.alt_signal_mode else 'OFF'}`")
        lines.append("")
        lines.append(f"*Active Signals ({len(signals)} total)*")
        lines.append("")
        for s in signals[:top_n]:
            emoji = "🟢" if s["final_signal"] == "BUY" else "🔴"
            ts = s.get("timestamp", "")[11:19] if s.get("timestamp") else ""
            lines.append(
                f"{emoji} `{s['symbol']:<12}` {s['final_signal']} @ ₹{s.get('price', 0):,.2f} "
                f"| {s.get('leading_name', '?')}: {s.get('leading_direction', '?')[:1]} "
                f"| Conf: {s.get('confirmation_count', 0)}/{s.get('signal_threshold', 0)} "
                f"| {ts}"
            )
        lines.append("")
        lines.append("💡 `/strategy` refresh | `/strategy_config` to tune")
        return "\n".join(lines)


    def backtest(self, symbol: str, days: int = 365, interval: str = "1d") -> Dict:
        """
        Backtest the strategy on historical data.
        Fetches data via yfinance, feeds bar-by-bar, tracks P&L.

        Args:
            symbol: Stock symbol (e.g. "RELIANCE")
            days: Days of historical data
            interval: "1d" for daily, "1m" for 1-min (last 7d only)
        Returns:
            Dict with trades, signals, performance metrics
        """
        import yfinance as yf
        sym = symbol.upper()

        # Map symbol
        yf_sym = f"{sym}.NS" if sym not in ('BTC', 'ETH', 'GOLD', 'SILVER') else sym

        # Fetch data
        try:
            if interval == "1m":
                df = yf.download(yf_sym, period="7d", interval="1m", progress=False, auto_adjust=True)
            else:
                df = yf.download(yf_sym, period=f"{days}d", interval=interval, progress=False, auto_adjust=True)
        except Exception as e:
            return {"error": f"Failed to fetch data: {e}"}

        if df.empty or len(df) < 30:
            return {"error": f"Insufficient data ({len(df)} bars)"}

        # Fix MultiIndex columns (yfinance returns ('Close', 'TICKER'))
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        # Also flatten if a single-level MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(0)

        opens = [float(row['Open']) for _, row in df.iterrows()]
        highs = [float(row['High']) for _, row in df.iterrows()]
        lows = [float(row['Low']) for _, row in df.iterrows()]
        closes = [float(row['Close']) for _, row in df.iterrows()]
        volumes = [int(row['Volume']) for _, row in df.iterrows()]
        dates = [str(idx) for idx in df.index]

        # Run strategy bar by bar
        trades = []
        in_position = False
        entry_price = 0
        entry_time = ""
        entry_signal = ""
        signals_log = []

        for i in range(self.min_bars, len(closes)):
            o = opens[:i + 1]
            h = highs[:i + 1]
            l = lows[:i + 1]
            c = closes[:i + 1]
            v = volumes[:i + 1]

            # Leading indicator
            leading_func = LEADING_INDICATORS.get(self.selected_leading)
            if not leading_func:
                leading_func = leading_superTrend
            try:
                ld = leading_func(o, h, l, c, v)
            except Exception:
                continue
            leading_dir = ld.get("direction", "NEUTRAL")
            if leading_dir == "NEUTRAL":
                continue

            # Confirmation filters
            conf_long = 0
            conf_short = 0
            for name in self.selected_confirmations:
                func = CONFIRMATION_FILTERS.get(name)
                if not func:
                    continue
                try:
                    result = func(o, h, l, c, v)
                except Exception:
                    continue
                if not result.get("confirmed", False):
                    continue
                if result["direction"] == "LONG":
                    conf_long += 1
                elif result["direction"] == "SHORT":
                    conf_short += 1

            long_votes = 1 + conf_long
            short_votes = 1 + conf_short

            if leading_dir == "LONG" and long_votes >= self.signal_threshold:
                signal = "BUY"
            elif leading_dir == "SHORT" and short_votes >= self.signal_threshold:
                signal = "SELL"
            else:
                signal = "HOLD"

            price = c[-1]

            signals_log.append({
                "index": i,
                "timestamp": dates[i],
                "price": price,
                "signal": signal,
                "leading": leading_dir,
                "confirmations": f"{conf_long}L/{conf_short}S",
            })

            # Trade management
            if not in_position:
                if signal == "BUY":
                    in_position = True
                    entry_price = price
                    entry_time = dates[i]
                    entry_signal = "BUY"
                elif signal == "SELL":
                    in_position = True
                    entry_price = price
                    entry_time = dates[i]
                    entry_signal = "SELL"
            else:
                exit_signal = False
                if entry_signal == "BUY" and signal == "SELL":
                    exit_signal = True
                elif entry_signal == "SELL" and signal == "BUY":
                    exit_signal = True

                if exit_signal:
                    pnl_pct = ((price - entry_price) / entry_price) * 100
                    if entry_signal == "SELL":
                        pnl_pct = -pnl_pct  # reverse for short
                    trades.append({
                        "entry_time": entry_time,
                        "exit_time": dates[i],
                        "direction": entry_signal,
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(price, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "bars_held": signals_log[-1]["index"] - next((s["index"] for s in signals_log[::-1] if s["signal"] == entry_signal), 0),
                    })
                    in_position = False
                    entry_price = 0

        # Close any open position at last bar
        if in_position and len(closes) > 1:
            price = closes[-1]
            pnl_pct = ((price - entry_price) / entry_price) * 100
            if entry_signal == "SELL":
                pnl_pct = -pnl_pct
            trades.append({
                "entry_time": entry_time,
                "exit_time": dates[-1],
                "direction": entry_signal,
                "entry_price": round(entry_price, 2),
                "exit_price": round(price, 2),
                "pnl_pct": round(pnl_pct, 2),
                "bars_held": len(closes) - next((s["index"] for s in signals_log[::-1] if s["signal"] == entry_signal), 0),
            })

        # Calculate metrics
        total_trades = len(trades)
        if total_trades == 0:
            return {
                "symbol": sym,
                "timeframe": interval,
                "bars_analyzed": len(closes),
                "total_trades": 0,
                "message": "No trades generated",
                "signals": signals_log[-50:],
            }

        # Filter out NaN trades
        trades = [t for t in trades if not (pd.isna(t["pnl_pct"]) or pd.isna(t["exit_price"]))]
        total_trades = len(trades)

        winning_trades = [t for t in trades if t["pnl_pct"] > 0]
        losing_trades = [t for t in trades if t["pnl_pct"] <= 0]
        win_rate = (len(winning_trades) / total_trades) * 100
        avg_win = sum(t["pnl_pct"] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t["pnl_pct"] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        total_return = sum(t["pnl_pct"] for t in trades if not pd.isna(t["pnl_pct"]))
        avg_return = total_return / total_trades
        max_win = max(t["pnl_pct"] for t in trades) if trades else 0
        max_loss = min(t["pnl_pct"] for t in trades) if trades else 0

        # Expectancy
        expectancy = (win_rate / 100) * avg_win + ((100 - win_rate) / 100) * avg_loss

        # Profit factor
        gross_profit = sum(t["pnl_pct"] for t in winning_trades)
        gross_loss = abs(sum(t["pnl_pct"] for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        return {
            "symbol": sym,
            "leading": self.selected_leading,
            "confirmations": self.selected_confirmations,
            "threshold": self.signal_threshold,
            "timeframe": interval,
            "bars_analyzed": len(closes),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_return, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "total_return": round(total_return, 2),
            "max_win": round(max_win, 2),
            "max_loss": round(max_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "∞",
            "expectancy": round(expectancy, 2),
            "buy_trades": len([t for t in trades if t["direction"] == "BUY"]),
            "sell_trades": len([t for t in trades if t["direction"] == "SELL"]),
            "trades": trades[-20:],
            "signals": signals_log,
        }

    def get_signal_history(self, symbol: str, days: int = 365, interval: str = "1d") -> Dict:
        """
        Get complete signal history for a symbol.
        Returns all BUY/SELL signals with detailed info.
        """
        bt = self.backtest(symbol, days, interval)
        all_signals = bt.get("signals", [])
        # Filter to only BUY/SELL
        trade_signals = [s for s in all_signals if s["signal"] in ("BUY", "SELL")]
        return {
            "symbol": symbol.upper(),
            "leading": bt.get("leading", self.selected_leading),
            "threshold": bt.get("threshold", self.signal_threshold),
            "timeframe": interval,
            "bars_analyzed": bt.get("bars_analyzed", 0),
            "total_signals": len(trade_signals),
            "buy_signals": len([s for s in trade_signals if s["signal"] == "BUY"]),
            "sell_signals": len([s for s in trade_signals if s["signal"] == "SELL"]),
            "performance": {
                "total_trades": bt.get("total_trades", 0),
                "win_rate": bt.get("win_rate", 0),
                "total_return": bt.get("total_return", 0),
                "profit_factor": bt.get("profit_factor", 0),
            },
            "signals": trade_signals,
        }

    def format_signal_history(self, symbol: str, days: int = 365, interval: str = "1d",
                              max_signals: int = 20) -> str:
        """Build a formatted message of signal history for Telegram."""
        hist = self.get_signal_history(symbol, days, interval)
        sym = hist["symbol"]
        total = hist["total_signals"]
        buys = hist["buy_signals"]
        sells = hist["sell_signals"]
        perf = hist["performance"]

        lines = [
            f"📊 *Signal History — {sym}* ({hist['timeframe']})",
            f"Lead: `{hist['leading']}` | Thresh: `{hist['threshold']}`",
            f"Bars: `{hist['bars_analyzed']}` | Total Signals: `{total}` (BUY: `{buys}` SELL: `{sells}`)",
        ]

        if perf["total_trades"] > 0:
            lines.append(
                f"Trades: `{perf['total_trades']}` | Win: `{perf['win_rate']}%` | "
                f"Return: `{perf['total_return']:+.2f}%` | PF: `{perf['profit_factor']}`"
            )

        lines.append("")
        lines.append(f"*Last {min(max_signals, total)} Signals*")
        lines.append("")

        signals = hist["signals"][-max_signals:]
        for s in signals:
            emoji = "🟢" if s["signal"] == "BUY" else "🔴"
            ts = str(s["timestamp"])[:10]
            lines.append(
                f"{emoji} `{ts}` {s['signal']} @ ₹{s['price']:,.2f} "
                f"| {s['leading'][:1]} | {s['confirmations']}"
            )

        lines.append("")
        lines.append(f"💡 `/signals_{sym.lower()} {days} {interval}` for full")
        return "\n".join(lines)


# ─── Singleton ──────────────────────────────────────────────────────

strategy_builder = StrategyBuilder()


# ─── Background Loop ────────────────────────────────────────────────

_sent_speedy_alerts: Dict[str, str] = {}  # symbol -> "BUY_ts" dedup

async def strategy_builder_loop():
    """Background loop: scan all symbols on 1-min timeframe every 3 min.
    Sends batch summary + individual alerts for Speedy+ALMA signals."""
    while True:
        try:
            signals = strategy_builder.scan_all()
            if signals:
                from app.services.telegram_notifier import telegram_notifier
                # Individual alerts for Speedy+ALMA
                for s in signals:
                    sym = s["symbol"]
                    sig = s["final_signal"]
                    ts = s.get("timestamp", "")
                    dedup_key = f"{sig}_{ts[:16]}"
                    last = _sent_speedy_alerts.get(sym)
                    if last == dedup_key:
                        continue
                    _sent_speedy_alerts[sym] = dedup_key
                    if len(_sent_speedy_alerts) > 500:
                        _sent_speedy_alerts.clear()
                    conf_count = s.get("confirmation_count", 0)
                    total_possible = s.get("total_possible", 7)
                    confidence = conf_count / max(total_possible, 1)
                    reasons = [c["name"] + ":" + c["direction"][0] for c in s.get("confirmations", [])]
                    explanation = (
                        f"Speedy+ALMA composite → {s.get('leading_direction', '?')} "
                        f"| {conf_count}/{total_possible} confirmations "
                        f"| Threshold: {s.get('signal_threshold', 3)}"
                    )
                    await telegram_notifier.send_signal_alert(
                        sym, sig, confidence, s.get("price", 0),
                        reasons[:3], explanation=explanation,
                    )

                # Batch summary
                msg = f"🧠 *1-min Strategy Signals*\n\nFound `{len(signals)}` active signals on `1m` chart\n\n"
                for s in signals[:5]:
                    emoji = "🟢" if s["final_signal"] == "BUY" else "🔴"
                    msg += f"{emoji} `{s['symbol']}` {s['final_signal']} ₹{s['price']:,.2f} ({s.get('leading_name','?')})\n"
                if len(signals) > 5:
                    msg += f"\n... and `{len(signals) - 5}` more\n"
                msg += "\n💡 `/strategy` for full dashboard"
                await telegram_notifier.send_message(msg)
        except Exception as e:
            print(f"Strategy builder loop error: {e}")
        await asyncio.sleep(180)  # 3 min between scans
