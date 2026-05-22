"""
Efficient strategy optimizer — 3-phase approach:
  Phase 1: 37 indicators × 30 stocks (sampled) → rank top 5
  Phase 2: Top 5 indicators × ALL stocks (133) → confirm rankings
  Phase 3: Top 3 indicators × conf-groups × thresholds × 50 stocks → find best combo
"""
import sys, os, json, time, csv, pickle
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from app.services.strategy_builder import (
    LEADING_INDICATORS, LEADING_NAMES, CONFIRMATION_FILTERS, CONFIRMATION_NAMES,
)
from app.data.stocks import INDIAN_STOCKS
import yfinance as yf
import pandas as pd
import numpy as np

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "ohlc_180_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
DAYS = 180
MIN_BARS = 20

DEFAULT_CONFIRMATIONS = ["EMA 20", "EMA 50", "MACD", "RSI", "Volume", "Price Action"]
CONF_GROUPS = {
    "default": DEFAULT_CONFIRMATIONS,
    "all": CONFIRMATION_NAMES,
    "trend": ["EMA 20", "EMA 50", "EMA 100", "EMA 200", "SMA 20", "SMA 50", "Market Structure", "Market Trend"],
    "momentum": ["MACD", "RSI", "Stochastic", "Williams %R", "MFI", "OBV", "Divergence"],
    "volatility": ["Bollinger", "Keltner", "ATR Trail", "Donchian", "Pivot"],
    "smart": ["EMA 20", "MACD", "RSI", "Volume", "Price Action", "Bollinger", "Market Trend", "ADX"],
    "light": ["EMA 20", "MACD", "RSI", "Volume", "Price Action"],
}
THRESHOLDS = [2, 3, 4, 5]

# ── Data loading ────────────────────────────────────────────────

def load_all_data():
    data = {}
    for symbol in INDIAN_STOCKS:
        path = os.path.join(CACHE_DIR, f"{symbol}.pkl")
        if os.path.exists(path):
            with open(path, "rb") as f:
                df = pickle.load(f)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df) >= MIN_BARS:
                data[symbol] = {
                    "opens": [float(r["Open"]) for _, r in df.iterrows()],
                    "highs": [float(r["High"]) for _, r in df.iterrows()],
                    "lows": [float(r["Low"]) for _, r in df.iterrows()],
                    "closes": [float(r["Close"]) for _, r in df.iterrows()],
                    "volumes": [int(r["Volume"]) for _, r in df.iterrows()],
                }
    return data

# ── Single backtest (fast: pure Python on pre-loaded lists) ────

