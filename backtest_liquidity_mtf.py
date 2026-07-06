"""
Multi-Timeframe Liquidity Sweep Optimization
=============================================
Key levels from higher timeframe (daily swings, prior day H/L) + 1m/1d entry
with layered confirmations. Addresses the core issue: daily sweeps lack edge
because sweep & rejection happen inside 1 candle.

Approaches tested:
  1. Prior-day session sweeps — price sweeps yesterday's H/L, enters at today's close
  2. Multi-week swing sweeps — 30+ bar lookback swing levels
  3. Both with EMA50 trend, volume, and BOS confirmations
  4. Close-entry vs next-open entry (slippage simulation)
"""
import sys, os, pickle, time, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from app.data.stocks import INDIAN_STOCKS
import pandas as pd
import numpy as np

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "ohlc_180_cache")
RPT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")
os.makedirs(RPT_DIR, exist_ok=True)

MIN_BARS = 60
MAX_STOP_PCT = 8.0
MIN_RR = 1.5


# ─── Helpers ──────────────────────────────────────────────────────────

def ema(arr, period):
    if len(arr) < period: return [arr[-1]]*len(arr)
    k = 2/(period+1); r = [arr[0]]
    for v in arr[1:]: r.append(v*k + r[-1]*(1-k))
    return r

def sma(arr, period):
    return [sum(arr[max(0,i-period+1):i+1])/min(period,i+1) for i in range(len(arr))]

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

def fair_value_gap(h, l):
    n = len(l); r = [0.0]*n
    for i in range(2, n):
        if l[i] > h[i-2]: r[i] = 1
        elif h[i] < l[i-2]: r[i] = -1
    return r

def detect_bos(i, h, l, swing_h, swing_l):
    sh_idx = [j for j in range(i) if swing_h[j]==1]
    sl_idx = [j for j in range(i) if swing_l[j]==1]
    if len(sh_idx)<2 or len(sl_idx)<2: return 0
    h1=h[sh_idx[-2]]; h2=h[sh_idx[-1]]; l1=l[sl_idx[-2]]; l2=l[sl_idx[-1]]
    if h2>h1 and l2>l1 and h[i]>h2: return 1
    if h2<h1 and l2<l1 and l[i]<l2: return -1
    return 0


# ─── Trade Simulation ──────────────────────────────────────────────────

def sim(entry_idx, direction, entry, stop, target, closes, highs, lows):
    be=False; cs=stop
    for j in range(entry_idx+1, len(closes)):
        ch,cl=highs[j],lows[j]
        if not be:
            if direction=="BUY" and ch>=entry+(entry-stop): be=True; cs=entry
            elif direction=="SELL" and cl<=entry-(stop-entry): be=True; cs=entry
        if direction=="BUY" and cl<=cs: return {"exit_idx":j,"exit_price":round(cs,2),"pnl_pct":round((cs-entry)/entry*100,2),"exit_reason":"stop","be_triggered":be,"bars_held":j-entry_idx}
        if direction=="SELL" and ch>=cs: return {"exit_idx":j,"exit_price":round(cs,2),"pnl_pct":round((entry-cs)/entry*100,2),"exit_reason":"stop","be_triggered":be,"bars_held":j-entry_idx}
        if direction=="BUY" and ch>=target: return {"exit_idx":j,"exit_price":round(target,2),"pnl_pct":round((target-entry)/entry*100,2),"exit_reason":"target","be_triggered":be,"bars_held":j-entry_idx}
        if direction=="SELL" and cl<=target: return {"exit_idx":j,"exit_price":round(target,2),"pnl_pct":round((entry-target)/entry*100,2),"exit_reason":"target","be_triggered":be,"bars_held":j-entry_idx}
    f=closes[-1]; pnl=((f-entry)/entry*100) if direction=="BUY" else ((entry-f)/entry*100)
    return {"exit_idx":len(closes)-1,"exit_price":round(f,2),"pnl_pct":round(pnl,2),"exit_reason":"expired","be_triggered":be,"bars_held":len(closes)-1-entry_idx}

def make_trade(i, direction, entry, stop, target, closes, highs, lows, dates, extra):
    r=sim(i,direction,entry,stop,target,closes,highs,lows)
    if r is None: return None
    rr=(target-entry)/(entry-stop) if direction=="BUY" and entry!=stop else ((entry-target)/(stop-entry) if direction=="SELL" and stop!=entry else 0)
    sp=abs(entry-stop)/entry*100
    return dict(extra,**{"direction":direction,"entry_time":dates[i].strftime("%Y-%m-%d") if hasattr(dates[i],"strftime") else str(dates[i])[:10],"exit_time":dates[r["exit_idx"]].strftime("%Y-%m-%d") if hasattr(dates[r["exit_idx"]],"strftime") else str(dates[r["exit_idx"]])[:10],"entry_price":round(entry,2),"stop_loss":round(stop,2),"target":round(target,2),"rr_setup":round(rr,1),"stop_pct":round(sp,2),**r})


