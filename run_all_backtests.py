#!/usr/bin/env python3
"""
run_all_backtests.py — Consolidated backtest runner for ALL strategy families.

Runs on YOUR machine (needs yfinance / nselib / network + the repo venv).
Backtests every strategy family on daily + a lower/intraday timeframe and writes
ONE consolidated report (Markdown + JSON) under data/backtest_reports/.

Families covered:
  A. Strategy Builder presets   (10 named presets)      — yfinance, 1d + 1m
  B. Strategy Builder leading    (43 leading indicators)  — yfinance, 1d + 1m  [--sweep-leading]
  C. Options F&O                 (4 strategies x indices)  — nselib, daily EOD
  D. Crypto consensus            (btc/eth/gold/silver)     — Binance/yfinance, 5m
  E. EMA200 bounce scanner       (all Indian stocks)       — yfinance, daily 6mo
  F. Liquidity-sweep scripts     (mtf / optimize / daily)  — subprocess, yfinance

Usage (from repo root):
  python run_all_backtests.py                        # default basket, all families
  python run_all_backtests.py --symbols full         # all 149 stocks (slow)
  python run_all_backtests.py --families builder,crypto
  python run_all_backtests.py --timeframes 1d        # skip intraday
  python run_all_backtests.py --no-sweep-leading --no-liq-scripts
  python run_all_backtests.py --daily-days 365 --delay 1.5

Every family is wrapped in try/except so one failure never kills the run —
you always get a partial report. Errors are captured into the report.
"""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import asyncio
import subprocess
import traceback
from datetime import datetime

# --- make `app` importable no matter where we're invoked from ---
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

RPT_DIR = os.path.join(REPO_ROOT, "data", "backtest_reports")
os.makedirs(RPT_DIR, exist_ok=True)

# Curated liquid basket (indices + high-liquidity equities) — keeps runtime sane.
DEFAULT_BASKET = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
    "BHARTIARTL", "LT", "BAJFINANCE", "KOTAKBANK", "AXISBANK", "ITC",
]
INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
CRYPTO_SYMBOLS = ["btc", "eth", "gold", "silver"]
LIQ_SCRIPTS = [
    "backtest_liquidity_mtf.py",
    "backtest_liquidity_optimize.py",
    "liquidity_sweep_daily.py",
]


def _pf(v):
    """Normalize profit factor which can be the string '∞'."""
    if isinstance(v, str):
        return v
    try:
        return round(float(v), 2)
    except Exception:
        return v


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# A + B. Strategy Builder
# ---------------------------------------------------------------------------
def run_strategy_builder(symbols, timeframes, daily_days, delay, sweep_leading, sweep_symbols):
    out = {"presets": [], "leading_sweep": [], "errors": []}
    try:
        from app.services.strategy_builder import strategy_builder, StrategyBuilder, LEADING_NAMES
    except Exception as e:
        out["errors"].append(f"import failed: {e}")
        return out

    preset_names = list(StrategyBuilder.STRATEGY_PRESETS.keys())

    # --- A. Presets ---
    for preset in preset_names:
        strategy_builder.select_preset(preset)
        for sym in symbols:
            for tf in timeframes:
                try:
                    r = strategy_builder.backtest(sym, days=daily_days, interval=tf)
                    if "error" in r:
                        out["errors"].append(f"{preset}/{sym}/{tf}: {r['error']}")
                        continue
                    out["presets"].append({
                        "preset": preset, "symbol": sym, "timeframe": tf,
                        "trades": r.get("total_trades", 0),
                        "win_rate": r.get("win_rate", 0),
                        "total_return": r.get("total_return", 0),
                        "avg_return": r.get("avg_return", 0),
                        "profit_factor": _pf(r.get("profit_factor", 0)),
                        "expectancy": r.get("expectancy", 0),
                        "buy": r.get("buy_trades", 0), "sell": r.get("sell_trades", 0),
                    })
                    log(f"  [builder] {preset:22s} {sym:10s} {tf}  "
                        f"trades={r.get('total_trades',0)} wr={r.get('win_rate',0)}%")
                except Exception as e:
                    out["errors"].append(f"{preset}/{sym}/{tf}: {e}")
                time.sleep(delay)

    # --- B. Leading-indicator sweep (standard confirmation set) ---
    if sweep_leading:
        for lead in LEADING_NAMES:
            try:
                strategy_builder.select_leading(lead)
                strategy_builder.set_confirmations(["EMA 20", "RSI", "Volume", "Price Action"])
                strategy_builder.set_threshold(2)
            except Exception as e:
                out["errors"].append(f"leading select {lead}: {e}")
                continue
            for sym in sweep_symbols:
                for tf in timeframes:
                    try:
                        r = strategy_builder.backtest(sym, days=daily_days, interval=tf)
                        if "error" in r:
                            continue
                        out["leading_sweep"].append({
                            "leading": lead, "symbol": sym, "timeframe": tf,
                            "trades": r.get("total_trades", 0),
                            "win_rate": r.get("win_rate", 0),
                            "total_return": r.get("total_return", 0),
                            "profit_factor": _pf(r.get("profit_factor", 0)),
                            "expectancy": r.get("expectancy", 0),
                        })
                        log(f"  [leading] {lead:20s} {sym:10s} {tf}  "
                            f"trades={r.get('total_trades',0)} wr={r.get('win_rate',0)}%")
                    except Exception as e:
                        out["errors"].append(f"leading {lead}/{sym}/{tf}: {e}")
                    time.sleep(delay)
    return out