def bt(opens, highs, lows, closes, volumes, leading_name, conf_names, threshold, buy_only=True, sl_pct=5.0):
    leading_func = LEADING_INDICATORS.get(leading_name)
    if not leading_func:
        return None
    confs = [(n, CONFIRMATION_FILTERS[n]) for n in conf_names if n in CONFIRMATION_FILTERS]
    trades = []
    pos = False; ep = 0; esig = ""; ehigh = 0; elow = 0

    for i in range(MIN_BARS, len(closes)):
        o, h, l, c, v = opens[:i+1], highs[:i+1], lows[:i+1], closes[:i+1], volumes[:i+1]
        try:
            ld = leading_func(o, h, l, c, v)
        except Exception:
            continue
        if ld.get("direction") == "NEUTRAL":
            continue
        ld_dir = ld["direction"]

        cl, cs = 0, 0
        for _, fn in confs:
            try:
                r = fn(o, h, l, c, v)
            except Exception:
                continue
            if r.get("confirmed"):
                if r["direction"] == "LONG": cl += 1
                elif r["direction"] == "SHORT": cs += 1

        signal = "HOLD"
        price = c[-1]
        if ld_dir == "LONG" and (1 + cl) >= threshold:
            signal = "BUY"
        elif ld_dir == "SHORT" and (1 + cs) >= threshold:
            signal = "SELL"
        if buy_only and signal == "SELL":
            signal = "HOLD"

        if not pos:
            if signal == "BUY":
                pos, ep, esig, ehigh, elow = True, price, "BUY", price, price
            elif signal == "SELL":
                pos, ep, esig, ehigh, elow = True, price, "SELL", price, price
        else:
            if esig == "BUY":
                ehigh = max(ehigh, price)
            else:
                elow = min(elow, price)

            sl = False
            if sl_pct > 0:
                if esig == "BUY" and price <= ehigh * (1 - sl_pct/100):
                    sl = True
                elif esig == "SELL" and price >= elow * (1 + sl_pct/100):
                    sl = True

            exit_reason = "signal"
            exit_sig = False
            if (esig == "BUY" and signal == "SELL") or (esig == "SELL" and signal == "BUY"):
                exit_sig = True
            if sl:
                exit_sig, exit_reason = True, "stop_loss"

            if exit_sig:
                pnl = ((price - ep) / ep) * 100
                if esig == "SELL":
                    pnl = -pnl
                trades.append(round(pnl, 2))
                pos = False

    if pos:
        price = closes[-1]
        pnl = ((price - ep) / ep) * 100
        if esig == "SELL":
            pnl = -pnl
        trades.append(round(pnl, 2))

    if not trades:
        return None
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    total = len(trades); nw = len(wins); nl = len(losses)
    if total == 0:
        return None
    wr = nw / total * 100
    tr = sum(trades)
    gp = sum(wins); gl = abs(sum(losses))
    pf = gp / gl if gl > 0 else (99.9 if gp > 0 else 0)
    avg_w = gp / nw if nw else 0
    avg_l = gl / nl if nl else 0
    rr = avg_w / avg_l if avg_l > 0 else 0
    exp_val = (wr/100) * avg_w - (1 - wr/100) * avg_l if nw and nl else 0
    return {
        "trades": total, "wins": nw, "losses": nl,
        "win_rate": round(wr, 1), "total_return": round(tr, 2),
        "profit_factor": round(min(pf, 99.9), 2), "rr_ratio": round(rr, 2),
        "expectancy": round(exp_val, 2),
    }

def bt_stock(name, stock, leading_name, conf_names, threshold, buy_only=True):
    return bt(stock["opens"], stock["highs"], stock["lows"], stock["closes"], stock["volumes"],
              leading_name, conf_names, threshold, buy_only)

# ── Scoring ─────────────────────────────────────────────────────

def score(r):
    return (min(r["win_rate"], 80) * 0.3 + min(abs(r["total_return"]), 150) * 0.2 +
            min(r["profit_factor"], 10) * 2 + min(r["rr_ratio"], 5) * 2 +
            min(r["trades"], 120) * 0.05)

def _run_bt_batch(data, ind_name, conf_names, threshold, buy_only=True, workers=8):
    """Run backtest for one indicator across many stocks in parallel."""
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_map = {pool.submit(bt_stock, sym, stock, ind_name, conf_names, threshold, buy_only): sym
                   for sym, stock in data.items()}
        for fut in as_completed(fut_map):
            try:
                r = fut.result(timeout=30)
                if r:
                    results.append(r)
            except Exception:
                pass
    return results

# Skip very slow indicators (Range Filter, Speedy Range ~0.14s/call → 22s/stock)
FAST_INDICATORS = [n for n in LEADING_NAMES if n not in ("Range Filter", "Speedy Range")]

# ── Phase 1: rank all on 30 stocks ─────────────────────────────

def phase1(data):
    print(f"Phase 1: Ranking {len(FAST_INDICATORS)} indicators on 30 stocks (parallel)...")
    sample = dict(list(data.items())[:30])
    results = []
    for name in tqdm(FAST_INDICATORS, desc="P1", unit="ind"):
        rows = _run_bt_batch(sample, name, DEFAULT_CONFIRMATIONS, 3, workers=10)
        if rows:
            r = {
                "indicator": name,
                "stocks": len(rows),
                "trades": sum(x["trades"] for x in rows),
                "win_rate": round(sum(x["win_rate"] for x in rows) / len(rows), 1),
                "total_return": round(sum(x["total_return"] for x in rows), 2),
                "profit_factor": round(sum(x["profit_factor"] for x in rows) / len(rows), 2),
                "rr_ratio": round(sum(x["rr_ratio"] for x in rows) / len(rows), 2),
                "expectancy": round(sum(x["expectancy"] for x in rows) / len(rows), 2),
            }
            results.append(r)
    results.sort(key=score, reverse=True)
    print(f"  Top 5: {[r['indicator'] for r in results[:5]]}")
    return results

# ── Phase 2: top N on ALL stocks ───────────────────────────────

