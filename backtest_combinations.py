"""
Comprehensive backtest: All 36 indicators x individual confirmations x dynamic SL strategies
Tests on daily (30d) and 1-minute (8d) timeframes with multiple exit strategies.
"""
import sys, os, json, time, csv, pickle
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from app.services.strategy_builder import (
    LEADING_INDICATORS, LEADING_NAMES, CONFIRMATION_FILTERS, CONFIRMATION_NAMES
)
import pandas as pd

REPORT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")
DAILY_CACHE = os.path.join(os.path.dirname(__file__), "data", "ohlc_cache")
CACHE_1M = os.path.join(os.path.dirname(__file__), "data", "ohlc_cache_1m")
os.makedirs(REPORT_DIR, exist_ok=True)

MIN_BARS = 20
THRESHOLDS = [2, 3, 4]
DEFAULT_CONFIRMATIONS = ["EMA 20", "EMA 50", "MACD", "RSI", "Volume", "Price Action"]


# ─── Dynamic Exit Strategies ─────────────────────────────────────────

def simulate_exit(closes, entry_idx, entry_price, direction, strategy):
    if direction == "BUY":
        return _simulate_long(closes, entry_idx, entry_price, strategy)
    else:
        return _simulate_short(closes, entry_idx, entry_price, strategy)


def _simulate_long(closes, entry_idx, entry_price, strategy):
    sl_type = strategy["sl_type"]
    rr_risk = strategy.get("risk_pct", 1.0)
    rr_reward = strategy.get("reward_pct", 2.0)
    max_hold = 20
    end = min(entry_idx + max_hold, len(closes))

    atr = _calc_atr(closes, entry_idx) if "atr" in sl_type else None

    if sl_type == "fixed":
        sl = entry_price * (1 - rr_risk / 100)
        tp = entry_price * (1 + rr_reward / 100)
    elif sl_type == "atr":
        sl = entry_price - atr * 1.5 if atr else entry_price * 0.99
        tp = entry_price + atr * 3.0 if atr else entry_price * 1.03
    elif sl_type == "atr_trail":
        sl = entry_price - atr * 1.5 if atr else entry_price * 0.99
        tp = entry_price + atr * 3.0 if atr else entry_price * 1.03
    elif sl_type == "breakeven":
        sl = entry_price * (1 - rr_risk / 100)
        tp = entry_price * (1 + rr_reward / 100)
        be_trigger = entry_price + (tp - entry_price) * 0.5
    elif sl_type == "step_trail":
        sl = entry_price * (1 - rr_risk / 100)
        tp = entry_price * (1 + rr_reward / 100)
        steps = [(0.25, entry_price * (1 - rr_risk / 200)),
                 (0.50, entry_price),
                 (0.75, entry_price * (1 + rr_reward / 300))]
    elif sl_type == "scale_out":
        sl = entry_price - atr * 1.5 if atr else entry_price * 0.99
        tp = entry_price + atr * 3.0 if atr else entry_price * 1.03
        tp1 = entry_price + atr * 1.5 if atr else entry_price * 1.01
        half_closed = False
    else:
        return ("NONE", entry_price, 0, 0.0)

    for i in range(entry_idx + 1, end):
        price = closes[i]

        if sl_type == "atr_trail" and atr:
            sl = max(sl, price - atr)
        elif sl_type == "breakeven" and price >= be_trigger:
            sl = max(sl, entry_price)

        if sl_type == "step_trail":
            progress = (price - entry_price) / (tp - entry_price) if tp != entry_price else 0
            for pct, new_sl in steps:
                if progress >= pct:
                    sl = max(sl, new_sl)

        if sl_type == "scale_out":
            if not half_closed and price >= tp1:
                half_closed = True
            if half_closed and atr:
                sl = max(sl, price - atr * 0.8)

        if price >= tp:
            return ("TP", price, i - entry_idx, (price - entry_price) / entry_price * 100)

    final_price = closes[end - 1]
    pnl = (final_price - entry_price) / entry_price * 100
    return ("TIMEOUT", final_price, end - entry_idx, pnl)


