"""
Batch backtest: all 36 leading indicators on all 149 stocks, 30 days daily data.
Downloads data once per stock, then runs all indicators on cached data.
"""
import sys, os, json, time, csv, pickle
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor
from datetime import datetime
from app.services.strategy_builder import (
    LEADING_INDICATORS, LEADING_NAMES, CONFIRMATION_FILTERS
)
from app.data.stocks import INDIAN_STOCKS
import yfinance as yf
import pandas as pd
import numpy as np

REPORT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "ohlc_cache")
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

DEFAULT_CONFIRMATIONS = ["EMA 20", "EMA 50", "MACD", "RSI", "Volume", "Price Action"]
THRESHOLD = 3
MIN_BARS = 20
DAYS = 30

def download_all_stocks():
    """Download 30d daily data for all stocks and cache to disk."""
    cached = 0
    failed = []
    for symbol in INDIAN_STOCKS:
        cache_path = os.path.join(CACHE_DIR, f"{symbol}.pkl")
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
            cached += 1
            continue
        try:
            sym = symbol.upper()
            yf_sym = f"{sym}.NS"
            df = yf.download(yf_sym, period=f"{DAYS}d", interval="1d", progress=False, auto_adjust=True)
            if df.empty or len(df) < MIN_BARS:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            with open(cache_path, "wb") as f:
                pickle.dump(df, f)
            time.sleep(0.3)  # rate limit
        except Exception as e:
            failed.append((symbol, str(e)))
    print(f"Downloaded {len(INDIAN_STOCKS) - len(failed)} stocks ({cached} cached, {len(failed)} failed)")

def load_cached(symbol):
    """Load cached OHLC data for a symbol."""
    cache_path = os.path.join(CACHE_DIR, f"{symbol}.pkl")
    if not os.path.exists(cache_path):
        return None
    with open(cache_path, "rb") as f:
        return pickle.load(f)

def run_indicator_on_stock(symbol, df, indicator_name, indicator_func, confirmations):
    """Run one indicator on one stock's cached data. Returns signals list."""
    try:
        opens = [float(row['Open']) for _, row in df.iterrows()]
        highs = [float(row['High']) for _, row in df.iterrows()]
        lows = [float(row['Low']) for _, row in df.iterrows()]
        closes = [float(row['Close']) for _, row in df.iterrows()]
        volumes = [int(row['Volume']) for _, row in df.iterrows()]
        dates = list(df.index)

        signals = []
        for i in range(MIN_BARS, len(closes)):
            o, h, l, c, v = opens[:i+1], highs[:i+1], lows[:i+1], closes[:i+1], volumes[:i+1]

            ld = indicator_func(o, h, l, c, v)
            leading_dir = ld.get("direction", "NEUTRAL")
            if leading_dir == "NEUTRAL":
                continue

            conf_long = 0
            conf_short = 0
            for name in confirmations:
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

            signal = "HOLD"
            if leading_dir == "LONG" and conf_long >= THRESHOLD:
                signal = "BUY"
            elif leading_dir == "SHORT" and conf_short >= THRESHOLD:
                signal = "SELL"

            if signal in ("BUY", "SELL"):
                dt = dates[i]
                if hasattr(dt, 'strftime'):
                    ts = dt.strftime("%Y-%m-%d")
                else:
                    ts = str(dt)[:10]
                ts += " 15:30"  # market close IST

                entry_price = c[-1]
                # Forward price checks (how did price move after signal?)
                fp1 = closes[i+1] if i+1 < len(closes) else None
                fp3 = closes[i+3] if i+3 < len(closes) else None
                fp5 = closes[i+5] if i+5 < len(closes) else None

                def pnl(exit_p):
                    if exit_p is None: return None
                    if signal == "BUY":
                        return round((exit_p - entry_price) / entry_price * 100, 2)
                    else:  # SELL
                        return round((entry_price - exit_p) / entry_price * 100, 2)

                signals.append({
                    "symbol": symbol.upper(),
                    "indicator": indicator_name,
                    "signal": signal,
                    "price": round(entry_price, 2),
                    "timestamp": ts,
                    "leading_dir": leading_dir,
                    "conf_long": conf_long,
                    "conf_short": conf_short,
                    "pnl_1d": pnl(fp1),
                    "pnl_3d": pnl(fp3),
                    "pnl_5d": pnl(fp5),
                })

        return signals
    except Exception as e:
        return []

