"""
Fast 1-minute backtest using vectorized numpy + numba JIT.
Computes ALL indicators once (vectorized per stock), then scans for signals.
"""
import sys, os, time, csv
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import numpy as np
import pandas as pd
import pickle
from numba import jit

from app.services.market_structure import (
    swing_highs, swing_lows, liquidity_sweep, market_structure,
    fair_value_gap, order_blocks, trendline_bounce, structure_direction,
)

REPORT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")
CACHE_1M = os.path.join(os.path.dirname(__file__), "data", "ohlc_cache_1m")
DAILY_CACHE = os.path.join(os.path.dirname(__file__), "data", "ohlc_cache")
os.makedirs(REPORT_DIR, exist_ok=True)

MIN_BARS = 30
DEFAULT_CONFS = ["EMA 20", "EMA 50", "MACD", "RSI", "Volume", "Price Action", "Liq Sweep", "BOS/CHoCH", "FVG", "Order Blocks", "Trendline"]


# ─── Numba-jitted helper functions ──────────────────────────────────

@jit(nopython=True)
def _ema(arr, period):
    """Fast EMA using loop, returns array of same length."""
    out = np.empty_like(arr)
    out[:] = np.nan
    alpha = 2.0 / (period + 1)
    for i in range(len(arr)):
        if np.isnan(arr[i]):
            out[i] = np.nan
        elif i == 0:
            out[i] = arr[i]
        else:
            out[i] = arr[i] * alpha + out[i-1] * (1 - alpha)
    return out

@jit(nopython=True)
def _sma(arr, period):
    out = np.empty_like(arr)
    out[:] = np.nan
    for i in range(len(arr)):
        if i >= period - 1:
            out[i] = np.mean(arr[i-period+1:i+1])
    return out

@jit(nopython=True)
def _rma(arr, period):
    """Wilder's RMA (similar to EMA but alpha=1/period)."""
    out = np.empty_like(arr)
    out[:] = np.nan
    alpha = 1.0 / period
    for i in range(len(arr)):
        if np.isnan(arr[i]):
            out[i] = np.nan
        elif i == 0:
            out[i] = arr[i]
        else:
            out[i] = arr[i] * alpha + out[i-1] * (1 - alpha)
    return out

@jit(nopython=True)
def _rsi_numba(c, period=14):
    """RSI series for all bars."""
    n = len(c)
    rsi = np.empty(n)
    rsi[:] = 50.0
    changes = np.diff(c)
    gains = np.maximum(changes, 0)
    losses = np.maximum(-changes, 0)
    # First RMA values
    avg_gain = np.empty(n)
    avg_loss = np.empty(n)
    avg_gain[:] = np.nan
    avg_loss[:] = np.nan
    for i in range(n):
        if i < period:
            continue
        if i == period:
            avg_gain[i] = np.mean(gains[:period])
            avg_loss[i] = np.mean(losses[:period])
        else:
            avg_gain[i] = (avg_gain[i-1] * (period - 1) + gains[i-1]) / period
            avg_loss[i] = (avg_loss[i-1] * (period - 1) + losses[i-1]) / period
        if avg_loss[i] > 1e-10:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100 - 100 / (1 + rs)
        else:
            rsi[i] = 100.0 if avg_gain[i] > 0 else 50.0
    return rsi

@jit(nopython=True)
def _atr_numba(h, l, c, period=14):
    """ATR series."""
    n = len(c)
    atr = np.empty(n)
    atr[:] = np.nan
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
    for i in range(n):
        if i < period:
            continue
        if i == period:
            atr[i] = np.mean(tr[1:period+1])
        else:
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr

@jit(nopython=True)
def _super_trend_numba(h, l, c, period=10, mult=3.0):
    """SuperTrend series: 1 = LONG, -1 = SHORT."""
    n = len(c)
    direction = np.zeros(n)
    atr = _atr_numba(h, l, c, period)
    upper = np.empty(n)
    lower = np.empty(n)
    upper[:] = np.nan
    lower[:] = np.nan
    hl2 = (h + l) / 2
    for i in range(n):
        if i < period:
            continue
        upper[i] = hl2[i] + mult * atr[i]
        lower[i] = hl2[i] - mult * atr[i]
        if i == period:
            direction[i] = 1  # LONG
        elif c[i] > upper[i-1]:
            direction[i] = 1
        elif c[i] < lower[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
        # Adjust bands
        if direction[i] == 1:
            lower[i] = max(lower[i], lower[i-1])
        else:
            upper[i] = min(upper[i], upper[i-1])
    return direction

@jit(nopython=True)
def _bollinger_dir(c, period=20, mult=2.0):
    """Bollinger direction: 1=above upper (overbought->SHORT), -1=below lower."""
    n = len(c)
    dirs = np.zeros(n)
    ma = _sma(c, period)
    for i in range(period, n):
        std = np.std(c[i-period+1:i+1])
        upper = ma[i] + mult * std
        lower = ma[i] - mult * std
        if c[i] > upper:
            dirs[i] = -1  # SHORT signal
        elif c[i] < lower:
            dirs[i] = 1  # LONG signal
    return dirs

@jit(nopython=True)
def _macd_dir(c, fast=12, slow=26, signal=9):
    """MACD direction: 1 = MACD above signal, -1 = below."""
    n = len(c)
    dirs = np.zeros(n)
    ema_f = _ema(c, fast)
    ema_s = _ema(c, slow)
    macd = ema_f - ema_s
    sig = _ema(macd, signal)
    for i in range(n):
        if np.isnan(sig[i]) or np.isnan(macd[i]):
            continue
        dirs[i] = 1 if macd[i] > sig[i] else -1 if macd[i] < sig[i] else 0
    return dirs

@jit(nopython=True)
def _ema_dir(c, period=20):
    """EMA direction: 1 = price above EMA, -1 = below."""
    n = len(c)
    dirs = np.zeros(n)
    e = _ema(c, period)
    for i in range(n):
        if np.isnan(e[i]):
            continue
        dirs[i] = 1 if c[i] > e[i] else -1 if c[i] < e[i] else 0
    return dirs

@jit(nopython=True)
def _stoch_dir(h, l, c, k=14, d=3):
    """Stochastic direction: 1 = K > D (long), -1 = K < D (short)."""
    n = len(c)
    dirs = np.zeros(n)
    k_vals = np.empty(n)
    k_vals[:] = np.nan
    for i in range(k, n):
        ll = np.min(l[i-k+1:i+1])
        hh = np.max(h[i-k+1:i+1])
        if hh > ll:
            k_vals[i] = (c[i] - ll) / (hh - ll) * 100
        else:
            k_vals[i] = 50
    d_vals = _sma(k_vals, d)
    for i in range(n):
        if np.isnan(k_vals[i]) or np.isnan(d_vals[i]):
            continue
        dirs[i] = 1 if k_vals[i] > d_vals[i] else -1 if k_vals[i] < d_vals[i] else 0
    return dirs

# ─── Computed indicator series ──────────────────────────────────────

INDICATOR_SERIES = {
    "RSI V2": lambda o, h, l, c, v: np.where(_rsi_numba(c) > 50, 1, np.where(_rsi_numba(c) < 50, -1, 0)),
    "SuperTrend": lambda o, h, l, c, v: _super_trend_numba(h, l, c),
    "HMA": lambda o, h, l, c, v: _ema_dir(c, 20),
    "MACD": lambda o, h, l, c, v: _macd_dir(c),
    "Stochastic V2": lambda o, h, l, c, v: _stoch_dir(h, l, c),
    "ZLEMA": lambda o, h, l, c, v: _ema_dir(c, 14),
    "ALMA": lambda o, h, l, c, v: _ema_dir(c, 9),
    "TEMA": lambda o, h, l, c, v: _ema_dir(c, 20),
    "DEMA": lambda o, h, l, c, v: _ema_dir(c, 14),
    "Williams %R V2": lambda o, h, l, c, v: -_stoch_dir(h, l, c, 14),
    "Momentum": lambda o, h, l, c, v: np.where(c > _sma(c, 14), 1, np.where(c < _sma(c, 14), -1, 0)),
    "ROC": lambda o, h, l, c, v: np.where(c / np.maximum(c, 1e-10) > 1.01, 1, np.where(c / np.maximum(c, 1e-10) < 0.99, -1, 0)),
    # ── Market Structure (SMC/ICT) ──
    "Liq Sweep": lambda o, h, l, c, v: liquidity_sweep(o, h, l, c, 10),
    "BOS/CHoCH": lambda o, h, l, c, v: market_structure(h, l, 3, 3),
    "FVG": lambda o, h, l, c, v: fair_value_gap(o, h, l, c),
    "Order Blocks": lambda o, h, l, c, v: order_blocks(o, h, l, c, v),
    "Trendline": lambda o, h, l, c, v: trendline_bounce(h, l, c, 15),
    "Struct Dir": lambda o, h, l, c, v: structure_direction(o, h, l, c, v, 10),
}

# Market structure indicators get 2x weight in composite vote
MARKET_STRUCTURE_NAMES = {"Liq Sweep", "BOS/CHoCH", "FVG", "Order Blocks", "Trendline", "Struct Dir"}

# Confirmation series (return 1 for LONG confirmed, -1 for SHORT, 0 for none)
CONFIRM_SERIES = {
    "EMA 20": lambda o, h, l, c, v: _ema_dir(c, 20),
    "EMA 50": lambda o, h, l, c, v: _ema_dir(c, 50),
    "EMA 100": lambda o, h, l, c, v: _ema_dir(c, 100),
    "EMA 200": lambda o, h, l, c, v: _ema_dir(c, 200),
    "MACD": lambda o, h, l, c, v: _macd_dir(c),
    "RSI": lambda o, h, l, c, v: np.where(_rsi_numba(c) > 50, 1, np.where(_rsi_numba(c) < 50, -1, 0)),
    "Bollinger": lambda o, h, l, c, v: _bollinger_dir(c),
    "Stochastic": lambda o, h, l, c, v: _stoch_dir(h, l, c),
    # ── Market Structure Confirmations ──
    "Liq Sweep": lambda o, h, l, c, v: liquidity_sweep(o, h, l, c, 10),
    "BOS/CHoCH": lambda o, h, l, c, v: market_structure(h, l, 3, 3),
    "FVG": lambda o, h, l, c, v: fair_value_gap(o, h, l, c),
    "Order Blocks": lambda o, h, l, c, v: order_blocks(o, h, l, c, v),
    "Trendline": lambda o, h, l, c, v: trendline_bounce(h, l, c, 15),
}


# ─── Exit Strategies ────────────────────────────────────────────────

EXIT_STRATEGIES = [
    {"name": "Fixed 1:2",  "sl_type": "fixed", "risk_pct": 0.5, "reward_pct": 1.0},
    {"name": "Fixed 1:3",  "sl_type": "fixed", "risk_pct": 0.5, "reward_pct": 1.5},
    {"name": "Fixed 1:4",  "sl_type": "fixed", "risk_pct": 0.5, "reward_pct": 2.0},
    {"name": "Breakeven",  "sl_type": "breakeven", "risk_pct": 0.5, "reward_pct": 1.0},
    {"name": "Step Trail", "sl_type": "step_trail", "risk_pct": 0.5, "reward_pct": 1.0},
    {"name": "Wide 1:3",   "sl_type": "fixed", "risk_pct": 1.0, "reward_pct": 3.0},
    {"name": "Wide 1:4",   "sl_type": "fixed", "risk_pct": 1.0, "reward_pct": 4.0},
]


def simulate_exit(closes, entry_idx, entry_price, direction, strategy):
    if direction == "BUY":
        return _sim_long(closes, entry_idx, entry_price, strategy)
    else:
        return _sim_short(closes, entry_idx, entry_price, strategy)


@jit(nopython=True)
def _sim_long_numba(c, idx, entry, sl_type, risk, reward, max_hold=40):
    """Numba-friendly exit sim."""
    end = min(idx + max_hold, len(c))
    moves = c[idx+1:end]
    if len(moves) == 0:
        return ("TIMEOUT", c[min(idx+max_hold-1, len(c)-1)], idx+max_hold - idx, 0.0)

    if sl_type == 0:  # fixed
        sl = entry * (1 - risk / 100)
        tp = entry * (1 + reward / 100)
    elif sl_type == 1:  # atr_trail
        atr_val = _calc_atr_numba(c, idx)
        sl = entry - atr_val * 2.0 if atr_val > 0 else entry * 0.995
        tp = entry + atr_val * 4.0 if atr_val > 0 else entry * 1.01
    elif sl_type == 2:  # breakeven
        sl = entry * (1 - risk / 100)
        tp = entry * (1 + reward / 100)
        be = entry + (tp - entry) * 0.4
    elif sl_type == 3:  # step_trail
        sl = entry * (1 - risk / 100)
        tp = entry * (1 + reward / 100)
    else:
        return ("NONE", entry, 0, 0.0)

    for j, p in enumerate(moves):
        if sl_type == 1:  # atr_trail
            atr_val = _calc_atr_numba(c, idx + j)
            if atr_val > 0:
                sl = max(sl, p - atr_val * 1.5)
        elif sl_type == 2:  # breakeven
            if p >= be:
                sl = max(sl, entry)
        elif sl_type == 3:  # step_trail
            prog = (p - entry) / (tp - entry) if tp != entry else 0
            if prog >= 0.3:
                sl = max(sl, entry * (1 - risk / 300))
            if prog >= 0.6:
                sl = max(sl, entry)

        if p <= sl:
            return ("SL", p, j+1, (p - entry) / entry * 100)
        if p >= tp:
            return ("TP", p, j+1, (p - entry) / entry * 100)

    fp = c[min(idx + max_hold, len(c)-1)]
    pnl = (fp - entry) / entry * 100
    return ("TIMEOUT", fp, max_hold, pnl)


@jit(nopython=True)
def _calc_atr_numba(c, idx, period=14):
    if idx < period:
        return 0.0
    total = 0.0
    for i in range(idx - period, idx):
        total += abs(c[i+1] - c[i])
    return total / period


def _sim_long(closes, entry_idx, entry_price, strategy):
    sl_type_map = {"fixed": 0, "atr_trail": 1, "breakeven": 2, "step_trail": 3}
    st = sl_type_map.get(strategy["sl_type"], 0)
    risk = strategy.get("risk_pct", 0.5)
    reward = strategy.get("reward_pct", 1.0)
    result = _sim_long_numba(np.array(closes, dtype=np.float64), entry_idx,
                             entry_price, st, risk, reward)
    return result


def _sim_short(closes, entry_idx, entry_price, strategy):
    # Symmetric - swap direction logic
    sl_type_map = {"fixed": 0, "atr_trail": 1, "breakeven": 2, "step_trail": 3}
    st = sl_type_map.get(strategy["sl_type"], 0)
    risk = strategy.get("risk_pct", 0.5)
    reward = strategy.get("reward_pct", 1.0)
    result = _sim_long_numba(np.array(closes, dtype=np.float64), entry_idx,
                             entry_price, st, risk, reward)
    # For short: swap SL/TP and invert P&L
    ext, exp, bars, pnl = result
    if ext == "SL":
        ext = "TP"  # On short, hitting SL means price went DOWN
    elif ext == "TP":
        ext = "SL"
    return (ext, exp, bars, -pnl)


# ─── Vectorized Backtest ───────────────────────────────────────────

def backtest_stock_fast(sym, df):
    """Backtest one stock using pre-computed series."""
    try:
        o = np.array([float(x) for x in df['Open']], dtype=np.float64)
        h = np.array([float(x) for x in df['High']], dtype=np.float64)
        l = np.array([float(x) for x in df['Low']], dtype=np.float64)
        c = np.array([float(x) for x in df['Close']], dtype=np.float64)
        v = np.array([int(x) for x in df['Volume']], dtype=np.float64)
        dates = list(df.index)
        n = len(c)
    except Exception:
        return []

    if n < MIN_BARS + 50:
        return []

    # Pre-compute ALL indicator series at once
    ind_series = {}
    for name, func in INDICATOR_SERIES.items():
        try:
            series = func(o, h, l, c, v)
            ind_series[name] = series
        except Exception:
            pass

    # Pre-compute confirmation series
    conf_series = {}
    for name, func in CONFIRM_SERIES.items():
        try:
            series = func(o, h, l, c, v)
            conf_series[name] = series
        except Exception:
            pass

    ema200_series = _ema(c, 200)

    # ── Super Composite: weighted consensus of ALL indicators ──
    n_indicators = len(ind_series)
    composite = np.zeros(n)
    market_struct_series = {}
    for ind_name, dir_series in ind_series.items():
        weight = 2.0 if ind_name in MARKET_STRUCTURE_NAMES else 1.0
        composite += weight * dir_series
        if ind_name in MARKET_STRUCTURE_NAMES:
            market_struct_series[ind_name] = dir_series

    # Also compute classic-only and structure-only sub-composites
    classic_composite = np.zeros(n)
    struct_composite = np.zeros(n)
    for ind_name, dir_series in ind_series.items():
        if ind_name in MARKET_STRUCTURE_NAMES:
            struct_composite += 2.0 * dir_series
        else:
            classic_composite += dir_series

    results = []
    conf_names = DEFAULT_CONFS
    thresholds = [2, 3, 4]
    # Composite thresholds (sum of weighted directions across 18 indicators)
    composite_thresholds = [3.0, 5.0, 7.0]

    # Scan bars
    for i in range(MIN_BARS, n):
        # Check trend filter
        if not np.isnan(ema200_series[i]):
            trend = 1 if c[i] > ema200_series[i] else -1
        else:
            trend = 0

        for ind_name, dir_series in ind_series.items():
            ld_dir = dir_series[i]
            if ld_dir == 0:
                continue

            # Trend filter
            if trend != 0:
                if ld_dir == 1 and trend == -1:
                    continue
                if ld_dir == -1 and trend == 1:
                    continue

            # Confirmations
            conf_long = conf_short = 0
            for conf_name in conf_names:
                cs = conf_series.get(conf_name)
                if cs is None:
                    continue
                cd = cs[i]
                if cd == 1:
                    conf_long += 1
                elif cd == -1:
                    conf_short += 1

            for th in thresholds:
                signal = None
                if ld_dir == 1 and conf_long >= th:
                    signal = "BUY"
                elif ld_dir == -1 and conf_short >= th:
                    signal = "SELL"
                if not signal:
                    continue

                entry_price = c[i]
                dt = dates[i]
                ts = dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, 'strftime') else str(dt)[:16]

                for strategy in EXIT_STRATEGIES:
                    ext, exp, bars, pnl = simulate_exit(c, i, entry_price, signal, strategy)
                    results.append({
                        "symbol": sym, "indicator": ind_name,
                        "confirmations": "+".join(conf_names),
                        "threshold": th, "signal": signal,
                        "entry_price": round(entry_price, 2),
                        "exit_strategy": strategy["name"],
                        "exit_type": ext, "exit_price": round(exp, 2),
                        "bars_held": bars, "pnl_pct": round(pnl, 2),
                        "is_win": 1 if pnl > 0 else 0, "timestamp": ts,
                    })

        # ── Elite Ensemble: force consensus between strong indicators ──
        c_score = composite[i]
        st_score = struct_composite[i]
        dirs = {name: ind_series[name][i] for name in ind_series}

        ms_dir = st_score  # market structure composite
        ls_dir = dirs.get("Liq Sweep", 0)
        bs_dir = dirs.get("BOS/CHoCH", 0)
        st_dir = dirs.get("SuperTrend", 0)
        rs_dir = dirs.get("RSI V2", 0)
        wr_dir = dirs.get("Williams %R V2", 0)
        fvg_dir = dirs.get("FVG", 0)
        ob_dir = dirs.get("Order Blocks", 0)

        elite_configs = []

        # Elite Triple: Liq Sweep + BOS/CHoCH + SuperTrend (most specific)
        if ls_dir != 0 and ls_dir == bs_dir == st_dir:
            elite_configs.append(("Elite:Liq+BOS+ST", ls_dir, 0))
        # Elite Triple: Liq Sweep + RSI + Williams agree
        if ls_dir != 0 and ls_dir == rs_dir == wr_dir:
            elite_configs.append(("Elite:Liq+RSI+WR", ls_dir, 0))
        # Elite Triple: all 3 structure indicators agree
        if ls_dir != 0 and bs_dir != 0 and fvg_dir != 0 and ls_dir == bs_dir == fvg_dir:
            elite_configs.append(("Elite:Struct3", ls_dir, 0))
        # Elite Pair: Liq Sweep + BOS/CHoCH
        if ls_dir != 0 and ls_dir == bs_dir:
            elite_configs.append(("Elite:Liq+BOS", ls_dir, 0))
        # Elite Pair: Liq Sweep + SuperTrend
        if ls_dir != 0 and ls_dir == st_dir:
            elite_configs.append(("Elite:Liq+ST", ls_dir, 0))
        # Elite Pair: BOS/CHoCH + SuperTrend
        if bs_dir != 0 and bs_dir == st_dir:
            elite_configs.append(("Elite:BOS+ST", bs_dir, 0))
        # Elite Pair: FVG + Order Blocks
        if fvg_dir != 0 and fvg_dir == ob_dir and ob_dir != 0:
            elite_configs.append(("Elite:FVG+OB", fvg_dir, 0))
        # Elite Trio: any 2 of (Liq Sweep, BOS/CHoCH, FVG) agree
        struct_dirs = [d for d in [ls_dir, bs_dir, fvg_dir] if d != 0]
        if len(struct_dirs) >= 2 and all(d == struct_dirs[0] for d in struct_dirs[:2]):
            elite_configs.append(("Elite:Struct2", struct_dirs[0], 0))
        # Elite Cross: structure composite + trend agree
        if ms_dir >= 2 and st_dir == 1:
            elite_configs.append(("Elite:Str+Trend", 1, 0))
        elif ms_dir <= -2 and st_dir == -1:
            elite_configs.append(("Elite:Str+Trend", -1, 0))
        # Elite Cross: structure composite + momentum agree
        if ms_dir >= 2 and rs_dir == 1:
            elite_configs.append(("Elite:Str+Mom", 1, 0))
        elif ms_dir <= -2 and rs_dir == -1:
            elite_configs.append(("Elite:Str+Mom", -1, 0))

        # Dedup: only the first (most specific) elite combo per bar+dir
        elite_conf_long = conf_long
        elite_conf_short = conf_short
        elite_thresholds = [0, 1, 2, 3]
        seen_elite = set()
        for e_name, e_dir, _ in elite_configs:
            if e_dir == 0 or (i, e_dir) in seen_elite:
                continue
            seen_elite.add((i, e_dir))
            signal = "BUY" if e_dir == 1 else "SELL"

            # Confirmation threshold sweep for elite combos
            for eth in elite_thresholds:
                if signal == "BUY" and elite_conf_long < eth:
                    continue
                if signal == "SELL" and elite_conf_short < eth:
                    continue

                # EMA 200 trend filter
                if trend != 0:
                    if signal == "BUY" and trend == -1:
                        continue
                    if signal == "SELL" and trend == 1:
                        continue

                entry_price = c[i]
                dt = dates[i]
                ts = dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, 'strftime') else str(dt)[:16]

                for strategy in EXIT_STRATEGIES:
                    ext, exp, bars, pnl = simulate_exit(c, i, entry_price, signal, strategy)
                    results.append({
                        "symbol": sym, "indicator": e_name,
                        "confirmations": "elite",
                        "threshold": eth,
                        "signal": signal,
                        "entry_price": round(entry_price, 2),
                        "exit_strategy": strategy["name"],
                        "exit_type": ext, "exit_price": round(exp, 2),
                        "bars_held": bars, "pnl_pct": round(pnl, 2),
                        "is_win": 1 if pnl > 0 else 0, "timestamp": ts,
                    })

    return results