def _simulate_short(closes, entry_idx, entry_price, strategy):
    sl_type = strategy["sl_type"]
    rr_risk = strategy.get("risk_pct", 1.0)
    rr_reward = strategy.get("reward_pct", 2.0)
    max_hold = 20
    end = min(entry_idx + max_hold, len(closes))
    atr = _calc_atr(closes, entry_idx) if "atr" in sl_type else None

    if sl_type == "fixed":
        sl = entry_price * (1 + rr_risk / 100)
        tp = entry_price * (1 - rr_reward / 100)
    elif sl_type == "atr":
        sl = entry_price + atr * 1.5 if atr else entry_price * 1.01
        tp = entry_price - atr * 3.0 if atr else entry_price * 0.97
    elif sl_type == "atr_trail":
        sl = entry_price + atr * 1.5 if atr else entry_price * 1.01
        tp = entry_price - atr * 3.0 if atr else entry_price * 0.97
    elif sl_type == "breakeven":
        sl = entry_price * (1 + rr_risk / 100)
        tp = entry_price * (1 - rr_reward / 100)
        be_trigger = entry_price - (entry_price - tp) * 0.5
    elif sl_type == "step_trail":
        sl = entry_price * (1 + rr_risk / 100)
        tp = entry_price * (1 - rr_reward / 100)
        steps = [(0.25, entry_price * (1 + rr_risk / 200)),
                 (0.50, entry_price),
                 (0.75, entry_price * (1 - rr_reward / 300))]
    elif sl_type == "scale_out":
        sl = entry_price + atr * 1.5 if atr else entry_price * 1.01
        tp = entry_price - atr * 3.0 if atr else entry_price * 0.97
        tp1 = entry_price - atr * 1.5 if atr else entry_price * 0.99
        half_closed = False
    else:
        return ("NONE", entry_price, 0, 0.0)

    for i in range(entry_idx + 1, end):
        price = closes[i]

        if sl_type == "atr_trail" and atr:
            sl = min(sl, price + atr)
        elif sl_type == "breakeven" and price <= be_trigger:
            sl = min(sl, entry_price)

        if sl_type == "step_trail":
            progress = (entry_price - price) / (entry_price - tp) if entry_price != tp else 0
            for pct, new_sl in steps:
                if progress >= pct:
                    sl = min(sl, new_sl)

        if sl_type == "scale_out":
            if not half_closed and price <= tp1:
                half_closed = True
            if half_closed and atr:
                sl = min(sl, price + atr * 0.8)

        if price >= sl:
            return ("SL", price, i - entry_idx, (entry_price - price) / entry_price * 100)
        if price <= tp:
            return ("TP", price, i - entry_idx, (entry_price - price) / entry_price * 100)

    final_price = closes[end - 1]
    pnl = (entry_price - final_price) / entry_price * 100
    return ("TIMEOUT", final_price, end - entry_idx, pnl)


def _calc_atr(closes, idx, period=14):
    if idx < period + 1:
        return None
    ranges = [abs(closes[i] - closes[i - 1]) for i in range(idx - period, idx + 1) if i > 0]
    return sum(ranges) / len(ranges) if ranges else None


EXIT_STRATEGIES = [
    {"name": "Fixed 1:2",   "sl_type": "fixed", "risk_pct": 1.0, "reward_pct": 2.0},
    {"name": "Fixed 1:3",   "sl_type": "fixed", "risk_pct": 1.0, "reward_pct": 3.0},
    {"name": "Fixed 2:3",   "sl_type": "fixed", "risk_pct": 2.0, "reward_pct": 3.0},
    {"name": "ATR 1.5:3",   "sl_type": "atr"},
    {"name": "ATR Trail",   "sl_type": "atr_trail"},
    {"name": "Breakeven",   "sl_type": "breakeven", "risk_pct": 1.0, "reward_pct": 2.0},
    {"name": "Step Trail",  "sl_type": "step_trail", "risk_pct": 1.0, "reward_pct": 2.0},
    {"name": "Scale Out",   "sl_type": "scale_out"},
]


# ─── Backtest Engine ──────────────────────────────────────────────────

def load_data(cache_dir):
    data = {}
    files = sorted(os.listdir(cache_dir))
    for f in files:
        if not f.endswith(".pkl"):
            continue
        sym = f.replace(".pkl", "").upper()
        try:
            df = pickle.load(open(os.path.join(cache_dir, f), "rb"))
            if len(df) >= MIN_BARS:
                data[sym] = df
        except Exception:
            pass
    return data


