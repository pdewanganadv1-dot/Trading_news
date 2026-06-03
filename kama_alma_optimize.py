"""
KAMA + ALMA optimization: test KAMA+light+thr=2 and ALMA+light+thr=2
with SL/TP grid search on 180d daily data (cached).
"""
import sys, os, json, time, pickle, csv
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.services.strategy_builder import (
    LEADING_INDICATORS, CONFIRMATION_FILTERS, CONFIRMATION_NAMES,
)
from app.data.stocks import INDIAN_STOCKS
import yfinance as yf
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "ohlc_180_cache")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "data")
MIN_BARS = 20

LIGHT_CONFS = ["EMA 20", "MACD", "RSI", "Volume", "Price Action"]

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

def bt_full(opens, highs, lows, closes, volumes, leading_name, conf_names, threshold,
            sl_pct=5.0, tp_pct=0.0, trailing_sl=True, buy_only=True):
    """Full backtest with trailing SL and TP support."""
    leading_func = LEADING_INDICATORS.get(leading_name)
    if not leading_func:
        return None
    confs = [(n, CONFIRMATION_FILTERS[n]) for n in conf_names if n in CONFIRMATION_FILTERS]
    trades = []
    pos = False; ep = 0; esig = ""; ehigh = 0; elow = 0; entry_idx = 0

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
                pos, ep, esig, ehigh, elow, entry_idx = True, price, "BUY", price, price, i
            elif signal == "SELL":
                pos, ep, esig, ehigh, elow, entry_idx = True, price, "SELL", price, price, i
        else:
            if esig == "BUY":
                ehigh = max(ehigh, price)
                if trailing_sl:
                    elow = max(elow, ehigh * (1 - sl_pct/100))
            else:
                elow = min(elow, price)
                if trailing_sl:
                    ehigh = min(ehigh, elow * (1 + sl_pct/100))

            exit_reason = None
            if sl_pct > 0:
                if esig == "BUY" and price <= (ehigh * (1 - sl_pct/100) if not trailing_sl else elow):
                    exit_reason = "stop_loss"
                elif esig == "SELL" and price >= (elow * (1 + sl_pct/100) if not trailing_sl else ehigh):
                    exit_reason = "stop_loss"

            if tp_pct > 0 and not exit_reason:
                if esig == "BUY" and price >= ep * (1 + tp_pct/100):
                    exit_reason = "take_profit"
                elif esig == "SELL" and price <= ep * (1 - tp_pct/100):
                    exit_reason = "take_profit"

            if not exit_reason:
                if (esig == "BUY" and signal == "SELL") or (esig == "SELL" and signal == "BUY"):
                    exit_reason = "signal"

            if exit_reason:
                pnl = ((price - ep) / ep) * 100
                if esig == "SELL":
                    pnl = -pnl
                trades.append({"pnl": round(pnl, 2), "bars": i - entry_idx,
                               "exit": exit_reason, "entry_price": round(ep, 2),
                               "exit_price": round(price, 2)})
                pos = False

    if pos:
        price = closes[-1]
        pnl = ((price - ep) / ep) * 100
        if esig == "SELL":
            pnl = -pnl
        trades.append({"pnl": round(pnl, 2), "bars": len(closes) - 1 - entry_idx,
                       "exit": "end", "entry_price": round(ep, 2),
                       "exit_price": round(price, 2)})

    if not trades:
        return None
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = len(trades); nw = len(wins); nl = len(losses)
    wr = nw / total * 100
    tr = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = gp / gl if gl > 0 else (99.9 if gp > 0 else 0)
    avg_w = gp / nw if nw else 0
    avg_l = gl / nl if nl else 0
    rr = avg_w / avg_l if avg_l > 0 else 0
    exp_val = (wr/100) * avg_w - (1 - wr/100) * avg_l if nw and nl else 0
    return {
        "trades": total, "wins": nw, "losses": nl,
        "win_rate": round(wr, 1), "total_return": round(tr, 2),
        "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
        "profit_factor": round(min(pf, 99.9), 2), "rr_ratio": round(rr, 2),
        "expectancy": round(exp_val, 2),
    }

def run_indicator(data, leading_name, conf_names, threshold, sl_pct, tp_pct, trailing_sl, workers=16):
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_map = {}
        for sym, stock in data.items():
            fut = pool.submit(bt_full, stock["opens"], stock["highs"], stock["lows"],
                              stock["closes"], stock["volumes"],
                              leading_name, conf_names, threshold,
                              sl_pct, tp_pct, trailing_sl)
            fut_map[fut] = sym
        for fut in as_completed(fut_map):
            try:
                r = fut.result(timeout=30)
                if r:
                    results.append(r)
            except Exception:
                pass
    return results