def phase2(data, top_inds):
    print(f"\nPhase 2: Testing {len(top_inds)} indicators on ALL {len(data)} stocks (parallel)...")
    results = []
    for name in tqdm(top_inds, desc="P2", unit="ind"):
        rows = _run_bt_batch(data, name, DEFAULT_CONFIRMATIONS, 3, workers=16)
        if rows:
            results.append({
                "indicator": name,
                "stocks": len(rows),
                "trades": sum(x["trades"] for x in rows),
                "win_rate": round(sum(x["win_rate"] for x in rows) / len(rows), 1),
                "total_return": round(sum(x["total_return"] for x in rows), 2),
                "profit_factor": round(sum(x["profit_factor"] for x in rows) / len(rows), 2),
                "rr_ratio": round(sum(x["rr_ratio"] for x in rows) / len(rows), 2),
                "expectancy": round(sum(x["expectancy"] for x in rows) / len(rows), 2),
            })
    results.sort(key=score, reverse=True)
    return results

# ── Phase 3: permutation on top 3 × conf groups × thresholds ──

def phase3(data, top_inds, sample_n=50):
    sampl = dict(list(data.items())[:sample_n])
    total = len(top_inds) * len(CONF_GROUPS) * len(THRESHOLDS)
    print(f"\nPhase 3: {len(top_inds)} inds × {len(CONF_GROUPS)} groups × {len(THRESHOLDS)} thr = {total} combos ({sample_n} stocks, parallel)...")
    results = []
    pbar = tqdm(total=total, desc="P3", unit="combo")
    for ind in top_inds:
        for gname, confs in CONF_GROUPS.items():
            for thr in THRESHOLDS:
                rows = _run_bt_batch(sampl, ind, confs, thr, workers=10)
                if rows:
                    results.append({
                        "indicator": ind, "conf_group": gname, "conf_count": len(confs),
                        "threshold": thr, "stocks": len(rows),
                        "trades": sum(x["trades"] for x in rows),
                        "win_rate": round(sum(x["win_rate"] for x in rows) / len(rows), 1),
                        "total_return": round(sum(x["total_return"] for x in rows), 2),
                        "profit_factor": round(sum(x["profit_factor"] for x in rows) / len(rows), 2),
                        "rr_ratio": round(sum(x["rr_ratio"] for x in rows) / len(rows), 2),
                        "expectancy": round(sum(x["expectancy"] for x in rows) / len(rows), 2),
                    })
                pbar.update(1)
    pbar.close()
    results.sort(key=score, reverse=True)
    return results

# ── Report ──────────────────────────────────────────────────────