# ─── Strategy Variants ────────────────────────────────────────────────

def backtest_v1_session_sweep(symbol):
    """Prior-day session H/L sweep. Enter at today's close."""
    return _backtest_mtf(symbol, use_session_levels=True, use_swing_levels=False, ema_filter=False, vol_filter=False, bos_filter=False)

def backtest_v2_swing_sweep(symbol):
    """Multi-week swing level sweep. Enter at close."""
    return _backtest_mtf(symbol, use_session_levels=False, use_swing_levels=True, ema_filter=False, vol_filter=False, bos_filter=False)

def backtest_v3_session_ema(symbol):
    """Session sweep + EMA50 trend filter."""
    return _backtest_mtf(symbol, use_session_levels=True, use_swing_levels=False, ema_filter=True, vol_filter=False, bos_filter=False)

def backtest_v4_swing_ema(symbol):
    """Swing sweep + EMA50 trend filter."""
    return _backtest_mtf(symbol, use_session_levels=False, use_swing_levels=True, ema_filter=True, vol_filter=False, bos_filter=False)

def backtest_v5_session_ema_vol(symbol):
    """Session sweep + EMA50 + Volume 1.5x."""
    return _backtest_mtf(symbol, use_session_levels=True, use_swing_levels=False, ema_filter=True, vol_filter=True, bos_filter=False)

def backtest_v6_swing_ema_vol(symbol):
    """Swing sweep + EMA50 + Volume 1.5x."""
    return _backtest_mtf(symbol, use_session_levels=False, use_swing_levels=True, ema_filter=True, vol_filter=True, bos_filter=False)

def backtest_v7_session_ema_vol_bos(symbol):
    """Session sweep + EMA50 + Volume + BOS — best daily combo."""
    return _backtest_mtf(symbol, use_session_levels=True, use_swing_levels=False, ema_filter=True, vol_filter=True, bos_filter=True)

def backtest_v8_swing_ema_vol_bos(symbol):
    """Swing sweep + EMA50 + Volume + BOS."""
    return _backtest_mtf(symbol, use_session_levels=False, use_swing_levels=True, ema_filter=True, vol_filter=True, bos_filter=True)

def backtest_v9_session_next(symbol):
    """Session sweep, enter NEXT open (slippage simulation)."""
    return _backtest_mtf(symbol, use_session_levels=True, use_swing_levels=False, ema_filter=False, vol_filter=False, bos_filter=False, entry_next=True)

def backtest_v10_swing_next(symbol):
    """Swing sweep, enter NEXT open (slippage)."""
    return _backtest_mtf(symbol, use_session_levels=False, use_swing_levels=True, ema_filter=False, vol_filter=False, bos_filter=False, entry_next=True)

STRATEGIES = {
    "v1_session":        backtest_v1_session_sweep,
    "v2_swing":          backtest_v2_swing_sweep,
    "v3_session_ema":    backtest_v3_session_ema,
    "v4_swing_ema":      backtest_v4_swing_ema,
    "v5_session_ema_vol": backtest_v5_session_ema_vol,
    "v6_swing_ema_vol":  backtest_v6_swing_ema_vol,
    "v7_session_ema_vol_bos": backtest_v7_session_ema_vol_bos,
    "v8_swing_ema_vol_bos":   backtest_v8_swing_ema_vol_bos,
    "v9_session_next":   backtest_v9_session_next,
    "v10_swing_next":    backtest_v10_swing_next,
}


