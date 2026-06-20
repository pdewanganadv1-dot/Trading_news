"""
1-Minute Liquidity Sweep Strategy Backtest
Based on YouTube transcript: Market Maker Model — Liquidity Sweeps

Key fixes from v1:
- S/R levels are based on prior-day/session highs/lows (not 1m swing points)
- Swing detection uses wider lookback (left=5, right=5) for stronger levels
- Cleaner trade structure with entry_price as explicit field
- Stops set at sweep extreme (per video rules)
- Min RR filter to ensure high quality setups
"""
import sys, os, pickle, time, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from app.data.stocks import INDIAN_STOCKS
import yfinance as yf
import pandas as pd

CACHE_1M = os.path.join(os.path.dirname(__file__), "data", "ohlc_1m_cache")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")
os.makedirs(CACHE_1M, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

MIN_BARS = 100
MAX_STOP_PCT = 2.0
MIN_RR = 3.0


def swing_highs(h, left=5, right=5):
    n = len(h)
    r = [0.0] * n
    for i in range(left, n - right):
        if all(h[j] < h[i] for j in range(i - left, i) if j >= 0) and \
           all(h[j] < h[i] for j in range(i + 1, i + right + 1) if j < n):
            r[i] = 1.0
    return r


def swing_lows(l, left=5, right=5):
    n = len(l)
    r = [0.0] * n
    for i in range(left, n - right):
        if all(l[j] > l[i] for j in range(i - left, i) if j >= 0) and \
           all(l[j] > l[i] for j in range(i + 1, i + right + 1) if j < n):
            r[i] = 1.0
    return r


def prev_swing(i, arr):
    for j in range(i - 1, -1, -1):
        if j < len(arr) and arr[j] == 1.0:
            return j
    return None


def sim(entry_idx, direction, entry, stop, target, closes, highs, lows):
    be = False
    cur_stop = stop
    for j in range(entry_idx + 1, len(closes)):
        ch, cl = highs[j], lows[j]
        if not be:
            if direction == "BUY" and ch >= entry + (entry - stop):
                be = True
                cur_stop = entry
            elif direction == "SELL" and cl <= entry - (stop - entry):
                be = True
                cur_stop = entry
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


def make_trade(entry_i, direction, entry, stop, target, closes, highs, lows, dates, extra):
    r = sim(entry_i, direction, entry, stop, target, closes, highs, lows)
    if r is None:
        return None
    rr = (target - entry) / (entry - stop) if direction == "BUY" and entry != stop else ((entry - target) / (stop - entry) if direction == "SELL" and stop != entry else 0)
    stop_pct = abs(entry - stop) / entry * 100
    return dict(extra, **{
        "direction": direction,
        "entry_time": dates[entry_i].strftime("%Y-%m-%d %H:%M") if hasattr(dates[entry_i], "strftime") else str(dates[entry_i])[:16],
        "exit_time": dates[r["exit_idx"]].strftime("%Y-%m-%d %H:%M") if hasattr(dates[r["exit_idx"]], "strftime") else str(dates[r["exit_idx"]])[:16],
        "entry_price": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "rr_setup": round(rr, 1),
        "stop_pct": round(stop_pct, 2),
        **r,
    })


def backtest_stock(symbol):
    try:
        path = os.path.join(CACHE_1M, f"{symbol}.pkl")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            df = pickle.load(f)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < MIN_BARS:
            return None

        opens = [float(x) for x in df["Open"]]
        highs = [float(x) for x in df["High"]]
        lows = [float(x) for x in df["Low"]]
        closes = [float(x) for x in df["Close"]]
        dates = list(df.index)

        sh = swing_highs(highs, 5, 5)
        sl = swing_lows(lows, 5, 5)

        # Build session levels: each trading day's high/low
        session_highs = {}
        session_lows = {}
        for idx, dt in enumerate(dates):
            day_key = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
            if day_key not in session_highs:
                session_highs[day_key] = highs[idx]
                session_lows[day_key] = lows[idx]
            else:
                session_highs[day_key] = max(session_highs[day_key], highs[idx])
                session_lows[day_key] = min(session_lows[day_key], lows[idx])

        trades = []
        entries = set()

        for i in range(MIN_BARS, len(closes)):
            if i in entries:
                continue

            dt = dates[i]
            day_key = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]

            sh_idx = prev_swing(i, sh)
            sl_idx = prev_swing(i, sl)

            ci = closes[i]
            hi = highs[i]
            li = lows[i]
            oi = opens[i]

            # ── 1) PRIOR SESSION HIGH/LOW SWEEPS (External, Major Levels) ──

            # Get yesterday's session levels
            yesterday = (dt - timedelta(days=1)).strftime("%Y-%m-%d") if hasattr(dt, "strftime") else None
            if yesterday and yesterday in session_highs:
                prev_high = session_highs[yesterday]
                prev_low = session_lows[yesterday]

                # Sellside sweep of yesterday's low → BUY
                if li < prev_low and ci > prev_low:
                    entry = ci
                    stop = min(li, prev_low - (hi - li) * 0.05)
                    if abs(entry - stop) / entry * 100 <= MAX_STOP_PCT:
                        target = prev_high if prev_high > entry else entry + (entry - stop) * 10
                        rr = (target - entry) / (entry - stop) if entry != stop else 0
                        if rr >= MIN_RR:
                            t = make_trade(i, "BUY", entry, stop, target, closes, highs, lows, dates, {"symbol": symbol.upper(), "type": "session_sellside"})
                            if t: trades.append(t); entries.add(i)

                # Buyside sweep of yesterday's high → SELL
                if i not in entries and hi > prev_high and ci < prev_high:
                    entry = ci
                    stop = max(hi, prev_high + (hi - li) * 0.05)
                    if abs(entry - stop) / entry * 100 <= MAX_STOP_PCT:
                        target = prev_low if prev_low < entry else entry - (stop - entry) * 10
                        rr = (entry - target) / (stop - entry) if stop != entry else 0
                        if rr >= MIN_RR:
                            t = make_trade(i, "SELL", entry, stop, target, closes, highs, lows, dates, {"symbol": symbol.upper(), "type": "session_buyside"})
                            if t: trades.append(t); entries.add(i)

            # ── 2) SWING LEVEL SWEEPS (1m internal swings, 25-30+ candle rule) ──
            #    Only consider swings that are strong enough (10+candle lookback)

            # Sellside sweep of swing low → BUY (reversal or bounce)
            if sl_idx is not None and i not in entries:
                candles_since = i - sl_idx
                if candles_since >= 20:
                    swing_low = lows[sl_idx]
                    if li < swing_low and ci > swing_low:
                        entry = ci
                        stop = min(li, swing_low - (hi - li) * 0.05)
                        if abs(entry - stop) / entry * 100 <= MAX_STOP_PCT:
                            tgt = None
                            for j in range(i, max(i - 80, -1), -1):
                                if j < len(sh) and sh[j] == 1.0 and highs[j] > entry:
                                    tgt = highs[j]; break
                            if tgt is None:
                                tgt = entry + (entry - stop) * 10
                            rr = (tgt - entry) / (entry - stop) if entry != stop else 0
                            if rr >= MIN_RR:
                                t = make_trade(i, "BUY", entry, stop, tgt, closes, highs, lows, dates, {"symbol": symbol.upper(), "type": "swing_sellside", "candles_since_level": candles_since, "sweep_level": round(swing_low, 2)})
                                if t: trades.append(t); entries.add(i)

            # Buyside sweep of swing high → SELL (reversal or rejection)
            if sh_idx is not None and i not in entries:
                candles_since = i - sh_idx
                if candles_since >= 20:
                    swing_high = highs[sh_idx]
                    if hi > swing_high and ci < swing_high:
                        entry = ci
                        stop = max(hi, swing_high + (hi - li) * 0.05)
                        if abs(entry - stop) / entry * 100 <= MAX_STOP_PCT:
                            tgt = None
                            for j in range(i, max(i - 80, -1), -1):
                                if j < len(sl) and sl[j] == 1.0 and lows[j] < entry:
                                    tgt = lows[j]; break
                            if tgt is None:
                                tgt = entry - (stop - entry) * 10
                            rr = (entry - tgt) / (stop - entry) if stop != entry else 0
                            if rr >= MIN_RR:
                                t = make_trade(i, "SELL", entry, stop, tgt, closes, highs, lows, dates, {"symbol": symbol.upper(), "type": "swing_buyside", "candles_since_level": candles_since, "sweep_level": round(swing_high, 2)})
                                if t: trades.append(t); entries.add(i)

        return trades if trades else None
    except Exception as e:
        return None