def run_batch():
    start_time = time.time()

    # Phase 1: Download all data
    print("=" * 60)
    print(f"Phase 1: Downloading {len(INDIAN_STOCKS)} stocks ({DAYS}d daily)...")
    print("=" * 60)
    download_all_stocks()
    print()

    # Phase 2: Load all cached data
    cached_data = {}
    for symbol in INDIAN_STOCKS:
        df = load_cached(symbol)
        if df is not None and len(df) >= MIN_BARS:
            cached_data[symbol] = df
    print(f"Loaded {len(cached_data)} stocks with sufficient data")
    print()

    # Phase 3: Run all indicators on all stocks
    print("=" * 60)
    print(f"Phase 2: Running {len(LEADING_NAMES)} indicators × {len(cached_data)} stocks")
    print("=" * 60)

    all_results = {}
    total_tasks = len(LEADING_NAMES) * len(cached_data)
    completed = 0

    for indicator_name in LEADING_NAMES:
        indicator_func = LEADING_INDICATORS[indicator_name]
        stock_signals = {}
        batch_start = time.time()

        with ThreadPoolExecutor(max_workers=20) as pool:
            fut_map = {}
            for sym, df in cached_data.items():
                fut = pool.submit(run_indicator_on_stock, sym, df, indicator_name, indicator_func, DEFAULT_CONFIRMATIONS)
                fut_map[fut] = sym

            for fut in as_completed(fut_map):
                sym = fut_map[fut]
                try:
                    sigs = fut.result()
                    if sigs:
                        stock_signals[sym.upper()] = sigs
                except Exception:
                    pass
                completed += 1

        elapsed = time.time() - batch_start
        total_sigs = sum(len(v) for v in stock_signals.values())
        print(f"  [{indicator_name:20s}] {total_sigs:5d} signals across {len(stock_signals):3d} stocks in {elapsed:.0f}s")

        if stock_signals:
            all_results[indicator_name] = stock_signals

    total_time = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"All done in {total_time:.0f}s ({total_time/60:.1f} min)")
    print(f"{'=' * 60}")

    # Phase 4: Generate report
    generate_report(all_results, total_time)
    return all_results

