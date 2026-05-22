"""
Compare ZLEMA-Optimized vs PSAR-Default on 1m vs 1d across all stocks.
Uses cached data directly (no redundant yfinance calls).
"""
import sys, os, pickle, time, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from tqdm import tqdm
from app.services.strategy_builder import LEADING_INDICATORS, CONFIRMATION_FILTERS
from app.data.stocks import INDIAN_STOCKS
import yfinance as yf
import pandas as pd

CACHE_1M = os.path.join(os.path.dirname(__file__), "data", "ohlc_1m_cache")
CACHE_1D = os.path.join(os.path.dirname(__file__), "data", "ohlc_180_cache")
os.makedirs(CACHE_1M, exist_ok=True)

MIN_BARS = 20

PRESETS = {
    "ZLEMA-Optimized": ("ZLEMA", ["EMA 20", "MACD", "RSI", "Volume", "Price Action"], 2),
    "PSAR-Default": ("PSAR", ["EMA 20", "EMA 50", "MACD", "RSI", "Volume", "Price Action", "Market Trend", "Liquidity Sweep", "Market Structure"], 3),
}

def bt(opens, highs, lows, closes, volumes, leading_name, conf_names, threshold, buy_only=True, sl_pct=5.0):
    """Direct backtest on cached data - no yfinance calls."""
    leading_func = LEADING_INDICATORS.get(leading_name)
    if not leading_func:
        return None
    confs = [(n, CONFIRMATION_FILTERS[n]) for n in conf_names if n in CONFIRMATION_FILTERS]
    trades = []; pos = False; ep = 0; esig = ""; ehigh = 0; elow = 0

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
            if esig == "BUY": ehigh = max(ehigh, price)
            else: elow = min(elow, price)
            sl = False
            if sl_pct > 0:
                if esig == "BUY" and price <= ehigh * (1 - sl_pct / 100): sl = True
                elif esig == "SELL" and price >= elow * (1 + sl_pct / 100): sl = True
            exit_sig = False
            if (esig == "BUY" and signal == "SELL") or (esig == "SELL" and signal == "BUY"):
                exit_sig = True
            if sl: exit_sig = True
            if exit_sig:
                pnl = ((price - ep) / ep) * 100
                if esig == "SELL": pnl = -pnl
                trades.append(round(pnl, 2))
                pos = False

    if pos:
        price = closes[-1]
        pnl = ((price - ep) / ep) * 100
        if esig == "SELL": pnl = -pnl
        trades.append(round(pnl, 2))

    if not trades: return None
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    total = len(trades); nw = len(wins); nl = len(losses)
    if total == 0: return None
    wr = nw / total * 100
    tr = sum(trades)
    gp = sum(wins); gl = abs(sum(losses))
    pf = gp / gl if gl > 0 else (99.9 if gp > 0 else 0)
    return {
        "trades": total, "win_rate": round(wr, 1),
        "total_return": round(tr, 2), "profit_factor": round(min(pf, 99.9), 2),
        "max_win": round(max(wins) if wins else 0, 2),
        "max_loss": round(max(abs(l) for l in losses) if losses else 0, 2),
    }

def _dedup_cols(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]
    return df

def load_cached_1d():
    data = {}
    for sym in INDIAN_STOCKS:
        path = os.path.join(CACHE_1D, f"{sym}.pkl")
        if os.path.exists(path):
            with open(path, "rb") as f:
                df = pickle.load(f)
            df = _dedup_cols(df)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df) >= MIN_BARS:
                data[sym] = (
                    [float(r["Open"]) for _, r in df.iterrows()],
                    [float(r["High"]) for _, r in df.iterrows()],
                    [float(r["Low"]) for _, r in df.iterrows()],
                    [float(r["Close"]) for _, r in df.iterrows()],
                    [int(r["Volume"]) for _, r in df.iterrows()],
                )
    return data

def download_1m():
    to_dl = [s for s in INDIAN_STOCKS if not os.path.exists(os.path.join(CACHE_1M, f"{s}.pkl"))]
    if not to_dl:
        return
    print(f"Downloading 1m data for {len(to_dl)} stocks...")
    def dl(sym):
        try:
            df = yf.download(f"{sym.upper()}.NS", period="7d", interval="1m", progress=False, auto_adjust=True)
            if df.empty or len(df) < MIN_BARS: return None
            df = _dedup_cols(df)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df) >= MIN_BARS:
                with open(os.path.join(CACHE_1M, f"{sym}.pkl"), "wb") as f:
                    pickle.dump(df, f)
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(tqdm(pool.map(dl, to_dl), total=len(to_dl), desc="DL 1m"))

def load_cached_1m():
    data = {}
    for sym in INDIAN_STOCKS:
        path = os.path.join(CACHE_1M, f"{sym}.pkl")
        if os.path.exists(path):
            with open(path, "rb") as f:
                df = pickle.load(f)
            df = _dedup_cols(df)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df) >= MIN_BARS:
                data[sym] = (
                    [float(r["Open"]) for _, r in df.iterrows()],
                    [float(r["High"]) for _, r in df.iterrows()],
                    [float(r["Low"]) for _, r in df.iterrows()],
                    [float(r["Close"]) for _, r in df.iterrows()],
                    [int(r["Volume"]) for _, r in df.iterrows()],
                )
    return data