# ---------------------------------------------------------------------------
# C. Options F&O
# ---------------------------------------------------------------------------
def run_options(indices, period_days):
    out = {"results": [], "errors": []}
    try:
        from app.services.options_backtest.backtest_engine import backtest_engine, BacktestConfig
    except Exception as e:
        out["errors"].append(f"import failed: {e}")
        return out

    strategies = ["mega", "fii_filtered", "short_premium", "ultra_selective"]
    for strat in strategies:
        for sym in indices:
            try:
                cfg = BacktestConfig(
                    symbol=sym, instrument="OPTIDX",
                    period_days=period_days, strategy_name=strat,
                    decay_stop_pct=70.0, max_hold_days=3, max_loss_per_trade=20000.0,
                )
                res = backtest_engine.run(cfg)
                d = res.to_dict() if hasattr(res, "to_dict") else res.__dict__
                out["results"].append({
                    "strategy": strat, "symbol": sym,
                    "trades": d.get("total_trades", 0),
                    "win_rate": round(100 * d.get("win_rate", 0), 1),
                    "total_pnl": round(d.get("total_pnl", 0), 0),
                    "profit_factor": _pf(d.get("profit_factor", 0)),
                    "max_dd_pct": round(d.get("max_drawdown_pct", 0), 1),
                    "sharpe": round(d.get("sharpe_ratio", 0), 2),
                })
                log(f"  [options] {strat:16s} {sym:10s}  "
                    f"trades={d.get('total_trades',0)} pnl={round(d.get('total_pnl',0),0)}")
            except Exception as e:
                out["errors"].append(f"{strat}/{sym}: {e}")
    return out


# ---------------------------------------------------------------------------
# D. Crypto  (async)
# ---------------------------------------------------------------------------
def run_crypto(symbols):
    out = {"results": [], "errors": []}
    try:
        from app.services.crypto_strategy_service import crypto_strategy_service
    except Exception as e:
        out["errors"].append(f"import failed: {e}")
        return out

    async def _one(sym):
        return sym, await crypto_strategy_service.backtest(sym)

    for sym in symbols:
        try:
            _, r = asyncio.run(_one(sym))
            out["results"].append({
                "symbol": r.get("symbol", sym.upper()),
                "strategy": r.get("strategy", ""),
                "indicators": r.get("indicators", ""),
                "trades": r.get("total_trades", 0),
                "win_rate": r.get("win_rate", 0),
                "total_return_pct": r.get("total_return_pct", 0),
                "profit_factor": _pf(r.get("profit_factor", 0)),
                "max_dd_pct": r.get("max_drawdown_pct", 0),
                "sharpe": r.get("sharpe_ratio", 0),
            })
            log(f"  [crypto] {sym:6s}  trades={r.get('total_trades',0)} "
                f"wr={r.get('win_rate',0)}% ret={r.get('total_return_pct',0)}%")
        except Exception as e:
            out["errors"].append(f"{sym}: {e}")
    return out


# ---------------------------------------------------------------------------
# E. EMA200 bounce  (async)
# ---------------------------------------------------------------------------
def run_ema_bounce():
    out = {"result": {}, "errors": []}
    try:
        from app.services.ema_bounce_scanner import run_backtest
    except Exception as e:
        out["errors"].append(f"import failed: {e}")
        return out
    try:
        r = asyncio.run(run_backtest())
        out["result"] = {
            "total_trades": r.get("total_trades", 0),
            "win_rate": r.get("win_rate", 0),
            "avg_return": r.get("avg_return", 0),
            "sharpe": r.get("sharpe", 0),
            "buy_trades": r.get("buy_trades", 0),
            "sell_trades": r.get("sell_trades", 0),
            "stocks_with_signals": r.get("stocks_with_signals", 0),
        }
        log(f"  [ema200] trades={r.get('total_trades',0)} wr={r.get('win_rate',0)}%")
    except Exception as e:
        out["errors"].append(str(e))
    return out