def build_report(all_signals, timeframe_label, total_time, stock_count):
    if not all_signals:
        print("  No signals generated", flush=True)
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rpt = os.path.join(REPORT_DIR, f"backtest_{timeframe_label}_{ts}.md")
    csv_f = os.path.join(REPORT_DIR, f"backtest_{timeframe_label}_{ts}.csv")

    rows = [s for batch in all_signals for s in batch]
    with open(csv_f, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    from collections import defaultdict
    aggr = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0.0})
    for r in rows:
        key = (r["indicator"], r["threshold"], r["exit_strategy"])
        aggr[key]["total"] += 1
        aggr[key]["wins"] += r["is_win"]
        aggr[key]["pnl"] += r["pnl_pct"]

    items = []
    for (ind, th, ext), d in aggr.items():
        wr = d["wins"] / d["total"] * 100 if d["total"] > 0 else 0
        avg = d["pnl"] / d["total"] if d["total"] > 0 else 0
        items.append({"indicator": ind, "threshold": th, "exit": ext,
                       "total": d["total"], "wins": d["wins"],
                       "win_rate": wr, "avg_pnl": avg})

    items.sort(key=lambda x: x["win_rate"], reverse=True)

    lines = [
        f"# Fast Backtest — {timeframe_label}",
        f"",
        f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Stocks**: {stock_count} | **Indicators**: {len(INDICATOR_SERIES)} | **Exits**: {len(EXIT_STRATEGIES)}",
        f"**Signals**: {len(rows)}",
        f"**Duration**: {total_time:.0f}s",
        f"**Confirmations**: {', '.join(DEFAULT_CONFS)}",
        f"**Trend Filter**: EMA 200",
        f"",
        f"## All Results (sorted by win rate)",
        f"| Indicator | Thresh | Exit | Signals | Wins | Losses | Win Rate | Avg P&L |",
        f"|-----------|--------|------|---------|------|--------|----------|---------|",
    ]
    for it in items:
        lines.append(
            f"| {it['indicator']} | {it['threshold']} | {it['exit']} | {it['total']} | "
            f"{it['wins']} | {it['total']-it['wins']} | {it['win_rate']:.1f}% | {it['avg_pnl']:+.3f}% |"
        )

    lines.append("")
    lines.append("## Top 20 Combos (min 20 signals)")
    lines.append("")
    top = [x for x in items if x["total"] >= 20][:20]
    for i, it in enumerate(top, 1):
        lines.append(
            f"{i}. **{it['indicator']}** (th={it['threshold']}) + {it['exit']} → "
            f"{it['win_rate']:.1f}% WR ({it['wins']}/{it['total']}), "
            f"Avg: {it['avg_pnl']:+.2f}%"
        )

    with open(rpt, "w") as f:
        f.write("\n".join(lines))
    print(f"  Report: {rpt}", flush=True)
    print(f"  CSV: {csv_f}", flush=True)
    for it in top[:5]:
        print(f"  🏆 {it['indicator']} (th={it['threshold']}) + {it['exit']}: "
              f"{it['win_rate']:.1f}% ({it['wins']}/{it['total']})", flush=True)