def run_one_combo(args):
    """Run one indicator+confirmation+threshold combo. Args tuple for parallel."""
    indicator_name, conf_names, threshold, data_dict = args
    indicator_func = LEADING_INDICATORS[indicator_name]
    confirm_funcs = {n: CONFIRMATION_FILTERS[n] for n in conf_names if n in CONFIRMATION_FILTERS}
    conf_label = "+".join(conf_names) if conf_names else "NONE"

    results = []
    for sym, df in data_dict.items():
        try:
            o = [float(x) for x in df['Open']]
            h = [float(x) for x in df['High']]
            l = [float(x) for x in df['Low']]
            c = [float(x) for x in df['Close']]
            v = [int(x) for x in df['Volume']]
            dates = list(df.index)
        except Exception:
            continue

        for i in range(MIN_BARS, len(c)):
            o_s, h_s, l_s, c_s, v_s = o[:i+1], h[:i+1], l[:i+1], c[:i+1], v[:i+1]

            try:
                ld = indicator_func(o_s, h_s, l_s, c_s, v_s)
            except Exception:
                continue
            ld_dir = ld.get("direction", "NEUTRAL")
            if ld_dir == "NEUTRAL":
                continue

            conf_long = conf_short = 0
            for name in conf_names:
                func = confirm_funcs.get(name)
                if not func:
                    continue
                try:
                    result = func(o_s, h_s, l_s, c_s, v_s)
                except Exception:
                    continue
                if not result.get("confirmed", False):
                    continue
                if result["direction"] == "LONG":
                    conf_long += 1
                elif result["direction"] == "SHORT":
                    conf_short += 1

            signal = None
            if ld_dir == "LONG" and conf_long >= threshold:
                signal = "BUY"
            elif ld_dir == "SHORT" and conf_short >= threshold:
                signal = "SELL"

            if not signal:
                continue

            entry_price = c[-1]
            dt = dates[i]
            ts = dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, 'strftime') else str(dt)[:16]

            for strategy in EXIT_STRATEGIES:
                exit_type, exit_price, bars_held, pnl = simulate_exit(c, i, entry_price, signal, strategy)
                results.append({
                    "symbol": sym,
                    "indicator": indicator_name,
                    "confirmations": conf_label,
                    "threshold": threshold,
                    "signal": signal,
                    "entry_price": round(entry_price, 2),
                    "exit_strategy": strategy["name"],
                    "exit_type": exit_type,
                    "exit_price": round(exit_price, 2),
                    "bars_held": bars_held,
                    "pnl_pct": round(pnl, 2),
                    "is_win": 1 if pnl > 0 else 0,
                    "timestamp": ts,
                })
    return results