# ---------------------------------------------------------------------------
# F. Liquidity-sweep scripts (subprocess) — capture their generated reports
# ---------------------------------------------------------------------------
def run_liq_scripts(timeout):
    out = {"runs": [], "errors": []}
    before = set(os.listdir(RPT_DIR))
    for script in LIQ_SCRIPTS:
        path = os.path.join(REPO_ROOT, script)
        if not os.path.exists(path):
            out["errors"].append(f"missing: {script}")
            continue
        try:
            log(f"  [liq] running {script} (timeout {timeout}s) ...")
            p = subprocess.run([sys.executable, path], cwd=REPO_ROOT,
                               capture_output=True, text=True, timeout=timeout)
            after = set(os.listdir(RPT_DIR))
            new_files = sorted(after - before)
            before = after
            out["runs"].append({
                "script": script, "returncode": p.returncode,
                "stdout_tail": p.stdout[-1500:], "stderr_tail": p.stderr[-600:],
                "new_report_files": new_files,
            })
        except subprocess.TimeoutExpired:
            out["errors"].append(f"{script}: TIMEOUT after {timeout}s")
        except Exception as e:
            out["errors"].append(f"{script}: {e}")
    return out


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def _agg_presets(rows):
    """Aggregate preset rows -> per-(preset,timeframe) means."""
    agg = {}
    for r in rows:
        k = (r["preset"], r["timeframe"])
        a = agg.setdefault(k, {"n": 0, "trades": 0, "wr": 0.0, "ret": 0.0})
        a["n"] += 1
        a["trades"] += r["trades"]
        a["wr"] += r["win_rate"]
        a["ret"] += r["total_return"]
    rows_out = []
    for (preset, tf), a in agg.items():
        n = max(a["n"], 1)
        rows_out.append({
            "preset": preset, "timeframe": tf, "symbols": a["n"],
            "avg_win_rate": round(a["wr"] / n, 1),
            "total_trades": a["trades"],
            "avg_total_return": round(a["ret"] / n, 2),
        })
    rows_out.sort(key=lambda x: (-x["avg_win_rate"], -x["avg_total_return"]))
    return rows_out