def run_batch(data_dict, preset_name, leading, confs, threshold, interval_label):
    if not data_dict:
        return []
    results = []
    keys = list(data_dict.keys())
    with ThreadPoolExecutor(max_workers=12) as pool:
        fut_map = {}
        for sym in keys:
            o, h, l, c, v = data_dict[sym]
            fut = pool.submit(bt, o, h, l, c, v, leading, confs, threshold)
            fut_map[fut] = sym
        for fut in tqdm(as_completed(fut_map), total=len(fut_map), desc=f"{preset_name} {interval_label}", unit="sym"):
            try:
                r = fut.result(timeout=60)
                if r:
                    r["symbol"] = fut_map[fut]
                    results.append(r)
            except Exception:
                pass
    return results

def gen_report(all_res):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    f = os.path.join(os.path.dirname(__file__), "data", f"tf_compare_{ts}.md")
    lines = []

    lines.append("# Timeframe Comparison: 1m vs 1d")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Total stocks: {len(INDIAN_STOCKS)}")
    lines.append("")

    for preset_name in ["ZLEMA-Optimized", "PSAR-Default"]:
        for tf in ["1m", "1d"]:
            key = f"{preset_name}_{tf}"
            d = all_res.get(key, [])
            if not d: continue
            tt = sum(r["trades"] for r in d)
            aw = sum(r["win_rate"] for r in d) / len(d)
            tr = sum(r["total_return"] for r in d)
            ap = sum(r["profit_factor"] for r in d) / len(d)
            a_win = sum(r["max_win"] for r in d) / len(d)
            a_loss = sum(r["max_loss"] for r in d) / len(d)
            rr = a_win / a_loss if a_loss > 0 else 0

            lines.append(f"## {preset_name} — {tf}")
            lines.append(f"- Stocks: {len(d)} | Trades: {tt}")
            lines.append(f"- Avg WR: {aw:.1f}% | Total Return: {tr:+.2f}%")
            lines.append(f"- Avg PF: {ap:.2f} | Avg RR: {rr:.2f}:1")
            lines.append("")

            # Per-stock table
            lines.append("| Stock | Trades | WR | Return | PF |")
            lines.append("|-------|--------|----|--------|----|")
            sr = sorted(d, key=lambda x: x["total_return"], reverse=True)
            for r in sr:
                lines.append(f"| {r['symbol']:15s} | {r['trades']:3d} | {r['win_rate']:5.1f}% | {r['total_return']:+7.2f}% | {r['profit_factor']:5.2f} |")
            lines.append("")

    # Side-by-side
    lines.append("## Side-by-Side Summary")
    lines.append("")
    lines.append("| Preset | TF | Stocks | Trades | WR | Return | PF | RR |")
    lines.append("|--------|----|--------|--------|----|--------|----|----|")
    for preset_name in ["ZLEMA-Optimized", "PSAR-Default"]:
        for tf in ["1m", "1d"]:
            d = all_res.get(f"{preset_name}_{tf}", [])
            if d:
                tt = sum(r["trades"] for r in d)
                aw = sum(r["win_rate"] for r in d) / len(d)
                tr = sum(r["total_return"] for r in d)
                ap = sum(r["profit_factor"] for r in d) / len(d)
                awin = sum(r["max_win"] for r in d) / len(d)
                al = sum(r["max_loss"] for r in d) / len(d)
                rr = awin / al if al > 0 else 0
                lines.append(f"| {preset_name:16s} | {tf:3s} | {len(d):3d} | {tt:4d} | {aw:5.1f}% | {tr:+8.2f}% | {ap:5.2f} | {rr:5.2f} |")

    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    # Find best
    best_wr = 0; best_label = ""
    for preset_name in ["ZLEMA-Optimized", "PSAR-Default"]:
        for tf in ["1m", "1d"]:
            d = all_res.get(f"{preset_name}_{tf}", [])
            if d:
                aw = sum(r["win_rate"] for r in d) / len(d)
                if aw > best_wr:
                    best_wr = aw
                    best_label = f"{preset_name} on {tf}"
    lines.append(f"**Best combo**: {best_label} ({best_wr:.1f}% avg WR)")
    lines.append("")

    with open(f, "w") as fp:
        fp.write("\n".join(lines))

    print(f"\nReport: {f}")
    return f

if __name__ == "__main__":
    t0 = time.time()

    # Download + load 1m
    download_1m()
    d1m = load_cached_1m()
    print(f"1m: {len(d1m)} stocks loaded")

    # Load 1d (already cached)
    d1d = load_cached_1d()
    print(f"1d: {len(d1d)} stocks loaded")

    all_res = {}
    for pname, (ldr, confs, thr) in PRESETS.items():
        all_res[f"{pname}_1m"] = run_batch(d1m, pname, ldr, confs, thr, "1m")
        all_res[f"{pname}_1d"] = run_batch(d1d, pname, ldr, confs, thr, "1d")

    gen_report(all_res)
    print(f"\nTotal: {time.time()-t0:.0f}s")