def gen_report(p1, p2, p3):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    f = os.path.join(os.path.dirname(__file__), "data", f"opt_report_{ts}.md")
    lines = [f"# Strategy Optimization Report (180d, {len(INDIAN_STOCKS)} stocks)",
             f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]

    lines.append("## Phase 1: All 37 Indicators Ranked (30-stock sample)")
    lines.append("| Rank | Indicator | Stocks | Trades | WR | Return | PF | RR | Exp |")
    lines.append("|------|-----------|--------|--------|----|--------|----|----|-----|")
    for i, r in enumerate(p1, 1):
        lines.append(f"| {i} | {r['indicator']:20s} | {r['stocks']:3d} | {r['trades']:4d} | {r['win_rate']:5.1f}% | {r['total_return']:+8.2f}% | {r['profit_factor']:5.2f} | {r['rr_ratio']:5.2f} | {r['expectancy']:+6.2f} |")
    lines.append("")

    top5 = [r["indicator"] for r in p1[:5]]
    lines.append(f"**Top 5**: {', '.join(top5)}\n")

    lines.append("## Phase 2: Top 5 on ALL Stocks")
    lines.append("| Rank | Indicator | Stocks | Trades | WR | Return | PF | RR | Exp |")
    lines.append("|------|-----------|--------|--------|----|--------|----|----|-----|")
    for i, r in enumerate(p2, 1):
        lines.append(f"| {i} | {r['indicator']:20s} | {r['stocks']:3d} | {r['trades']:4d} | {r['win_rate']:5.1f}% | {r['total_return']:+8.2f}% | {r['profit_factor']:5.2f} | {r['rr_ratio']:5.2f} | {r['expectancy']:+6.2f} |")
    lines.append("")

    top3 = [r["indicator"] for r in p2[:3]]
    lines.append(f"**Final 3**: {', '.join(top3)}\n")

    lines.append("## Phase 3: Best Permutations (Top 3 × Conf Groups × Thresholds)")
    lines.append("| Rank | Indicator | Conf Group | #Confs | Thr | Stocks | Trades | WR | Return | PF | RR | Exp |")
    lines.append("|------|-----------|------------|--------|-----|--------|--------|----|--------|----|----|-----|")
    for i, r in enumerate(p3[:30], 1):
        lines.append(f"| {i} | {r['indicator']:16s} | {r['conf_group']:10s} | {r['conf_count']:3d} | {r['threshold']} | {r['stocks']:3d} | {r['trades']:4d} | {r['win_rate']:5.1f}% | {r['total_return']:+8.2f}% | {r['profit_factor']:5.2f} | {r['rr_ratio']:5.2f} | {r['expectancy']:+6.2f} |")
    lines.append("")

    # Best RR combos
    high_rr = [r for r in p3 if r["rr_ratio"] >= 2.0 and r["stocks"] >= 5][:10]
    lines.append("## Best Risk-Reward Combos (RR ≥ 2.0, ≥5 stocks)")
    if high_rr:
        lines.append("| Rank | Combo | Stocks | Trades | WR | Return | PF | RR |")
        lines.append("|------|-------|--------|--------|----|--------|----|----|")
        for i, r in enumerate(high_rr, 1):
            combo = f"{r['indicator']}+{r['conf_group']}(thr={r['threshold']})"
            lines.append(f"| {i} | {combo:40s} | {r['stocks']:3d} | {r['trades']:4d} | {r['win_rate']:5.1f}% | {r['total_return']:+8.2f}% | {r['profit_factor']:5.2f} | {r['rr_ratio']:5.2f} |")
    else:
        lines.append("None found with RR ≥ 2.0.")
    lines.append("")

    # Recommendations
    lines.append("## Top 3 Recommended Strategies")
    for i, r in enumerate(p3[:3], 1):
        lines.append(f"### {i}. {r['indicator']} + {r['conf_group']} (threshold={r['threshold']})")
        lines.append(f"- Win Rate: {r['win_rate']}% on {r['stocks']} stocks, {r['trades']} total trades")
        lines.append(f"- Total Return: {r['total_return']:+.2f}% | Profit Factor: {r['profit_factor']}")
        lines.append(f"- Risk-Reward: {r['rr_ratio']} | Expectancy: {r['expectancy']:+.2f}%/trade")
        lines.append("")

    lines.append("## One-Day Signal Recommendation")
    lines.append("For daily BUY signals, the strategy should use:")
    lines.append(f"- **Leading Indicator**: {p3[0]['indicator'] if p3 else 'N/A'}")
    lines.append(f"- **Confirmations**: {p3[0]['conf_group'] if p3 else 'N/A'} ({p3[0]['conf_count'] if p3 else 0} filters)")
    lines.append(f"- **Threshold**: {p3[0]['threshold'] if p3 else 'N/A'}")
    if p3:
        lines.append(f"- Expected Win Rate: {p3[0]['win_rate']}%")
        lines.append(f"- Risk-Reward Ratio: {p3[0]['rr_ratio']}:1")

    with open(f, "w") as fp:
        fp.write("\n".join(lines))
    print(f"\nReport: {f}")
    return f

# ── Main ────────────────────────────────────────────────────────

if __name__ == "__main__":
    t0 = time.time()
    data = load_all_data()
    print(f"Loaded {len(data)} stocks\n")

    p1 = phase1(data)
    p1_top5 = [r["indicator"] for r in p1[:5]]

    p2 = phase2(data, p1_top5)
    p2_top3 = [r["indicator"] for r in p2[:3]]

    p3 = phase3(data, p2_top3, sample_n=50)

    gen_report(p1, p2, p3)

    # Save the report data as JSON too
    json_path = os.path.join(os.path.dirname(__file__), "data", f"opt_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(json_path, "w") as fp:
        json.dump({"phase1": p1, "phase2": p2, "phase3": p3}, fp, indent=2)
    print(f"JSON: {json_path}")
    print(f"\nTotal: {time.time()-t0:.0f}s")