def aggregate(results):
    if not results:
        return None
    total_trades = sum(r["trades"] for r in results)
    total_returns = sum(r["total_return"] for r in results)
    total_wins = sum(r["wins"] for r in results)
    total_losses = sum(r["losses"] for r in results)
    total_gp = sum(r["avg_win"] * r["wins"] for r in results if r["wins"])
    total_gl = sum(r["avg_loss"] * r["losses"] for r in results if r["losses"])
    avg_wr = sum(r["win_rate"] for r in results) / len(results)
    avg_pf = total_gp / total_gl if total_gl > 0 else 0
    avg_win = total_gp / total_wins if total_wins else 0
    avg_loss = total_gl / total_losses if total_losses else 0
    rr = avg_win / avg_loss if avg_loss > 0 else 0
    return {
        "stocks": len(results), "total_trades": total_trades,
        "total_wins": total_wins, "total_losses": total_losses,
        "avg_win_rate": round(avg_wr, 1), "total_return": round(total_returns, 2),
        "avg_pf": round(avg_pf, 2), "avg_rr": round(rr, 2),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
    }

def gen_report(all_results, leading_name, ts):
    lines = []
    lines.append(f"# {leading_name} + Light + thr=2 — SL/TP Grid Optimization")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Data: 180d daily, {len(INDIAN_STOCKS)} stocks cached")
    lines.append(f"Confirmations: {', '.join(LIGHT_CONFS)}")
    lines.append("")

    # Sort by composite score (WR*0.3 + RR*0.3 + PF*0.2 + Return*0.2)
    def score(r):
        return (r["avg_win_rate"] * 0.3 + r["avg_rr"] * 20 * 0.3 +
                r["avg_pf"] * 0.2 + r["total_return"] * 0.002 * 0.2)

    sorted_results = sorted(all_results, key=score, reverse=True)

    lines.append(f"## All {len(all_results)} SL/TP Combinations")
    lines.append("| Rank | SL% | TP% | Trailing | Stocks | Trades | WR | Return | PF | RR | AvgWin | AvgLoss |")
    lines.append("|------|-----|-----|----------|--------|--------|----|--------|----|----|--------|---------|")
    for rank, r in enumerate(sorted_results, 1):
        trail = "Y" if r["trailing"] else "N"
        tp = r["tp"] if r["tp"] > 0 else "∞"
        lines.append(f"| {rank:2d} | {r['sl']:3.0f}% | {str(tp):>4s} | {trail:>8s} | {r['stocks']:3d} | {r['total_trades']:4d} | {r['avg_win_rate']:5.1f}% | {r['total_return']:+8.2f}% | {r['avg_pf']:5.2f} | {r['avg_rr']:5.2f} | {r['avg_win']:6.2f}% | {r['avg_loss']:6.2f}% |")
    lines.append("")

    lines.append("## Top 10 Combinations")
    lines.append("| Rank | SL% | TP% | Trailing | WR | Return | PF | RR | Trades |")
    lines.append("|------|-----|-----|----------|----|--------|----|----|--------|")
    for rank, r in enumerate(sorted_results[:10], 1):
        trail = "Y" if r["trailing"] else "N"
        tp = r["tp"] if r["tp"] > 0 else "∞"
        lines.append(f"| {rank:2d} | {r['sl']:3.0f}% | {str(tp):>4s} | {trail:>8s} | {r['avg_win_rate']:5.1f}% | {r['total_return']:+8.2f}% | {r['avg_pf']:5.2f} | {r['avg_rr']:5.2f} | {r['total_trades']:4d} |")
    lines.append("")

    # Best per target
    lines.append("## Best by Target Metric")
    best_wr = max(sorted_results, key=lambda r: r["avg_win_rate"])
    best_return = max(sorted_results, key=lambda r: r["total_return"])
    best_rr = max(sorted_results, key=lambda r: r["avg_rr"])
    best_pf = max(sorted_results, key=lambda r: r["avg_pf"])

    lines.append(f"- **Highest WR**: SL={best_wr['sl']:.0f}% TP={best_wr['tp'] if best_wr['tp']>0 else '∞'}% Trailing={'Y' if best_wr['trailing'] else 'N'} → WR {best_wr['avg_win_rate']}%")
    lines.append(f"- **Highest Return**: SL={best_return['sl']:.0f}% TP={best_return['tp'] if best_return['tp']>0 else '∞'}% Trailing={'Y' if best_return['trailing'] else 'N'} → Return {best_return['total_return']:+.2f}%")
    lines.append(f"- **Highest RR**: SL={best_rr['sl']:.0f}% TP={best_rr['tp'] if best_rr['tp']>0 else '∞'}% Trailing={'Y' if best_rr['trailing'] else 'N'} → RR {best_rr['avg_rr']:.2f}")
    lines.append(f"- **Highest PF**: SL={best_pf['sl']:.0f}% TP={best_pf['tp'] if best_pf['tp']>0 else '∞'}% Trailing={'Y' if best_pf['trailing'] else 'N'} → PF {best_pf['avg_pf']:.2f}")
    lines.append("")

    lines.append("## Recommended Configurations")
    lines.append("### For aggressive (max return):")
    r1 = sorted_results[0]
    lines.append(f"- SL={r1['sl']:.0f}% TP={r1['tp'] if r1['tp']>0 else '∞'}% Trailing={'Y' if r1['trailing'] else 'N'} — {r1['avg_win_rate']}% WR, {r1['total_return']:+.2f}% return, RR {r1['avg_rr']}")
    lines.append("")
    lines.append("### For conservative (max WR):")
    bw = sorted_results[0]
    lines.append(f"- SL={bw['sl']:.0f}% TP={bw['tp'] if bw['tp']>0 else '∞'}% Trailing={'Y' if bw['trailing'] else 'N'} — {bw['avg_win_rate']}% WR, {bw['total_return']:+.2f}% return, RR {bw['avg_rr']}")
    lines.append("")
    lines.append("### For best risk-reward:")
    br = sorted_results[0]
    lines.append(f"- SL={br['sl']:.0f}% TP={br['tp'] if br['tp']>0 else '∞'}% Trailing={'Y' if br['trailing'] else 'N'} — {br['avg_win_rate']}% WR, RR {br['avg_rr']}")
    lines.append("")

    # Per-stock breakdown for best combo
    lines.append("## Per-Stock Breakdown (Best Combo)")
    best = sorted_results[0]
    lines.append(f"(SL={best['sl']:.0f}%, TP={best['tp'] if best['tp']>0 else '∞'}%, Trailing={'Y' if best['trailing'] else 'N'})")
    lines.append("| Stock | Trades | WR | Return |")
    lines.append("|-------|--------|----|--------|")
    # Re-run best combo for per-stock detail
    data = load_all_data()
    per_stock = []
    for sym, stock in data.items():
        r = bt_full(stock["opens"], stock["highs"], stock["lows"], stock["closes"],
                    stock["volumes"], leading_name, LIGHT_CONFS, 2,
                    best["sl"], best["tp"] if best["tp"] > 0 else 0.0, best["trailing"])
        if r and r["trades"] > 0:
            per_stock.append((sym, r))
    per_stock.sort(key=lambda x: x[1]["total_return"], reverse=True)
    for sym, r in per_stock:
        lines.append(f"| {sym:15s} | {r['trades']:3d} | {r['win_rate']:5.1f}% | {r['total_return']:+8.2f}% |")

    fname = os.path.join(REPORT_DIR, f"{leading_name.lower()}_optimize_{ts}.md")
    with open(fname, "w") as fp:
        fp.write("\n".join(lines))
    print(f"\nReport saved: {fname}")
    return fname


