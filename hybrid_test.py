"""
Test hybrid strategy combinations on 1m cached data.
Combines PSAR and ZLEMA to find the best performing blend.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from tqdm import tqdm
from app.services.strategy_builder import LEADING_INDICATORS, CONFIRMATION_FILTERS, leading_psar, leading_zlema
from app.data.stocks import INDIAN_STOCKS
import pandas as pd

# Reuse loaders from timeframe_comparison
sys.path.insert(0, os.path.dirname(__file__))
from timeframe_comparison import load_cached_1m, MIN_BARS, bt, gen_report

# ─── Register new confirmation filters wrapping leading indicators ───

def confirm_psar(o, h, l, c, v):
    try:
        r = leading_psar(o, h, l, c, v)
        return {"confirmed": True, "direction": r["direction"], "value": r["value"]}
    except Exception:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}

def confirm_zlema(o, h, l, c, v):
    try:
        r = leading_zlema(o, h, l, c, v)
        d = r["direction"]
        return {"confirmed": d != "NEUTRAL", "direction": d, "value": r["value"]}
    except Exception:
        return {"confirmed": False, "direction": "NEUTRAL", "value": 0}

# Register them so bt() can find them
CONFIRMATION_FILTERS["PSAR as Conf"] = confirm_psar
CONFIRMATION_FILTERS["ZLEMA as Conf"] = confirm_zlema

# ─── Composite leading: both PSAR and ZLEMA must agree ───

def leading_composite(o, h, l, c, v):
    try:
        r1 = leading_psar(o, h, l, c, v)
        r2 = leading_zlema(o, h, l, c, v)
        if r1["direction"] == r2["direction"]:
            return {"direction": r1["direction"], "value": (r1["value"] + r2["value"]) / 2}
        return {"direction": "NEUTRAL", "value": 0}
    except Exception:
        return {"direction": "NEUTRAL", "value": 0}

LEADING_INDICATORS["Composite PSAR+ZLEMA"] = leading_composite

# ─── Hybrid presets to test ───

LIGHT_CONFS = ["EMA 20", "MACD", "RSI", "Volume", "Price Action"]
HEAVY_CONFS = ["EMA 20", "EMA 50", "MACD", "RSI", "Volume", "Price Action", "Market Trend", "Liquidity Sweep", "Market Structure"]

HYBRID_PRESETS = {
    "PSAR-Light":         ("PSAR", LIGHT_CONFS, 2),
    "ZLEMA-Heavy":        ("ZLEMA", HEAVY_CONFS, 3),
    "PSAR+ZLEMA-Conf":    ("PSAR", LIGHT_CONFS + ["ZLEMA as Conf"], 2),
    "ZLEMA+PSAR-Conf":    ("ZLEMA", LIGHT_CONFS + ["PSAR as Conf"], 2),
    "Composite Both":     ("Composite PSAR+ZLEMA", LIGHT_CONFS, 2),
}

def run_batch(data_dict, preset_name, leading, confs, threshold, label):
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
        for fut in tqdm(as_completed(fut_map), total=len(fut_map), desc=label, unit="sym"):
            try:
                r = fut.result(timeout=60)
                if r:
                    r["symbol"] = fut_map[fut]
                    results.append(r)
            except Exception:
                pass
    return results

def gen_hybrid_report(all_res):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    f = os.path.join(os.path.dirname(__file__), "data", f"hybrid_{ts}.md")
    lines = []
    lines.append("# Hybrid Strategy Comparison (1m Intraday)")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Total stocks: {len(INDIAN_STOCKS)}")
    lines.append("")

    for pname in HYBRID_PRESETS:
        d = all_res.get(pname, [])
        if not d:
            continue
        tt = sum(r["trades"] for r in d)
        aw = sum(r["win_rate"] for r in d) / len(d)
        tr = sum(r["total_return"] for r in d)
        ap = sum(r["profit_factor"] for r in d) / len(d)
        a_win = sum(r["max_win"] for r in d) / len(d)
        a_loss = sum(r["max_loss"] for r in d) / len(d)
        rr = a_win / a_loss if a_loss > 0 else 0

        lines.append(f"## {pname}")
        lines.append(f"- Stocks: {len(d)} | Trades: {tt}")
        lines.append(f"- Avg WR: {aw:.1f}% | Total Return: {tr:+.2f}%")
        lines.append(f"- Avg PF: {ap:.2f} | Avg RR: {rr:.2f}:1")
        lines.append("")

        lines.append("| Stock | Trades | WR | Return | PF |")
        lines.append("|-------|--------|----|--------|----|")
        sr = sorted(d, key=lambda x: x["total_return"], reverse=True)
        for r in sr:
            lines.append(f"| {r['symbol']:15s} | {r['trades']:3d} | {r['win_rate']:5.1f}% | {r['total_return']:+7.2f}% | {r['profit_factor']:5.2f} |")
        lines.append("")

    # Summary table
    lines.append("## Side-by-Side Summary")
    lines.append("")
    lines.append("| Preset | Stocks | Trades | WR | Return | PF | RR |")
    lines.append("|--------|--------|--------|----|--------|----|----|")
    for pname in HYBRID_PRESETS:
        d = all_res.get(pname, [])
        if d:
            tt = sum(r["trades"] for r in d)
            aw = sum(r["win_rate"] for r in d) / len(d)
            tr = sum(r["total_return"] for r in d)
            ap = sum(r["profit_factor"] for r in d) / len(d)
            a_win = sum(r["max_win"] for r in d) / len(d)
            a_loss = sum(r["max_loss"] for r in d) / len(d)
            rr = a_win / a_loss if a_loss > 0 else 0
            lines.append(f"| {pname:22s} | {len(d):3d} | {tt:4d} | {aw:5.1f}% | {tr:+8.2f}% | {ap:5.2f} | {rr:5.2f} |")

    # Ranking
    lines.append("")
    lines.append("## Ranking by WR")
    ranked = []
    for pname in HYBRID_PRESETS:
        d = all_res.get(pname, [])
        if d:
            aw = sum(r["win_rate"] for r in d) / len(d)
            tt = sum(r["trades"] for r in d)
            ranked.append((aw, tt, pname))
    ranked.sort(reverse=True)
    for i, (aw, tt, pn) in enumerate(ranked, 1):
        lines.append(f"  {i}. **{pn}** — {aw:.1f}% WR ({tt} trades)")

    lines.append("")
    lines.append("## Ranking by Total Return")
    ranked_r = []
    for pname in HYBRID_PRESETS:
        d = all_res.get(pname, [])
        if d:
            tr = sum(r["total_return"] for r in d)
            ranked_r.append((tr, pname))
    ranked_r.sort(reverse=True)
    for i, (tr, pn) in enumerate(ranked_r, 1):
        lines.append(f"  {i}. **{pn}** — {tr:+.2f}% return")

    with open(f, "w") as fp:
        fp.write("\n".join(lines))
    print(f"\nReport: {f}")
    return f

if __name__ == "__main__":
    t0 = time.time()
    d1m = load_cached_1m()
    print(f"1m: {len(d1m)} stocks loaded")

    all_res = {}
    for pname, (ldr, confs, thr) in HYBRID_PRESETS.items():
        print(f"\nRunning {pname}...")
        all_res[pname] = run_batch(d1m, pname, ldr, confs, thr, pname)

    gen_hybrid_report(all_res)
    print(f"\nTotal: {time.time()-t0:.0f}s")