def _backtest_mtf(symbol, use_session_levels=True, use_swing_levels=True,
                  ema_filter=False, vol_filter=False, bos_filter=False,
                  entry_next=False):
    """Core MTF backtest engine."""
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
        e50 = ema(closes, 50)
        vol_avg = sma(volumes, 20)
        fvg = fair_value_gap(highs, lows)

        trades = []; entries = set()

        for i in range(MIN_BARS, len(closes)):
            if i in entries: continue
            ci = closes[i]; hi = highs[i]; li = lows[i]; oi = opens[i]

            # ── Build levels ──
            levels = []

            # Prior-day session levels
            if use_session_levels and i >= 1:
                levels.append(("session_high", highs[i-1], "SELL"))
                levels.append(("session_low", lows[i-1], "BUY"))

            # Swing levels (30+ bar lookback for significance)
            if use_swing_levels:
                sh_idx = prev_swing(i, sh)
                sl_idx = prev_swing(i, sl)
                if sh_idx is not None and i - sh_idx >= 20:
                    levels.append(("swing_high", highs[sh_idx], "SELL"))
                if sl_idx is not None and i - sl_idx >= 20:
                    levels.append(("swing_low", lows[sl_idx], "BUY"))

            # ── Confirmations ──
            conf_ema = not ema_filter or (ci > e50[i])
            conf_vol = not vol_filter or (i >= 20 and volumes[i] >= 1.5 * vol_avg[i])
            conf_bos = not bos_filter or (detect_bos(i, highs, lows, sh, sl) != 0)
            conf_fvg = fvg[i] != 0
            conf_ok = conf_ema and conf_vol and conf_bos

            if not conf_ok: continue

            # ── Check sweeps ──
            for lname, level, expected_dir in levels:
                if i in entries: break

                if expected_dir == "BUY" and li < level and ci > level:
                    # Sellside sweep → BUY
                    entry_price = oi if entry_next else ci
                    stop = min(li, level - (hi-li)*0.1)
                    if abs(entry_price-stop)/entry_price*100 > MAX_STOP_PCT: continue

                    # Target: next swing high or 2x risk
                    tgt = None
                    for j in range(i, max(i-120, -1), -1):
                        if j < len(sh) and sh[j]==1.0 and highs[j] > entry_price: tgt=highs[j]; break
                    if tgt is None: tgt = entry_price + (entry_price-stop)*10
                    rr = (tgt-entry_price)/(entry_price-stop) if entry_price != stop else 0
                    if rr < MIN_RR: continue

                    t = make_trade(i, "BUY", entry_price, stop, tgt, closes, highs, lows, dates,
                        {"symbol": symbol.upper(), "approach": f"{lname}_buy", "entry_type": "next_open" if entry_next else "close"})
                    if t: trades.append(t); entries.add(i)

                elif expected_dir == "SELL" and hi > level and ci < level:
                    # Buyside sweep → SELL
                    entry_price = oi if entry_next else ci
                    stop = max(hi, level + (hi-li)*0.1)
                    if abs(entry_price-stop)/entry_price*100 > MAX_STOP_PCT: continue

                    tgt = None
                    for j in range(i, max(i-120, -1), -1):
                        if j < len(sl) and sl[j]==1.0 and lows[j] < entry_price: tgt=lows[j]; break
                    if tgt is None: tgt = entry_price - (stop-entry_price)*10
                    rr = (entry_price-tgt)/(stop-entry_price) if stop != entry_price else 0
                    if rr < MIN_RR: continue

                    t = make_trade(i, "SELL", entry_price, stop, tgt, closes, highs, lows, dates,
                        {"symbol": symbol.upper(), "approach": f"{lname}_sell", "entry_type": "next_open" if entry_next else "close"})
                    if t: trades.append(t); entries.add(i)

        return trades if trades else None
    except Exception:
        return None


# ─── Run ──────────────────────────────────────────────────────────────

def run_strategy(name, func):
    t0 = time.time()
    all_trades = []; sym_with = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(func, sym): sym for sym in INDIAN_STOCKS}
        for fut in as_completed(fut_map):
            try:
                trades = fut.result(timeout=60)
                if trades:
                    all_trades.extend(trades); sym_with += 1
            except Exception:
                pass
    elapsed = time.time() - t0
    return all_trades, sym_with, elapsed