def generate_report(results, total_time):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = os.path.join(REPORT_DIR, f"backtest_report_{timestamp}.md")
    csv_file = os.path.join(REPORT_DIR, f"backtest_trades_{timestamp}.csv")

    md_lines = [
        f"# Batch Backtest Report — {DAYS}-Day Daily ({datetime.now().strftime('%Y-%m-%d')})",
        f"",
        f"**Stocks**: {len(INDIAN_STOCKS)} | **Indicators**: {len(LEADING_NAMES)}",
        f"**Confirmations**: {', '.join(DEFAULT_CONFIRMATIONS)} (threshold ≥ {THRESHOLD})",
        f"**Duration**: {total_time:.0f}s ({total_time/60:.1f} min)",
        f"",
    ]

    csv_rows = []
    trade_count = 0
    indicator_stats = {}

    for ind_name, stock_sigs in sorted(results.items()):
        total_buys = 0
        total_sells = 0
        wins_1d = 0
        wins_3d = 0
        wins_5d = 0
        total_pnl_1d = 0
        total_pnl_3d = 0
        total_pnl_5d = 0
        count_pnl_1d = 0
        count_pnl_3d = 0
        count_pnl_5d = 0
        stocks_with_signals = len(stock_sigs)

        for sym, sigs in stock_sigs.items():
            for s in sigs:
                csv_rows.append(s)
                if s["signal"] == "BUY":
                    total_buys += 1
                elif s["signal"] == "SELL":
                    total_sells += 1
                trade_count += 1

                if s.get("pnl_1d") is not None:
                    total_pnl_1d += s["pnl_1d"]
                    count_pnl_1d += 1
                    if s["pnl_1d"] > 0:
                        wins_1d += 1
                if s.get("pnl_3d") is not None:
                    total_pnl_3d += s["pnl_3d"]
                    count_pnl_3d += 1
                    if s["pnl_3d"] > 0:
                        wins_3d += 1
                if s.get("pnl_5d") is not None:
                    total_pnl_5d += s["pnl_5d"]
                    count_pnl_5d += 1
                    if s["pnl_5d"] > 0:
                        wins_5d += 1

        indicator_stats[ind_name] = {
            "stocks": stocks_with_signals,
            "buys": total_buys,
            "sells": total_sells,
            "total": total_buys + total_sells,
            "wins_1d": wins_1d,
            "wins_3d": wins_3d,
            "wins_5d": wins_5d,
            "count_1d": count_pnl_1d,
            "count_3d": count_pnl_3d,
            "count_5d": count_pnl_5d,
            "avg_pnl_1d": round(total_pnl_1d / count_pnl_1d, 2) if count_pnl_1d else 0,
            "avg_pnl_3d": round(total_pnl_3d / count_pnl_3d, 2) if count_pnl_3d else 0,
            "avg_pnl_5d": round(total_pnl_5d / count_pnl_5d, 2) if count_pnl_5d else 0,
        }

    # Summary table with win rates
    md_lines.append("## Indicator Performance — Ranked by 5-Day Win Rate")
    md_lines.append("")
    md_lines.append(f"| Indicator | Stocks | BUY | SELL | Win% 1D | Win% 3D | Win% 5D | Avg P&L 5D |")
    md_lines.append(f"|-----------|--------|-----|------|---------|---------|---------|------------|")
    sorted_inds = sorted(
        indicator_stats.items(),
        key=lambda x: (x[1]["wins_5d"] / x[1]["count_5d"] * 100) if x[1]["count_5d"] > 0 else 0,
        reverse=True
    )
    for ind_name, st in sorted_inds:
        wr1 = f"{st['wins_1d']/st['count_1d']*100:.0f}%" if st['count_1d'] else "N/A"
        wr3 = f"{st['wins_3d']/st['count_3d']*100:.0f}%" if st['count_3d'] else "N/A"
        wr5 = f"{st['wins_5d']/st['count_5d']*100:.0f}%" if st['count_5d'] else "N/A"
        ap5 = f"{st['avg_pnl_5d']:+.2f}%" if st['count_5d'] else "N/A"
        md_lines.append(
            f"| {ind_name} | {st['stocks']} | {st['buys']} | {st['sells']} | {wr1} | {wr3} | {wr5} | {ap5} |"
        )

    md_lines.append("")
    md_lines.append(f"**Total signals**: {trade_count}")
    md_lines.append("")

    # Top 5 by win rate + volume
    md_lines.append("## Recommended Combinations (Highest 5D Win Rate)")
    md_lines.append("")
    top5_wr = [x for x in sorted_inds if x[1]["count_5d"] >= 3][:5]
    for i, (name, st) in enumerate(top5_wr, 1):
        wr5 = f"{st['wins_5d']/st['count_5d']*100:.0f}%"
        md_lines.append(f"{i}. **{name}** — Win Rate: {wr5} ({st['wins_5d']}/{st['count_5d']}) | Avg P&L 5D: {st['avg_pnl_5d']:+.2f}% | {st['buys']} BUY / {st['sells']} SELL on {st['stocks']} stocks")

    # Per-stock detail
    md_lines.append("")
    md_lines.append("## Per-Stock Signal Details")
    md_lines.append("")
    for ind_name in sorted(results.keys()):
        md_lines.append(f"### {ind_name}")
        md_lines.append("")
        stock_sigs = results[ind_name]
        for sym in sorted(stock_sigs.keys()):
            sigs = stock_sigs[sym]
            first = sigs[0]["timestamp"][:16] if sigs else ""
            last = sigs[-1]["timestamp"][:16] if sigs else ""
            md_lines.append(f"**{sym}** — {len(sigs)} signals ({first} to {last})")
            for s in sigs:
                emoji = "🟢" if s["signal"] == "BUY" else "🔴"
                ts = s["timestamp"]
                pnl_str = ""
                if s.get("pnl_5d") is not None:
                    pnl_str = f" | P&L 5D: {s['pnl_5d']:+.2f}%"
                md_lines.append(f"  {emoji} {s['signal']:5s} @ ₹{s['price']:>8.2f} on {ts}{pnl_str}")
            md_lines.append("")

    with open(report_file, "w") as f:
        f.write("\n".join(md_lines))

    if csv_rows:
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
            writer.writeheader()
            writer.writerows(csv_rows)

    summary = {
        "generated": datetime.now().isoformat(),
        "stocks": len(INDIAN_STOCKS),
        "indicators": len(LEADING_NAMES),
        "duration_seconds": total_time,
        "total_signals": trade_count,
        "indicator_stats": indicator_stats,
    }
    summary_file = os.path.join(REPORT_DIR, f"summary_{timestamp}.json")
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nReport: {report_file}")
    print(f"CSV:    {csv_file}")
    print(f"Summary: {summary_file}")

    # Print Telegram-ready summary
    print("\n" + "=" * 50)
    print("TELEGRAM REPORT")
    print("=" * 50)
    print(f"📊 *30-Day Backtest Results*")
    print(f"Indicators: {len(LEADING_NAMES)} | Stocks: {len(INDIAN_STOCKS)}")
    print(f"Total signals: {trade_count}")
    print(f"Duration: {total_time:.0f}s")
    print()
    top5 = sorted(indicator_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:5]
    print("*Top 5 Indicators:*")
    for name, st in top5:
        print(f"  • {name}: {st['buys']} BUY / {st['sells']} SELL on {st['stocks']} stocks")
    print()
    print(f"Full report: {report_file}")

if __name__ == "__main__":
    run_batch()
