"""
Liquidity Sweep + Confirmation Filters Optimization Backtest
=============================================================
Tests liquidity sweep setups on daily data with various confirmation
filter combinations to find the highest-win-rate entry criteria.

Combines concepts from:
  - market_structure.py (JIT-compiled sweep, BOS, FVG, OB, trendline)
  - strategy_builder.py (EMA trend, volume confirmation, composites)
  - liquidity_sweep_daily.py (daily swing sweeps)

Each combo is tested across all 149 stocks (180-day daily data).
Reports WR, RR, PF, total return per combo.
"""
import sys, os, pickle, time, json, math
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from app.data.stocks import INDIAN_STOCKS
import pandas as pd
import numpy as np

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "ohlc_180_cache")
RPT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")
os.makedirs(RPT_DIR, exist_ok=True)

MIN_BARS = 50
MAX_STOP_PCT = 8.0
MIN_RR = 1.5
MIN_CANDLES = 15


# ─── Swing / Sweep Detection (pure Python, same logic as .py versions) ─────

def swing_highs(h, left=3, right=3):
    n = len(h); r = [0.0]*n
    for i in range(left, n-right):
        if all(h[j]<h[i] for j in range(i-left,i) if j>=0) and all(h[j]<h[i] for j in range(i+1,i+right+1) if j<n): r[i]=1.0
    return r

def swing_lows(l, left=3, right=3):
    n = len(l); r = [0.0]*n
    for i in range(left, n-right):
        if all(l[j]>l[i] for j in range(i-left,i) if j>=0) and all(l[j]>l[i] for j in range(i+1,i+right+1) if j<n): r[i]=1.0
    return r

def prev_swing(i, arr):
    for j in range(i-1,-1,-1):
        if j<len(arr) and arr[j]==1.0: return j
    return None

def ema(arr, period):
    if len(arr) < period: return [arr[-1]]*len(arr)
    k = 2 / (period + 1)
    r = [arr[0]]
    for v in arr[1:]:
        r.append(v * k + r[-1] * (1 - k))
    return r

def sma(arr, period):
    if len(arr) < period: return [sum(arr)/len(arr)]*len(arr)
    r = []
    for i in range(len(arr)):
        if i < period - 1: r.append(sum(arr[:i+1])/(i+1))
        else: r.append(sum(arr[i-period+1:i+1])/period)
    return r

def rsi(arr, period=14):
    if len(arr) < period + 1: return [50]*len(arr)
    gains, losses = [], []
    for i in range(1, len(arr)):
        d = arr[i] - arr[i-1]
        gains.append(d if d > 0 else 0)
        losses.append(-d if d < 0 else 0)
    avg_g = sum(gains[:period])/period; avg_l = sum(losses[:period])/period
    r = []
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        rs = avg_g / avg_l if avg_l > 0 else 999
        r.append(100 - 100/(1+rs))
    return [50]*period + r

def fair_value_gap(h, l):
    """Detect FVG: gap between 3 consecutive candles. Returns array of 1/-1/0."""
    n = len(l); r = [0.0]*n
    for i in range(2, n):
        if l[i] > h[i-2]: r[i] = 1
        elif h[i] < l[i-2]: r[i] = -1
    return r

def detect_liquidity_sweep(i, h, l, c, lookback=10):
    """Check if bar i is a liquidity sweep. Returns 1 (bullish), -1 (bearish), 0."""
    if i < lookback + 1: return 0
    recent_high = max(h[i-lookback:i])
    recent_low = min(l[i-lookback:i])
    if h[i] > recent_high and c[i] < recent_high: return -1
    if l[i] < recent_low and c[i] > recent_low: return 1
    return 0