def analyze(trades, sym_with, name, elapsed):
    if not trades:
        return {"approach": name, "trades": 0, "stocks": sym_with, "win_rate": 0, "avg_pnl": 0, "total_return": 0, "profit_factor": 0, "realized_rr": 0, "elapsed_s": round(elapsed, 1)}

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
    pf = sum(t["pnl_pct"] for t in winners) / abs(sum(t["pnl_pct"] for t in losers)) if losers and sum(t["pnl_pct"] for t in losers) != 0 else 999
    avg_bars = sum(t["bars_held"] for t in trades)/total
    avg_rr_s = sum(t["rr_setup"] for t in trades)/total

    return {
        "approach": name, "trades": total, "stocks": sym_with,
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
    print("MULTI-TIMEFRAME LIQUIDITY SWEEP OPTIMIZATION")
    print("=" * 70)
    print(f"Stocks: {len(INDIAN_STOCKS)}")
    print(f"MaxStop: {MAX_STOP_PCT}% | MinRR: {MIN_RR}:1")
    print(f"Strategies: {len(STRATEGIES)}")
    print()

    results = []
    for sname, sfunc in STRATEGIES.items():
        trades, sym_with, elapsed = run_strategy(sname, sfunc)
        r = analyze(trades, sym_with, sname, elapsed)
        results.append(r)
        print(f"  {sname:30s} → {r['trades']:5d} trades, WR {r['win_rate']:5.1f}%, Total {r['total_return']:+8.2f}% ({elapsed:.0f}s)")

    total_elapsed = time.time() - t0
    print(f"\nCompleted in {total_elapsed:.0f}s\n")

    results.sort(key=lambda x: x["win_rate"], reverse=True)

    print("=" * 70)
    print("COMPARISON — Ranked by Win Rate")
    print("=" * 70)
    print(f"{'Rank':<5} {'Approach':<30} {'Trades':>7} {'WR%':>6} {'Avg%':>8} {'Tot%':>8} {'PF':>6} {'RR':>6} {'Stks':>5}")
    print("-" * 70)
    for i, r in enumerate(results):
        if r["trades"] == 0: continue
        print(f"{i+1:<5} {r['approach']:<30} {r['trades']:>7} {r['win_rate']:>5.1f}% {r['avg_pnl']:+7.2f}% {r['total_return']:+7.2f}% {r['profit_factor']:>5.2f} {r['realized_rr']:>5.2f} {r['stocks']:>4d}")

    # Save report
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rpt = os.path.join(RPT_DIR, f"liq_sweep_mtf_{ts}.md")
    jf = os.path.join(RPT_DIR, f"liq_sweep_mtf_{ts}.json")

    lines = [
        f"# Multi-Timeframe Liquidity Sweep Optimization",
        f"",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Duration**: {total_elapsed:.0f}s",
        f"**Stocks**: {len(INDIAN_STOCKS)}",
        f"**Params**: MaxStop={MAX_STOP_PCT}%, MinRR={MIN_RR}:1",
        f"",
        f"## Results (Ranked by Win Rate)",
        f"",
        f"| Rank | Approach | Trades | Stocks | WR | Avg PnL | Total Return | PF | Realized RR | Targets | Stops | BE% |",
        f"|------|----------|--------|--------|----|---------|-------------|----|-------------|---------|-------|------|",
    ]
    for i, r in enumerate(results):
        if r["trades"] == 0: continue
        be_pct = r["be"]/r["trades"]*100 if r["trades"] else 0
        lines.append(f"| {i+1} | {r['approach']:30s} | {r['trades']:6d} | {r['stocks']:4d} | {r['win_rate']:5.1f}% | {r['avg_pnl']:+8.2f}% | {r['total_return']:+9.2f}% | {r['profit_factor']:.2f} | {r['realized_rr']:.2f}:1 | {r['targets']:5d} | {r['stops']:5d} | {be_pct:.1f}% |")

    lines.extend(["", "## Approach Legend", "",
        "| ID | Approach | Description |",
        "|----|----------|-------------|",
        "| v1_session | Session sweep | Prior-day H/L sweep, close entry |",
        "| v2_swing | Swing sweep | 20+ bar swing level sweep, close entry |",
        "| v3_session_ema | Session + EMA50 | + EMA50 trend filter |",
        "| v4_swing_ema | Swing + EMA50 | + EMA50 trend filter |",
        "| v5_session_ema_vol | Session + EMA50 + Vol | + Volume 1.5x spike |",
        "| v6_swing_ema_vol | Swing + EMA50 + Vol | + Volume 1.5x spike |",
        "| v7_session_ema_vol_bos | Session + EMA50 + Vol + BOS | + Market Structure BOS |",
        "| v8_swing_ema_vol_bos | Swing + EMA50 + Vol + BOS | + Market Structure BOS |",
        "| v9_session_next | Session (next open) | Entry at next day's open |",
        "| v10_swing_next | Swing (next open) | Entry at next day's open |",
    ])

    with open(rpt, "w") as f: f.write("\n".join(lines))
    with open(jf, "w") as f: json.dump({"generated": datetime.now().isoformat(), "results": results}, f, indent=2, default=str)

    print(f"\nReport: {rpt}")
    print(f"JSON:   {jf}")
    print()
    print("=" * 50)
    print("TELEGRAM REPORT")
    print("=" * 50)
    print("📊 *MTF Liquidity Sweep Optimization*")
    print(f"Strategies: {len(STRATEGIES)} | Stocks: {len(INDIAN_STOCKS)} | Duration: {total_elapsed:.0f}s")
    print()
    for i, r in enumerate(results):
        print(f"{i+1}. *{r['approach']}* — {r['trades']} trades, WR {r['win_rate']}%, Ret {r['total_return']}%, PF {r['profit_factor']}")


if __name__ == "__main__":
    run()