def write_reports(data, ts):
    json_path = os.path.join(RPT_DIR, f"all_strategies_{ts}.json")
    md_path = os.path.join(RPT_DIR, f"all_strategies_{ts}.md")
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    L = []
    L.append(f"# Full Strategy Backtest Report — {ts}")
    L.append("")
    L.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"Config: {json.dumps(data['meta'])}")
    L.append("")

    # A. Strategy Builder presets
    b = data.get("builder", {})
    if b.get("presets"):
        L.append("## A. Strategy Builder — Presets (avg across symbols)")
        L.append("")
        L.append("| Preset | TF | Symbols | Avg Win% | Trades | Avg Return% |")
        L.append("|---|---|--:|--:|--:|--:|")
        for r in _agg_presets(b["presets"]):
            L.append(f"| {r['preset']} | {r['timeframe']} | {r['symbols']} | "
                     f"{r['avg_win_rate']} | {r['total_trades']} | {r['avg_total_return']} |")
        L.append("")

    # B. Leading sweep (top 15 by win rate)
    if b.get("leading_sweep"):
        L.append("## B. Strategy Builder — Leading indicator sweep (top 15 by win%)")
        L.append("")
        L.append("| Leading | Symbol | TF | Trades | Win% | Return% | PF |")
        L.append("|---|---|---|--:|--:|--:|--:|")
        rows = sorted(b["leading_sweep"],
                      key=lambda x: (-(x["win_rate"] or 0), -(x["total_return"] or 0)))[:15]
        for r in rows:
            L.append(f"| {r['leading']} | {r['symbol']} | {r['timeframe']} | "
                     f"{r['trades']} | {r['win_rate']} | {r['total_return']} | {r['profit_factor']} |")
        L.append("")

    # C. Options
    o = data.get("options", {})
    if o.get("results"):
        L.append("## C. Options F&O (daily EOD)")
        L.append("")
        L.append("| Strategy | Symbol | Trades | Win% | PnL ₹ | PF | MaxDD% | Sharpe |")
        L.append("|---|---|--:|--:|--:|--:|--:|--:|")
        for r in sorted(o["results"], key=lambda x: -(x["total_pnl"] or 0)):
            L.append(f"| {r['strategy']} | {r['symbol']} | {r['trades']} | {r['win_rate']} | "
                     f"{r['total_pnl']} | {r['profit_factor']} | {r['max_dd_pct']} | {r['sharpe']} |")
        L.append("")

    # D. Crypto
    c = data.get("crypto", {})
    if c.get("results"):
        L.append("## D. Crypto consensus (5m)")
        L.append("")
        L.append("| Symbol | Strategy | Trades | Win% | Return% | PF | MaxDD% | Sharpe |")
        L.append("|---|---|--:|--:|--:|--:|--:|--:|")
        for r in c["results"]:
            L.append(f"| {r['symbol']} | {r['strategy']} | {r['trades']} | {r['win_rate']} | "
                     f"{r['total_return_pct']} | {r['profit_factor']} | {r['max_dd_pct']} | {r['sharpe']} |")
        L.append("")

    # E. EMA bounce
    e = data.get("ema_bounce", {})
    if e.get("result"):
        r = e["result"]
        L.append("## E. EMA200 Bounce (daily, all stocks)")
        L.append("")
        L.append(f"- Total trades: {r.get('total_trades')}  |  Win rate: {r.get('win_rate')}%  "
                 f"|  Avg return: {r.get('avg_return')}%  |  Sharpe: {r.get('sharpe')}")
        L.append(f"- BUY: {r.get('buy_trades')}  |  SELL: {r.get('sell_trades')}  "
                 f"|  Stocks with signals: {r.get('stocks_with_signals')}")
        L.append("")

    # F. Liquidity scripts
    lq = data.get("liq_scripts", {})
    if lq.get("runs"):
        L.append("## F. Liquidity-sweep scripts")
        L.append("")
        for run in lq["runs"]:
            L.append(f"### {run['script']} (exit {run['returncode']})")
            if run.get("new_report_files"):
                L.append(f"New report files: {', '.join(run['new_report_files'])}")
            L.append("```")
            L.append(run["stdout_tail"].strip() or "(no stdout)")
            L.append("```")
            L.append("")

    # Errors
    all_errs = []
    for fam in ("builder", "options", "crypto", "ema_bounce", "liq_scripts"):
        all_errs += [f"[{fam}] {x}" for x in data.get(fam, {}).get("errors", [])]
    if all_errs:
        L.append("## Errors / skips")
        L.append("")
        for x in all_errs[:80]:
            L.append(f"- {x}")
        L.append("")

    with open(md_path, "w") as f:
        f.write("\n".join(L))
    return json_path, md_path


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Run all strategy backtests.")
    ap.add_argument("--symbols", default="basket",
                    help="'basket' (default), 'full' (all 149), 'indices', or comma list")
    ap.add_argument("--timeframes", default="1d,1m", help="comma list, e.g. 1d,1m")
    ap.add_argument("--daily-days", type=int, default=365)
    ap.add_argument("--families", default="builder,options,crypto,ema,liq",
                    help="comma subset of: builder,options,crypto,ema,liq")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between yfinance calls")
    ap.add_argument("--sweep-leading", dest="sweep_leading", action="store_true", default=True)
    ap.add_argument("--no-sweep-leading", dest="sweep_leading", action="store_false")
    ap.add_argument("--sweep-symbols", default="RELIANCE,NIFTY",
                    help="symbols for the 43-leading-indicator sweep")
    ap.add_argument("--options-days", type=int, default=90)
    ap.add_argument("--liq-timeout", type=int, default=1800, help="per-script timeout (s)")
    args = ap.parse_args()

    if args.symbols == "basket":
        symbols = DEFAULT_BASKET
    elif args.symbols == "indices":
        symbols = INDICES
    elif args.symbols == "full":
        from app.data.stocks import INDIAN_STOCKS
        symbols = [s.upper() for s in INDIAN_STOCKS]
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    families = set(f.strip() for f in args.families.split(","))
    sweep_symbols = [s.strip().upper() for s in args.sweep_symbols.split(",") if s.strip()]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    data = {"meta": {
        "timestamp": ts, "symbols": symbols, "timeframes": timeframes,
        "daily_days": args.daily_days, "families": sorted(families),
        "sweep_leading": args.sweep_leading, "sweep_symbols": sweep_symbols,
    }}

    log(f"START — families={sorted(families)} symbols={len(symbols)} tf={timeframes}")

    if "builder" in families:
        log("== A/B. Strategy Builder ==")
        data["builder"] = run_strategy_builder(
            symbols, timeframes, args.daily_days, args.delay,
            args.sweep_leading, sweep_symbols)
    if "options" in families:
        log("== C. Options F&O ==")
        data["options"] = run_options(INDICES, args.options_days)
    if "crypto" in families:
        log("== D. Crypto ==")
        data["crypto"] = run_crypto(CRYPTO_SYMBOLS)
    if "ema" in families:
        log("== E. EMA200 bounce ==")
        data["ema_bounce"] = run_ema_bounce()
    if "liq" in families:
        log("== F. Liquidity-sweep scripts ==")
        data["liq_scripts"] = run_liq_scripts(args.liq_timeout)

    json_path, md_path = write_reports(data, ts)
    log(f"DONE. Report:\n  {md_path}\n  {json_path}")
    print(f"\n=== REPORT WRITTEN ===\n{md_path}\n{json_path}")


if __name__ == "__main__":
    main()