def main():
    start = time.time()
    print("=" * 60, flush=True)
    print("Loading 1-minute data...", flush=True)

    data = {}
    for f in sorted(os.listdir(CACHE_1M)):
        if not f.endswith(".pkl"):
            continue
        sym = f.replace(".pkl", "").upper()
        try:
            df = pickle.load(open(os.path.join(CACHE_1M, f), "rb"))
            if len(df) >= MIN_BARS:
                data[sym] = df
        except Exception:
            pass
    print(f"  Loaded {len(data)} stocks", flush=True)

    liquid = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN",
              "BHARTIARTL", "ITC", "LT", "WIPRO", "AXISBANK", "KOTAKBANK",
              "MARUTI", "TITAN", "ADANIENT", "NTPC", "POWERGRID",
              "HCLTECH", "BAJFINANCE", "HINDUNILVR"]
    stock_data = {s: data[s] for s in liquid if s in data}
    print(f"  Using {len(stock_data)} liquid stocks", flush=True)
    print()

    # JIT warmup
    print("  Warming up numba JIT...", flush=True)
    c_test = np.array([100.0 + i * 0.1 for i in range(100)], dtype=np.float64)
    h_test = np.array([101.0 + i * 0.15 for i in range(100)], dtype=np.float64)
    l_test = np.array([99.0 + i * 0.05 for i in range(100)], dtype=np.float64)
    o_test = np.array([99.5 + i * 0.1 for i in range(100)], dtype=np.float64)
    v_test = np.array([1000 + i * 10 for i in range(100)], dtype=np.float64)
    _ = _rsi_numba(c_test)
    _ = _super_trend_numba(h_test, l_test, c_test)
    _ = swing_highs(h_test, 3, 3)
    _ = swing_lows(l_test, 3, 3)
    _ = liquidity_sweep(o_test, h_test, l_test, c_test, 10)
    _ = market_structure(h_test, l_test, 3, 3)
    _ = fair_value_gap(o_test, h_test, l_test, c_test)
    _ = order_blocks(o_test, h_test, l_test, c_test, v_test)
    _ = trendline_bounce(h_test, l_test, c_test, 15)
    print("  JIT ready.", flush=True)
    print()

    # ── Load daily cache (much longer history than resampling 1-min) ──
    daily_data = {}
    for f in sorted(os.listdir(DAILY_CACHE)):
        if not f.endswith(".pkl"):
            continue
        sym = f.replace(".pkl", "").upper()
        if sym not in stock_data:
            continue
        try:
            df = pickle.load(open(os.path.join(DAILY_CACHE, f), "rb"))
            if len(df) >= MIN_BARS:
                daily_data[sym] = df
        except Exception:
            pass
    print(f"  Daily cache: {len(daily_data)} stocks", flush=True)

    # ── Run on multiple timeframes ──
    timeframes = {
        "1MIN": stock_data,
        "1D": daily_data,
    }

    # Resample to 5-min and 15-min
    print("Resampling to higher timeframes...", flush=True)
    for sym, df in list(stock_data.items()):
        df.index = pd.to_datetime(df.index)
        df_5m = df.resample("5min").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum"
        }).dropna()
        if len(df_5m) >= MIN_BARS:
            timeframes.setdefault("5MIN", {})[sym] = df_5m
        df_15m = df.resample("15min").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum"
        }).dropna()
        if len(df_15m) >= MIN_BARS:
            timeframes.setdefault("15MIN", {})[sym] = df_15m

    for tf_name, tf_data in timeframes.items():
        print(f"\n  Running on {tf_name} ({len(tf_data)} stocks)...", flush=True)
        tf_items = list(tf_data.items())
        all_signals = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            fut_map = {pool.submit(backtest_stock_fast, sym, df): sym for sym, df in tf_items}
            done = 0
            for f in as_completed(fut_map):
                try:
                    sigs = f.result()
                    if sigs:
                        all_signals.extend(sigs)
                except Exception as e:
                    print(f"    Error: {e}", flush=True)
                done += 1
                if done % 5 == 0 or done == len(tf_items):
                    print(f"    [{done}/{len(tf_items)}] {time.time()-start:.0f}s — {len(all_signals)} signals", flush=True)

        elapsed = time.time() - start
        print(f"  {tf_name} done in {elapsed:.0f}s", flush=True)
        build_report([all_signals] if all_signals else [], tf_name, elapsed, len(tf_data))
        print()

    total = time.time() - start
    print(f"\n{'=' * 60}", flush=True)
    print(f"ALL DONEl {total:.0f}s", flush=True)
    print("=" * 60, flush=True)

    print(f"\n{'=' * 60}", flush=True)
    print(f"ALL DONE — {time.time() - start:.0f}s", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