if __name__ == "__main__":
    t0 = time.time()
    print(f"Loading cached 180d data...")
    data = load_all_data()
    print(f"Loaded {len(data)} stocks")

    # SL/TP grid
    sl_values = [2, 3, 5, 7]
    tp_values = [0, 4, 6, 8, 10, 15]
    trailing_values = [True, False]

    for leading_name in ["KAMA", "ALMA"]:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"\n{'='*60}")
        print(f"Testing {leading_name} + Light + thr=2")
        print(f"{'='*60}")

        all_results = []
        total_combos = len(sl_values) * len(tp_values) * len(trailing_values)
        combo = 0

        for sl in sl_values:
            for tp in tp_values:
                for trail in trailing_values:
                    combo += 1
                    print(f"  [{combo}/{total_combos}] SL={sl}% TP={tp if tp>0 else '∞'}% Trail={'Y' if trail else 'N'}...", end=" ")
                    r = run_indicator(data, leading_name, LIGHT_CONFS, 2, sl, tp, trail)
                    agg = aggregate(r)
                    if agg:
                        agg["sl"] = sl
                        agg["tp"] = tp
                        agg["trailing"] = trail
                        all_results.append(agg)
                        print(f"→ {agg['avg_win_rate']}% WR, {agg['total_return']:+.2f}% ret, RR {agg['avg_rr']}")
                    else:
                        print("→ no trades")

        if all_results:
            fpath = gen_report(all_results, leading_name, ts)
            print(f"\n{leading_name} optimization complete → {fpath}")
        else:
            print(f"\n{leading_name}: No results generated!")
        print(f"  Time: {time.time()-t0:.0f}s")

    print(f"\nTotal time: {time.time()-t0:.0f}s")