def detect_bos_choch(i, h, l, swing_h, swing_l):
    """Check if bar i is a BOS/CHoCH. Returns 1/2/-1/-2/0 (same as market_structure.py)."""
    sh_idx = [j for j in range(i) if swing_h[j] == 1]
    sl_idx = [j for j in range(i) if swing_l[j] == 1]
    if len(sh_idx) < 2 or len(sl_idx) < 2: return 0
    h1 = h[sh_idx[-2]]; h2 = h[sh_idx[-1]]
    l1 = l[sl_idx[-2]]; l2 = l[sl_idx[-1]]
    uptrend = h2 > h1 and l2 > l1
    downtrend = h2 < h1 and l2 < l1
    if uptrend and h[i] > h2: return 1
    if downtrend and l[i] < l2: return -1
    if h2 > h1 and l2 < l1: return -2
    if h2 < h1 and l2 > l1: return 2
    return 0

def detect_order_block(i, o, h, l, c, lookback=10):
    """Simple OB detection: previous candle before a strong impulse."""
    if i < 3: return 0
    for j in range(i-1, max(i-lookback, 1), -1):
        body = abs(c[j] - o[j])
        prev_body = abs(c[j-1] - o[j-1])
        if body < 0.3: continue
        if c[j] > o[j] and body > prev_body * 1.5:
            ob_h = max(o[j-1], c[j-1]); ob_l = min(o[j-1], c[j-1])
            if l[i] <= ob_h and h[i] >= ob_l: return 1
        elif c[j] < o[j] and body > prev_body * 1.5:
            ob_h = max(o[j-1], c[j-1]); ob_l = min(o[j-1], c[j-1])
            if l[i] <= ob_h and h[i] >= ob_l: return -1
    return 0


# ─── Confirmation Filters ──────────────────────────────────────────────

def conf_ema_trend(i, c, period=50):
    """Check if close is on correct side of EMA for sweep direction."""
    e = ema(c, period)
    return c[i] > e[i] if len(e) > i else False  # bullish check

def conf_volume_spike(i, v, mult=1.5):
    """Check if volume is above average."""
    if i < 20: return True
    avg_v = sum(v[i-20:i]) / 20
    return v[i] >= mult * avg_v

def conf_rsi_extreme(i, c, period=14, threshold=30):
    """Check RSI for oversold (< threshold for BUY) or overbought (> 100-threshold for SELL)."""
    r = rsi(c, period)
    if i >= len(r): return False
    return r[i] < threshold  # bullish oversold check

def conf_fvg(i, h, l):
    """Check if FVG exists at this bar."""
    fvg = fair_value_gap(h, l)
    return abs(fvg[i]) > 0 if i < len(fvg) else False

def conf_market_structure(i, h, l, swing_h, swing_l):
    """Check if BOS/CHoCH aligns with sweep direction."""
    return detect_bos_choch(i, h, l, swing_h, swing_l) != 0

def conf_order_block(i, o, h, l, c):
    """Check if sweep retests an order block."""
    return detect_order_block(i, o, h, l, c) != 0

def conf_ema50_trend(i, c):
    e = ema(c, 50)
    return c[i] > e[i] if len(e) > i else False

def conf_ema200_trend(i, c):
    e = ema(c, 200)
    return c[i] > e[i] if len(e) > i else False

def conf_liqhunter(i, o, h, l, c, v, lookback=60, wick_ratio=2.0):
    """Check if a liq wick level was swept (from strategy_builder LiqHunter logic)."""
    start = max(0, i - lookback)
    for j in range(i-1, start-1, -1):
        body = abs(c[j] - o[j])
        if body < 1e-10: continue
        upper_wick = h[j] - max(c[j], o[j])
        lower_wick = min(c[j], o[j]) - l[j]
        if c[j] > o[j] and lower_wick >= wick_ratio * body:
            level = l[j]
            if l[i] <= level and c[i] > level: return True
        elif c[j] < o[j] and upper_wick >= wick_ratio * body:
            level = h[j]
            if h[i] >= level and c[i] < level: return True
    return False


# ─── Trade Simulation ──────────────────────────────────────────────────