def build_report(all_signals, timeframe_label, total_time):
    if not all_signals:
        print(f"  No signals for {timeframe_label}", flush=True)
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = os.path.join(REPORT_DIR, f"combo_{timeframe_label}_{timestamp}.md")
    csv_file = os.path.join(REPORT_DIR, f"combo_{timeframe_label}_{timestamp}.csv")

    rows = [s for batch in all_signals for s in batch]
    if not rows:
        print(f"  No signals for {timeframe_label}", flush=True)
        return

    with open(csv_file, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    df = pd.DataFrame(rows)
    md_lines = [f"# Comprehensive Backtest — {timeframe_label}",
                f"", f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"**Signals**: {len(df)}", f"**Duration**: {total_time:.0f}s", ""]

    grouped = df.groupby(["indicator", "exit_strategy"])
    summary_rows = []
    md_lines.append("## Full Results")
    md_lines.append("| Indicator | Exit Strategy | Signals | Wins | Losses | Win Rate | Avg P&L |")
    md_lines.append("|-----------|---------------|---------|------|--------|----------|---------|")
    for (ind, exit_s), g in grouped:
        total = len(g)
        wins = int(g["is_win"].sum())
        loss = total - wins
        wr = wins / total * 100 if total > 0 else 0
        avg_pnl = g["pnl_pct"].mean()
        md_lines.append(f"| {ind} | {exit_s} | {total} | {wins} | {loss} | {wr:.1f}% | {avg_pnl:+.2f}% |")
        summary_rows.append({"indicator": ind, "exit": exit_s, "total": total, "wins": wins,
                             "losses": loss, "win_rate": round(wr, 1), "avg_pnl": round(avg_pnl, 2)})

    md_lines.append("")
    md_lines.append("## Top 15 by Win Rate (min 10 signals)")
    md_lines.append("")
    top = [r for r in summary_rows if r["total"] >= 10]
    top.sort(key=lambda x: x["win_rate"], reverse=True)
    for i, r in enumerate(top[:15], 1):
        md_lines.append(f"{i}. **{r['indicator']}** + {r['exit']} — {r['win_rate']}% ({r['wins']}/{r['total']}), Avg: {r['avg_pnl']:+.2f}%")

    # Best confirmation per indicator per exit strategy
    if "confirmations" in df.columns:
        md_lines.append("")
        md_lines.append("## Best Confirmation per Indicator")
        md_lines.append("")
        for ind, g in df.groupby("indicator"):
            best = g.groupby("confirmations")["is_win"].mean().sort_values(ascending=False)
            if len(best) > 0:
                best_name = best.index[0]
                best_wr = best.iloc[0] * 100
                md_lines.append(f"- **{ind}**: Best conf = `{best_name}` ({best_wr:.0f}% WR)")

    with open(report_file, "w") as f:
        f.write("\n".join(md_lines))
    print(f"  Report: {report_file}", flush=True)
    print(f"  CSV: {csv_file}", flush=True)
    print(f"  Signals: {len(rows)}", flush=True)
    for r in top[:5]:
        print(f"  🏆 {r['indicator']} + {r['exit']}: {r['win_rate']}% ({r['wins']}/{r['total']})", flush=True)


def main():
    start = time.time()
    print("=" * 60, flush=True)
    print("Loading cached data...", flush=True)
    daily_data = load_data(DAILY_CACHE)
    data_1m = load_data(CACHE_1M)
    print(f"  Daily: {len(daily_data)} stocks", flush=True)
    print(f"  1-min: {len(data_1m)} stocks", flush=True)

    # Build combo list
    confirm_sets = [("NONE", [])] + [(n, [n]) for n in CONFIRMATION_NAMES] + \
                   [("ALL", CONFIRMATION_NAMES), ("DEFAULT6", DEFAULT_CONFIRMATIONS)]

    combos = []
    for ind_name in LEADING_NAMES:
        for conf_name, conf_list in confirm_sets:
            for th in THRESHOLDS:
                if not conf_list and th > 2:
                    continue
                if len(conf_list) == 1 and th > 1:
                    continue
                combos.append((ind_name, conf_list, th))
    print(f"Combos: {len(combos)} (36 indicators x 26 conf sets x thresholds)", flush=True)
    print()

    # ── DAILY ──
    print("=" * 60, flush=True)
    print("DAILY BACKTEST — running...", flush=True)
    print("=" * 60, flush=True)
    daily_signals = []
    n = len(combos)
    for idx, combo in enumerate(combos):
        r = run_one_combo((combo[0], combo[1], combo[2], daily_data))
        daily_signals.append(r)
        if (idx + 1) % 50 == 0 or idx == n - 1:
            elapsed = time.time() - start
            sigs = sum(len(s) for s in daily_signals if s)
            print(f"  [{idx+1}/{n}] {elapsed:.0f}s — {sigs} signals so far", flush=True)

    daily_time = time.time() - start
    print(f"\nDaily done in {daily_time:.0f}s", flush=True)
    build_report(daily_signals, "DAILY", daily_time)

    # ── 1-MINUTE (optimized: pre-compute series, decimate to 260 bars) ──
    print(f"\n{'=' * 60}", flush=True)
    print("1-MIN BACKTEST — optimized (10 indicators x 20 stocks)", flush=True)
    print("=" * 60, flush=True)

    # Pick liquid stocks with 1m data
    top_stocks = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
                  "SBIN", "BHARTIARTL", "ITC", "LT", "WIPRO",
                  "AXISBANK", "KOTAKBANK", "MARUTI", "TITAN", "TATAMOTORS",
                  "ADANIENT", "NTPC", "POWERGRID", "HCLTECH", "BAJFINANCE"]
    # Decimate 1m data: take every 10th bar (~10 min resolution)
    data_1m_subset = {}
    for s in top_stocks:
        if s not in data_1m:
            continue
        df = data_1m[s].iloc[::10].copy()
        if len(df) >= MIN_BARS:
            data_1m_subset[s] = df
    print(f"  1m data: {len(data_1m_subset)} stocks @ {list(data_1m_subset.values())[0].shape[0] if data_1m_subset else 0} bars", flush=True)

    top_inds = ["RSI V2", "HMA", "SuperTrend"]
    fast_exits = [s for s in EXIT_STRATEGIES if s["name"] in ("Fixed 1:2", "Step Trail")]

    def run_stock_1m(sym, df):
        """Compute signals for one stock."""
        try:
            o = [float(x) for x in df['Open']]
            h = [float(x) for x in df['High']]
            l = [float(x) for x in df['Low']]
            c = [float(x) for x in df['Close']]
            v = [int(x) for x in df['Volume']]
            dates = list(df.index)
            n = len(c)
        except Exception:
            return []

        # Pre-compute indicator series: for each bar index, get direction
        # Use every 5th bar to cut O(n²) cost
        step = 5
        stock_results = []
        for ind_name in top_inds:
            indicator_func = LEADING_INDICATORS[ind_name]
            for th in [2, 3]:
                for idx in range(MIN_BARS, n, step):
                    i = min(idx, n - 1)
                    o_s, h_s, l_s, c_s, v_s = o[:i+1], h[:i+1], l[:i+1], c[:i+1], v[:i+1]
                    try:
                        ld = indicator_func(o_s, h_s, l_s, c_s, v_s)
                    except Exception:
                        continue
                    ld_dir = ld.get("direction", "NEUTRAL")
                    if ld_dir == "NEUTRAL":
                        continue

                    conf_long = conf_short = 0
                    for conf_name in DEFAULT_CONFIRMATIONS:
                        func = CONFIRMATION_FILTERS.get(conf_name)
                        if not func:
                            continue
                        try:
                            result = func(o_s, h_s, l_s, c_s, v_s)
                        except Exception:
                            continue
                        if result.get("confirmed") and result["direction"] == "LONG":
                            conf_long += 1
                        elif result.get("confirmed") and result["direction"] == "SHORT":
                            conf_short += 1

                    signal = "BUY" if ld_dir == "LONG" and conf_long >= th else \
                             "SELL" if ld_dir == "SHORT" and conf_short >= th else None
                    if not signal:
                        continue

                    entry_price = c[-1]
                    dt = dates[i]
                    ts = dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, 'strftime') else str(dt)[:16]
                    for strategy in fast_exits:
                        ext, exp, bars, pnl = simulate_exit(c, i, entry_price, signal, strategy)
                        stock_results.append({
                            "symbol": sym, "indicator": ind_name,
                            "confirmations": "+".join(DEFAULT_CONFIRMATIONS),
                            "threshold": th, "signal": signal,
                            "entry_price": round(entry_price, 2),
                            "exit_strategy": strategy["name"],
                            "exit_type": ext, "exit_price": round(exp, 2),
                            "bars_held": bars, "pnl_pct": round(pnl, 2),
                            "is_win": 1 if pnl > 0 else 0, "timestamp": ts,
                        })
        return stock_results

    stock_list = list(data_1m_subset.items())
    all_1m_signals = []
    n_stocks = len(stock_list)
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(run_stock_1m, sym, df): sym for sym, df in stock_list}
        done_st = 0
        for fut in as_completed(fut_map):
            try:
                all_1m_signals.extend(fut.result())
            except Exception as e:
                print(f"  Error stock: {e}", flush=True)
            done_st += 1
            elapsed = time.time() - start - daily_time
            print(f"  Stock [{done_st}/{n_stocks}] {elapsed:.0f}s — {len(all_1m_signals)} signals", flush=True)

    _1m_time = time.time() - start - daily_time
    build_report([all_1m_signals] if all_1m_signals else [], "1MIN", _1m_time)

    total = time.time() - start
    print(f"\n{'=' * 60}", flush=True)
    print(f"ALL DONE — {total:.0f}s ({total/60:.1f} min)", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