def download_1m():
    to_dl = [s for s in INDIAN_STOCKS if not os.path.exists(os.path.join(CACHE_1M, f"{s}.pkl"))]
    if not to_dl:
        return
    print(f"Downloading 1m for {len(to_dl)} stocks...")
    def dl(sym):
        try:
            df = yf.download(f"{sym.upper()}.NS", period="7d", interval="1m", progress=False, auto_adjust=True)
            if df.empty or len(df) < MIN_BARS: return
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df) >= MIN_BARS:
                with open(os.path.join(CACHE_1M, f"{sym}.pkl"), "wb") as f:
                    pickle.dump(df, f)
        except Exception:
            pass
    from tqdm import tqdm
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(tqdm(pool.map(dl, to_dl), total=len(to_dl), desc="1m DL"))


def load_cached():
    data = {}
    for sym in INDIAN_STOCKS:
        path = os.path.join(CACHE_1M, f"{sym}.pkl")
        if os.path.exists(path):
            with open(path, "rb") as f:
                df = pickle.load(f)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df) >= MIN_BARS:
                data[sym] = df
    return data


def run():
    t0 = time.time()
    print("=" * 60)
    print("LIQUIDITY SWEEP STRATEGY — 1-Minute Backtest (v3)")
    print("=" * 60)
    print(f"Stocks: {len(INDIAN_STOCKS)}")
    print(f"Max stop: {MAX_STOP_PCT}% | Min RR: {MIN_RR}:1")
    print()

    download_1m()
    all_data = load_cached()
    print(f"Loaded {len(all_data)} stocks\n")

    all_trades = []
    sym_with = 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(backtest_stock, sym): sym for sym in all_data}
        for fut in as_completed(fut_map):
            try:
                trades = fut.result(timeout=120)
                if trades:
                    all_trades.extend(trades)
                    sym_with += 1
                    print(f"  {fut_map[fut].upper():15s} → {len(trades):3d} trades")
            except Exception:
                pass

    print(f"\nDone in {time.time()-t0:.0f}s")
    report(all_trades, sym_with, time.time() - t0)