def sim(entry_idx, direction, entry, stop, target, closes, highs, lows):
    be = False; cur_stop = stop
    for j in range(entry_idx + 1, len(closes)):
        ch, cl = highs[j], lows[j]
        if not be:
            if direction == "BUY" and ch >= entry + (entry - stop):
                be = True; cur_stop = entry
            elif direction == "SELL" and cl <= entry - (stop - entry):
                be = True; cur_stop = entry
        if direction == "BUY" and cl <= cur_stop:
            return {"exit_idx": j, "exit_price": round(cur_stop, 2), "pnl_pct": round((cur_stop - entry) / entry * 100, 2), "exit_reason": "stop", "be_triggered": be, "bars_held": j - entry_idx}
        if direction == "SELL" and ch >= cur_stop:
            return {"exit_idx": j, "exit_price": round(cur_stop, 2), "pnl_pct": round((entry - cur_stop) / entry * 100, 2), "exit_reason": "stop", "be_triggered": be, "bars_held": j - entry_idx}
        if direction == "BUY" and ch >= target:
            return {"exit_idx": j, "exit_price": round(target, 2), "pnl_pct": round((target - entry) / entry * 100, 2), "exit_reason": "target", "be_triggered": be, "bars_held": j - entry_idx}
        if direction == "SELL" and cl <= target:
            return {"exit_idx": j, "exit_price": round(target, 2), "pnl_pct": round((entry - target) / entry * 100, 2), "exit_reason": "target", "be_triggered": be, "bars_held": j - entry_idx}
    final = closes[-1]
    pnl = ((final - entry) / entry * 100) if direction == "BUY" else ((entry - final) / entry * 100)
    return {"exit_idx": len(closes) - 1, "exit_price": round(final, 2), "pnl_pct": round(pnl, 2), "exit_reason": "expired", "be_triggered": be, "bars_held": len(closes) - 1 - entry_idx}

def make_trade(i, direction, entry, stop, target, closes, highs, lows, dates, extra):
    r = sim(i, direction, entry, stop, target, closes, highs, lows)
    if r is None: return None
    rr = (target - entry) / (entry - stop) if direction == "BUY" and entry != stop else ((entry - target) / (stop - entry) if direction == "SELL" and stop != entry else 0)
    sp = abs(entry - stop) / entry * 100
    return dict(extra, **{"direction": direction, "entry_time": dates[i].strftime("%Y-%m-%d") if hasattr(dates[i], "strftime") else str(dates[i])[:10], "exit_time": dates[r["exit_idx"]].strftime("%Y-%m-%d") if hasattr(dates[r["exit_idx"]], "strftime") else str(dates[r["exit_idx"]])[:10], "entry_price": round(entry, 2), "stop_loss": round(stop, 2), "target": round(target, 2), "rr_setup": round(rr, 1), "stop_pct": round(sp, 2), **r})


# ─── Confirmation Combo Definitions ──────────────────────────────────

CONFIRMATIONS = {
    "none":               lambda i,o,h,l,c,v,sw_h,sw_l: True,
    "ema50":              lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c),
    "vol150":             lambda i,o,h,l,c,v,sw_h,sw_l: conf_volume_spike(i, v, 1.5),
    "rsi30":              lambda i,o,h,l,c,v,sw_h,sw_l: conf_rsi_extreme(i, c, 14, 30),
    "fvg":                lambda i,o,h,l,c,v,sw_h,sw_l: conf_fvg(i, h, l),
    "bos_choch":          lambda i,o,h,l,c,v,sw_h,sw_l: conf_market_structure(i, h, l, sw_h, sw_l),
    "orderblock":         lambda i,o,h,l,c,v,sw_h,sw_l: conf_order_block(i, o, h, l, c),
    "liqhunter":          lambda i,o,h,l,c,v,sw_h,sw_l: conf_liqhunter(i, o, h, l, c, v),
    "ema200":             lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema200_trend(i, c),
    # Multi-confirmation combos
    "ema50+vol150":       lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_volume_spike(i, v, 1.5),
    "ema50+fvg":          lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_fvg(i, h, l),
    "ema50+bos":          lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_market_structure(i, h, l, sw_h, sw_l),
    "ema50+liqhunter":    lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_liqhunter(i, o, h, l, c, v),
    "ema50+vol150+bos":   lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_volume_spike(i, v, 1.5) and conf_market_structure(i, h, l, sw_h, sw_l),
    "ema50+vol150+rsi":   lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_volume_spike(i, v, 1.5) and conf_rsi_extreme(i, c, 14, 30),
    "ema50+vol150+fvg":   lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_volume_spike(i, v, 1.5) and conf_fvg(i, h, l),
    "ema50+bos+ob":       lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_market_structure(i, h, l, sw_h, sw_l) and conf_order_block(i, o, h, l, c),
    "ema50+vol150+liqhunter": lambda i,o,h,l,c,v,sw_h,sw_l: conf_ema50_trend(i, c) and conf_volume_spike(i, v, 1.5) and conf_liqhunter(i, o, h, l, c, v),
}

COMBO_NAMES = list(CONFIRMATIONS.keys())


# ─── Backtest Per Stock ──────────────────────────────────────────────

def backtest_stock(symbol, combo_name):
    try:
        path = os.path.join(CACHE_DIR, f"{symbol}.pkl")
        if not os.path.exists(path): return None
        with open(path, "rb") as f: df = pickle.load(f)
        if isinstance(df.columns, pd.MultiIndex): df.columns = [c[0] for c in df.columns]
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < MIN_BARS: return None

        opens = [float(x) for x in df["Open"]]
        highs = [float(x) for x in df["High"]]
        lows = [float(x) for x in df["Low"]]
        closes = [float(x) for x in df["Close"]]
        volumes = [float(x) for x in df["Volume"]]
        dates = list(df.index)

        sh = swing_highs(highs, 3, 3)
        sl = swing_lows(lows, 3, 3)
        confirm = CONFIRMATIONS[combo_name]

        trades = []; entries = set()

        for i in range(MIN_BARS, len(closes)):
            if i in entries: continue
            sh_idx = prev_swing(i, sh)
            sl_idx = prev_swing(i, sl)
            ci = closes[i]; hi = highs[i]; li = lows[i]; oi = opens[i]

            # ── Sellside sweep → BUY ──
            if sl_idx is not None and i - sl_idx >= MIN_CANDLES:
                swing_low = lows[sl_idx]
                if li < swing_low and ci > swing_low:
                    entry = ci; stop = min(li, swing_low - (hi-li)*0.05)
                    if abs(entry-stop)/entry*100 <= MAX_STOP_PCT:
                        tgt = None
                        for j in range(i, max(i-120, -1), -1):
                            if j < len(sh) and sh[j]==1.0 and highs[j] > entry: tgt=highs[j]; break
                        if tgt is None: tgt = entry + (entry-stop)*10
                        rr = (tgt-entry)/(entry-stop) if entry != stop else 0
                        if rr >= MIN_RR and confirm(i, opens, highs, lows, closes, volumes, sh, sl):
                            t = make_trade(i, "BUY", entry, stop, tgt, closes, highs, lows, dates,
                                {"symbol": symbol.upper(), "combo": combo_name, "type": "sellside", "candles_since": i-sl_idx, "sweep_level": round(swing_low, 2)})
                            if t: trades.append(t); entries.add(i)

            # ── Buyside sweep → SELL ──
            if sh_idx is not None and i not in entries and i - sh_idx >= MIN_CANDLES:
                swing_high = highs[sh_idx]
                if hi > swing_high and ci < swing_high:
                    entry = ci; stop = max(hi, swing_high + (hi-li)*0.05)
                    if abs(entry-stop)/entry*100 <= MAX_STOP_PCT:
                        tgt = None
                        for j in range(i, max(i-120, -1), -1):
                            if j < len(sl) and sl[j]==1.0 and lows[j] < entry: tgt=lows[j]; break
                        if tgt is None: tgt = entry - (stop-entry)*10
                        rr = (entry-tgt)/(stop-entry) if stop != entry else 0
                        if rr >= MIN_RR and confirm(i, opens, highs, lows, closes, volumes, sh, sl):
                            t = make_trade(i, "SELL", entry, stop, tgt, closes, highs, lows, dates,
                                {"symbol": symbol.upper(), "combo": combo_name, "type": "buyside", "candles_since": i-sh_idx, "sweep_level": round(swing_high, 2)})
                            if t: trades.append(t); entries.add(i)

        return trades if trades else None
    except Exception:
        return None