def report(all_trades, sym_with, elapsed):
    if not all_trades:
        print("No trades!")
        return

    total = len(all_trades)
    types = {}
    for t in all_trades:
        types.setdefault(t["type"], []).append(t)

    buys = [t for t in all_trades if t["direction"] == "BUY"]
    sells = [t for t in all_trades if t["direction"] == "SELL"]
    winners = [t for t in all_trades if t["pnl_pct"] > 0]
    losers = [t for t in all_trades if t["pnl_pct"] <= 0]
    targets = [t for t in all_trades if t["exit_reason"] == "target"]
    stops = [t for t in all_trades if t["exit_reason"] == "stop"]
    expired = [t for t in all_trades if t["exit_reason"] == "expired"]
    be = [t for t in all_trades if t["be_triggered"]]

    wr = len(winners) / total * 100
    avg_pnl = sum(t["pnl_pct"] for t in all_trades) / total
    avg_win = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
    avg_loss = sum(abs(t["pnl_pct"]) for t in losers) / len(losers) if losers else 0
    rr_r = avg_win / avg_loss if avg_loss > 0 else 0
    tot_r = sum(t["pnl_pct"] for t in all_trades)
    gw = sum(t["pnl_pct"] for t in winners)
    gl = abs(sum(t["pnl_pct"] for t in losers))
    pf = gw / gl if gl > 0 else 999
    avg_bars = sum(t["bars_held"] for t in all_trades) / total
    avg_rr_s = sum(t["rr_setup"] for t in all_trades) / total

    print(f"\n{'=' * 60}")
    print("RESULTS")
    print(f"{'=' * 60}")
    print(f"Total trades:            {total}")
    print(f"Stocks with trades:      {sym_with}")
    print(f"BUY:  {len(buys)}  SELL: {len(sells)}")
    print(f"Types: {', '.join(f'{k}={len(v)}' for k,v in sorted(types.items()))}")
    print(f"Winners: {len(winners)}  Losers: {len(losers)}")
    print(f"Targets: {len(targets)}  Stops: {len(stops)}  Expired: {len(expired)}")
    print(f"BE triggered: {len(be)} ({len(be)/total*100:.1f}%)")
    print()
    print(f"Win Rate:               {wr:.1f}%")
    print(f"Avg PnL:                {avg_pnl:+.4f}%")
    print(f"Avg Win:                {avg_win:+.4f}%")
    print(f"Avg Loss:               {avg_loss:.4f}%")
    print(f"Realized RR:            {rr_r:.2f}:1")
    print(f"Avg Setup RR:           {avg_rr_s:.2f}:1")
    print(f"Profit Factor:          {pf:.2f}")
    print(f"Total Return:           {tot_r:+.2f}%")
    print(f"Avg Bars Held:          {avg_bars:.0f}")

    for tname, tlist in sorted(types.items()):
        tw = [t for t in tlist if t["pnl_pct"] > 0]
        tp = sum(t["pnl_pct"] for t in tlist) / len(tlist)
        print(f"  {tname:30s}: {len(tlist):5d} trades, WR {len(tw)/len(tlist)*100:.1f}%, Avg {tp:+.4f}%")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rpt = os.path.join(REPORT_DIR, f"liq_sweep_1m_{ts}.md")

    lines = [
        f"# Liquidity Sweep Strategy — 1-Minute Backtest v3",
        f"",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Duration**: {elapsed:.0f}s",
        f"**Stocks**: {sym_with}/{len(INDIAN_STOCKS)}",
        f"**Params**: MaxStop={MAX_STOP_PCT}%, MinRR={MIN_RR}:1",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Trades | {total} |",
        f"| BUY / SELL | {len(buys)} / {len(sells)} |",
        f"| Win Rate | {wr:.1f}% |",
        f"| Avg PnL | {avg_pnl:+.4f}% |",
        f"| Avg Win | {avg_win:+.4f}% |",
        f"| Avg Loss | {avg_loss:.4f}% |",
        f"| Realized RR | {rr_r:.2f}:1 |",
        f"| Avg Setup RR | {avg_rr_s:.2f}:1 |",
        f"| Profit Factor | {pf:.2f} |",
        f"| Total Return | {tot_r:+.2f}% |",
        f"| Avg Bars Held | {avg_bars:.0f} |",
        f"| Targets Hit | {len(targets)}/{total} ({len(targets)/total*100:.1f}%) |",
        f"| Stops Hit | {len(stops)}/{total} ({len(stops)/total*100:.1f}%) |",
        f"| BE Triggered | {len(be)}/{total} ({len(be)/total*100:.1f}%) |",
        f"",
        f"## Per-Type Breakdown",
        f"",
        f"| Type | Trades | WR | Avg PnL | Avg RR |",
        f"|------|--------|----|---------|--------|",
    ]
    for tname, tlist in sorted(types.items()):
        tw = [t for t in tlist if t["pnl_pct"] > 0]
        tp = sum(t["pnl_pct"] for t in tlist) / len(tlist)
        trr = sum(t["rr_setup"] for t in tlist) / len(tlist)
        lines.append(f"| {tname:30s} | {len(tlist):5d} | {len(tw)/len(tlist)*100:.1f}% | {tp:+.4f}% | {trr:.2f}:1 |")

    ss = {}
    for t in all_trades:
        ss.setdefault(t["symbol"], []).append(t)
    lines.extend(["", "## Per-Stock Results", "", "| Symbol | Trades | WR | Avg PnL | Total Return |", "|--------|--------|----|---------|-------------|"])
    for sym in sorted(ss):
        s = ss[sym]
        w = [t for t in s if t["pnl_pct"] > 0]
        ap = sum(t["pnl_pct"] for t in s) / len(s)
        tr = sum(t["pnl_pct"] for t in s)
        lines.append(f"| {sym:10s} | {len(s):4d} | {len(w)/len(s)*100:5.1f}% | {ap:+7.4f}% | {tr:+9.2f}% |")

    st = sorted(all_trades, key=lambda x: x["pnl_pct"], reverse=True)
    lines.extend(["", "## Best Trades", "", "| # | Symbol | Type | Dir | Entry | Exit | PnL% | RR | Bars |", "|---|--------|------|-----|-------|------|------|-----|------|"])
    for k, t in enumerate(st[:10]):
        lines.append(f"| {k+1} | {t['symbol']:10s} | {t['type'][:22]:22s} | {t['direction']:4s} | {t['entry_price']:>7.2f} | {t['exit_price']:>7.2f} | {t['pnl_pct']:+5.2f}% | {t['rr_setup']:.1f}:1 | {t['bars_held']:3d} |")

    lines.extend(["", "## Worst Trades", "", "| # | Symbol | Type | Dir | Entry | Exit | PnL% | RR | Bars |", "|---|--------|------|-----|-------|------|------|-----|------|"])
    for k, t in enumerate(st[-10:]):
        lines.append(f"| {k+1} | {t['symbol']:10s} | {t['type'][:22]:22s} | {t['direction']:4s} | {t['entry_price']:>7.2f} | {t['exit_price']:>7.2f} | {t['pnl_pct']:+5.2f}% | {t['rr_setup']:.1f}:1 | {t['bars_held']:3d} |")

    with open(rpt, "w") as f:
        f.write("\n".join(lines))

    jf = os.path.join(REPORT_DIR, f"liq_sweep_1m_{ts}.json")
    with open(jf, "w") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "params": {"max_stop_pct": MAX_STOP_PCT, "min_rr": MIN_RR},
            "summary": {"total": total, "stocks": sym_with, "win_rate": round(wr, 1), "avg_pnl": round(avg_pnl, 4), "avg_win": round(avg_win, 4), "avg_loss": round(avg_loss, 4), "realized_rr": round(rr_r, 2), "avg_setup_rr": round(avg_rr_s, 2), "profit_factor": round(pf, 2), "total_return": round(tot_r, 2), "targets": len(targets), "stops": len(stops), "be": len(be)},
            "trades": st,
        }, f, indent=2)

    print(f"\nReport: {rpt}")
    print(f"JSON:   {jf}")

    print(f"\n{'=' * 50}")
    print("TELEGRAM REPORT")
    print(f"{'=' * 50}")
    print(f"📊 *Liquidity Sweep 1m Backtest v3*")
    print(f"Stocks: {sym_with}/{len(INDIAN_STOCKS)} | Trades: {total}")
    print(f"WR: {wr:.1f}% | Avg PnL: {avg_pnl:+.4f}% | Total: {tot_r:+.2f}%")
    print(f"Realized RR: {rr_r:.2f}:1 | PF: {pf:.2f}")
    print(f"Targets: {len(targets)}/{total} | BE: {len(be)}/{total}")
    for tname, tlist in sorted(types.items()):
        tw = [t for t in tlist if t["pnl_pct"] > 0]
        print(f"  {tname}: {len(tlist)} trades, {len(tw)/len(tlist)*100:.1f}% WR")


if __name__ == "__main__":
    run()