# ─── Run All Combos ─────────────────────────────────────────────────

def run_combo(combo_name):
    all_trades = []; sym_with = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(backtest_stock, sym, combo_name): sym for sym in INDIAN_STOCKS}
        for fut in as_completed(fut_map):
            try:
                trades = fut.result(timeout=60)
                if trades:
                    all_trades.extend(trades); sym_with += 1
            except Exception:
                pass
    return all_trades, sym_with


def analyze(trades, sym_with, combo_name, elapsed):
    if not trades:
        return {"combo": combo_name, "trades": 0, "stocks": sym_with, "win_rate": 0, "avg_pnl": 0, "total_return": 0, "profit_factor": 0, "realized_rr": 0, "avg_setup_rr": 0, "targets": 0, "stops": 0, "be": 0, "avg_bars": 0}

    total = len(trades)
    winners = [t for t in trades if t["pnl_pct"] > 0]
    losers = [t for t in trades if t["pnl_pct"] <= 0]
    targets = [t for t in trades if t["exit_reason"] == "target"]
    stops = [t for t in trades if t["exit_reason"] == "stop"]
    be = [t for t in trades if t["be_triggered"]]

    wr = len(winners)/total*100
    avg_pnl = sum(t["pnl_pct"] for t in trades)/total
    avg_win = sum(t["pnl_pct"] for t in winners)/len(winners) if winners else 0
    avg_loss = sum(abs(t["pnl_pct"]) for t in losers)/len(losers) if losers else 0
    rr_r = avg_win/avg_loss if avg_loss > 0 else 0
    tot_r = sum(t["pnl_pct"] for t in trades)
    gw = sum(t["pnl_pct"] for t in winners)
    gl = abs(sum(t["pnl_pct"] for t in losers))
    pf = gw/gl if gl > 0 else 999
    avg_bars = sum(t["bars_held"] for t in trades)/total
    avg_rr_s = sum(t["rr_setup"] for t in trades)/total

    return {
        "combo": combo_name, "trades": total, "stocks": sym_with,
        "win_rate": round(wr, 1), "avg_pnl": round(avg_pnl, 4),
        "avg_win": round(avg_win, 4), "avg_loss": round(avg_loss, 4),
        "realized_rr": round(rr_r, 2), "avg_setup_rr": round(avg_rr_s, 2),
        "profit_factor": round(pf, 2), "total_return": round(tot_r, 2),
        "targets": len(targets), "stops": len(stops), "be": len(be),
        "avg_bars": round(avg_bars, 1), "elapsed_s": round(elapsed, 1)
    }


def run():
    t0 = time.time()
    print("=" * 70)
    print("LIQUIDITY SWEEP + CONFIRMATION FILTERS OPTIMIZATION")
    print("=" * 70)
    print(f"Stocks: {len(INDIAN_STOCKS)} | MinCandles: {MIN_CANDLES}")
    print(f"MaxStop: {MAX_STOP_PCT}% | MinRR: {MIN_RR}:1")
    print(f"Confirmations: {len(COMBO_NAMES)}")
    print()

    results = []
    all_trades_by_combo = {}

    for idx, cname in enumerate(COMBO_NAMES):
        t1 = time.time()
        print(f"[{idx+1}/{len(COMBO_NAMES)}] Testing: {cname} ... ", end="", flush=True)
        trades, sym_with = run_combo(cname)
        elapsed = time.time() - t1
        r = analyze(trades, sym_with, cname, elapsed)
        results.append(r)
        all_trades_by_combo[cname] = trades
        print(f"✓ {r['trades']:5d} trades, WR {r['win_rate']:5.1f}%, Total {r['total_return']:+8.2f}% ({elapsed:.0f}s)")

    total_elapsed = time.time() - t0
    print(f"\nCompleted in {total_elapsed:.0f}s\n")

    # ── Print comparison table ──
    results.sort(key=lambda x: x["win_rate"], reverse=True)

    print("=" * 70)
    print("COMPARISON — Ranked by Win Rate")
    print("=" * 70)
    print(f"{'Rank':<5} {'Combo':<25} {'Trades':>8} {'WR%':>6} {'AvgPnL%':>10} {'TotRet%':>10} {'PF':>6} {'RR':>6} {'Stks':>5}")
    print("-" * 70)
    for i, r in enumerate(results):
        if r["trades"] == 0: continue
        print(f"{i+1:<5} {r['combo']:<25} {r['trades']:>8} {r['win_rate']:>5.1f}% {r['avg_pnl']:+9.2f}% {r['total_return']:+9.2f}% {r['profit_factor']:>5.2f} {r['realized_rr']:>5.2f} {r['stocks']:>4d}")

    # ── Save report ──
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rpt = os.path.join(RPT_DIR, f"liq_sweep_optimize_{ts}.md")
    jf = os.path.join(RPT_DIR, f"liq_sweep_optimize_{ts}.json")

    lines = [
        f"# Liquidity Sweep + Confirmation Filters Optimization",
        f"",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Duration**: {total_elapsed:.0f}s",
        f"**Stocks**: {len(INDIAN_STOCKS)}",
        f"**Params**: MinCandles={MIN_CANDLES}, MaxStop={MAX_STOP_PCT}%, MinRR={MIN_RR}:1",
        f"**Combos tested**: {len(COMBO_NAMES)}",
        f"",
        f"## Results (Ranked by Win Rate)",
        f"",
        f"| Rank | Combo | Trades | Stocks | WR | Avg PnL | Total Return | PF | Realized RR | Avg Setup RR | Targets | Stops | BE% |",
        f"|------|-------|--------|--------|----|---------|-------------|----|-------------|--------------|---------|-------|------|",
    ]
    for i, r in enumerate(results):
        if r["trades"] == 0: continue
        be_pct = r["be"]/r["trades"]*100 if r["trades"] else 0
        lines.append(f"| {i+1} | {r['combo']:25s} | {r['trades']:6d} | {r['stocks']:4d} | {r['win_rate']:5.1f}% | {r['avg_pnl']:+8.2f}% | {r['total_return']:+9.2f}% | {r['profit_factor']:.2f} | {r['realized_rr']:.2f}:1 | {r['avg_setup_rr']:.2f}:1 | {r['targets']:5d} | {r['stops']:5d} | {be_pct:.1f}% |")

    # Best combo detailed breakdown
    best = results[0]
    best_trades = all_trades_by_combo.get(best["combo"], [])
    lines.extend([
        "",
        "---",
        f"## Best Combo: \"{best['combo']}\"",
        f"**Performance**: {best['trades']} trades, {best['win_rate']}% WR, {best['total_return']}% total return, PF {best['profit_factor']}, RR {best['realized_rr']}:1",
        "",
        "### Per-Type Breakdown",
        "",
        "| Type | Trades | WR | Avg PnL |",
        "|------|--------|----|---------|",
    ])
    if best_trades:
        types = {}
        for t in best_trades: types.setdefault(t["type"], []).append(t)
        for tname, tlist in sorted(types.items()):
            tw = [t for t in tlist if t["pnl_pct"] > 0]
            tp = sum(t["pnl_pct"] for t in tlist)/len(tlist)
            lines.append(f"| {tname:20s} | {len(tlist):5d} | {len(tw)/len(tlist)*100:.1f}% | {tp:+8.2f}% |")

        # Per stock
        ss = {}
        for t in best_trades: ss.setdefault(t["symbol"], []).append(t)
        lines.extend(["", "### Per-Stock Breakdown", "", "| Symbol | Trades | WR | Avg PnL | Total Return |", "|--------|--------|----|---------|-------------|"])
        for sym in sorted(ss):
            s = ss[sym]; w = [t for t in s if t["pnl_pct"] > 0]
            ap = sum(t["pnl_pct"] for t in s)/len(s)
            lines.append(f"| {sym:12s} | {len(s):4d} | {len(w)/len(s)*100:5.1f}% | {ap:+8.2f}% | {sum(t['pnl_pct'] for t in s):+9.2f}% |")

        st = sorted(best_trades, key=lambda x: x["pnl_pct"], reverse=True)
        lines.extend(["", "### Best 10 Trades", "", "| # | Symbol | Type | Dir | Entry | Exit | PnL% | RR | Days |", "|---|--------|------|-----|-------|------|------|-----|------|"])
        for k, t in enumerate(st[:10]):
            lines.append(f"| {k+1} | {t['symbol']:10s} | {t['type'][:12]:12s} | {t['direction']:4s} | {t['entry_price']:>8.2f} | {t['exit_price']:>8.2f} | {t['pnl_pct']:+6.2f}% | {t['rr_setup']:.1f}:1 | {t['bars_held']:3d} |")
        lines.extend(["", "### Worst 10 Trades", "", "| # | Symbol | Type | Dir | Entry | Exit | PnL% | RR | Days |", "|---|--------|------|-----|-------|------|------|-----|------|"])
        for k, t in enumerate(st[-10:]):
            lines.append(f"| {k+1} | {t['symbol']:10s} | {t['type'][:12]:12s} | {t['direction']:4s} | {t['entry_price']:>8.2f} | {t['exit_price']:>8.2f} | {t['pnl_pct']:+6.2f}% | {t['rr_setup']:.1f}:1 | {t['bars_held']:3d} |")

    with open(rpt, "w") as f:
        f.write("\n".join(lines))

    json_results = [r for r in results if r["trades"] > 0]
    with open(jf, "w") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "params": {"min_candles": MIN_CANDLES, "max_stop_pct": MAX_STOP_PCT, "min_rr": MIN_RR},
            "combos_tested": len(COMBO_NAMES),
            "results": json_results,
        }, f, indent=2, default=str)

    print(f"\nReport: {rpt}")
    print(f"JSON:   {jf}")
    print()

    # ── Telegram-ready summary ──
    print("=" * 50)
    print("TELEGRAM REPORT")
    print("=" * 50)
    print(f"📊 *Liq Sweep + Confirmations Optimization*")
    print(f"Combos: {len(COMBO_NAMES)} | Stocks: {len(INDIAN_STOCKS)} | Duration: {total_elapsed:.0f}s")
    print()
    for i, r in enumerate(results[:10]):
        print(f"{i+1}. *{r['combo']}* — {r['trades']} trades, WR {r['win_rate']}%, Ret {r['total_return']}%, PF {r['profit_factor']}")
    print()
    print(f"🏆 *Best*: \"{results[0]['combo']}\" — {results[0]['win_rate']}% WR, {results[0]['total_return']}% return, PF {results[0]['profit_factor']}")
    if len(results) > 1:
        print(f"Runner-up: \"{results[1]['combo']}\" — {results[1]['win_rate']}% WR, {results[1]['total_return']}% return")


if __name__ == "__main__":
    run()
