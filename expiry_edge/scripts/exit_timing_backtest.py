"""WHEN to close the 15:05 nearest-OTM strangle: mark-to-market at every 5-min bar from 15:05 to
15:35 on every CAS expiry with option bars, versus holding to the auction settlement (intrinsic).

Index universe: the 15 index expiries (Aug backtest set + 1/3/10 Sep live days).
Stock universe : the 25 Aug 2026 monthly stock expiry (every symbol with 5-min option bars in the repo).

Per row: legs at the 15:05 bar (CE = lowest strike > spot, PE = highest strike < spot), entry = sum of
15:05 closes, value at 15:10/15:15/15:20/15:25/15:30/15:35 bar closes (sum of the two legs' closes),
settlement value = intrinsic at the last bar's spot, best close-exit (max over bars >= 15:15) and its
time, best bar-high exit (upper bound, where high is available), and the strangle rule's verdict
(prem <= 0.25% of spot, easier breakeven <= 0.35%). Bars are labelled by START time: the 15:15 bar
covers 15:15:00-15:19:59 (the first post-freeze bar for stocks; index options keep trading).
Writes outputs/model/exit_timing_index.csv, exit_timing_stocks.csv and prints the summary tables.
"""
import datetime as dt
import glob
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXP = HERE / "dhan_export"
LOTS = {"NIFTY": 65, "SENSEX": 20, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120, "BANKEX": 30}
IDX_DAYS = [("NIFTY", "2026-08-04"), ("SENSEX", "2026-08-06"), ("NIFTY", "2026-08-11"), ("SENSEX", "2026-08-13"),
            ("NIFTY", "2026-08-18"), ("SENSEX", "2026-08-20"), ("BANKNIFTY", "2026-08-25"), ("FINNIFTY", "2026-08-25"),
            ("MIDCPNIFTY", "2026-08-25"), ("NIFTY", "2026-08-25"), ("BANKEX", "2026-08-27"), ("SENSEX", "2026-08-27"),
            ("NIFTY", "2026-09-01"), ("SENSEX", "2026-09-10")]
# 3 Sep SENSEX is EXCLUDED: rolling_options_SENSEX_sep3.csv (pulled 10 Sep, expiryCode 2) carries a later
# contract's prices (15:05 closes ~4x the live chain of that day); only its spot column is valid.
EXITS = [dt.time(15, 10), dt.time(15, 15), dt.time(15, 20), dt.time(15, 25), dt.time(15, 30), dt.time(15, 35)]
T_ENTRY = dt.time(15, 5)
BUDGET = 20000


def load_index(idx):
    files = {"NIFTY": ["rolling_options_NIFTY.csv", "rolling_options_NIFTY_sep1_3.csv"],
             "SENSEX": ["rolling_options_SENSEX.csv", "rolling_options_SENSEX_aug27.csv", "rolling_options_SENSEX_sep1_3.csv",
                        "rolling_options_SENSEX_sep3.csv", "rolling_options_SENSEX_sep10.csv"]}.get(
        idx, [f"rolling_options_{idx}_monthly.csv", f"rolling_options_{idx}.csv"])
    parts = [pd.read_csv(EXP / f, parse_dates=["ts"]) for f in files if (EXP / f).exists()]
    d = pd.concat(parts, ignore_index=True)
    for c in ("high", "low"):
        if c not in d.columns:
            d[c] = np.nan
    return d[["ts", "side", "strike", "spot", "close", "high", "low"]].drop_duplicates(["ts", "side", "strike"])


def bar(day, t):
    b = day[day.ts.dt.time == t]
    return b


def evaluate(day, name, date, lot=None):
    day = day.sort_values("ts")
    pre = day[day.ts.dt.time <= T_ENTRY]
    if pre.empty:
        return None
    b0 = pre[pre.ts == pre.ts.max()]
    spot = float(b0.spot.iloc[0])
    ks = sorted(day.strike.unique())
    try:
        ce_k = min(k for k in ks if k > spot); pe_k = max(k for k in ks if k < spot)
    except ValueError:
        return None
    ce0 = b0[(b0.side == "CE") & (b0.strike == ce_k)]; pe0 = b0[(b0.side == "PE") & (b0.strike == pe_k)]
    if ce0.empty or pe0.empty:
        return None
    ce_p, pe_p = float(ce0.close.iloc[0]), float(pe0.close.iloc[0])
    entry = ce_p + pe_p
    settle = float(day.spot.iloc[-1])
    settle_val = max(0.0, settle - ce_k) + max(0.0, pe_k - settle)
    r = {"name": name, "date": date, "spot_1505": round(spot, 2), "ce_k": ce_k, "pe_k": pe_k, "ce_p": ce_p, "pe_p": pe_p,
         "entry": round(entry, 2), "prem_pct": round(entry / spot * 100, 3),
         "need_pct": round(min((ce_k - spot) + entry, (spot - pe_k) + entry) / spot * 100, 3),
         "settle": settle, "settle_val": round(settle_val, 2), "auction_pct": None}
    frz = day[day.ts.dt.time <= dt.time(15, 15)]
    r["auction_pct"] = round((settle / float(frz.spot.iloc[-1]) - 1) * 100, 3) if len(frz) else None
    r["rule"] = "BUY" if (r["prem_pct"] <= 0.25 and r["need_pct"] <= 0.35) else ("SKIP-EXP" if r["prem_pct"] > 0.25 else "SKIP-GEO")
    best_val, best_t, best_hi = -1, None, np.nan
    for t in EXITS:
        b = bar(day, t)
        ce = b[(b.side == "CE") & (b.strike == ce_k)]; pe = b[(b.side == "PE") & (b.strike == pe_k)]
        col = f"v_{t.strftime('%H%M')}"
        if (ce.empty or pe.empty) and t >= dt.time(15, 25) and not b.empty:
            # leg fell out of the ATM+-3 export window after a big auction (27 Aug): value it at intrinsic on that bar's spot
            sp = float(b.spot.iloc[0])
            cv = float(ce.close.iloc[0]) if not ce.empty else max(0.0, sp - ce_k)
            pv = float(pe.close.iloc[0]) if not pe.empty else max(0.0, pe_k - sp)
            r[col] = round(cv + pv, 2); r["filled_intrinsic"] = True
            if t >= dt.time(15, 15) and r[col] > best_val:
                best_val, best_t = r[col], t.strftime("%H:%M")
            continue
        if ce.empty or pe.empty:
            r[col] = np.nan; continue
        v = float(ce.close.iloc[0]) + float(pe.close.iloc[0])
        r[col] = round(v, 2)
        if t >= dt.time(15, 15) and v > best_val:
            best_val, best_t = v, t.strftime("%H:%M")
        hi = float(ce.high.iloc[0]) + float(pe.high.iloc[0]) if (pd.notna(ce.high.iloc[0]) and pd.notna(pe.high.iloc[0])) else np.nan
        if t >= dt.time(15, 15) and pd.notna(hi) and (np.isnan(best_hi) or hi > best_hi):
            best_hi = hi
    b25 = bar(day, dt.time(15, 25)); b20 = bar(day, dt.time(15, 20))
    def ohlc(bb, side, k):
        x = bb[(bb.side == side) & (bb.strike == k)]
        return x.iloc[0] if len(x) and pd.notna(x.iloc[0].get("high", np.nan)) else None
    c25, p25, c20, p20 = ohlc(b25, "CE", ce_k), ohlc(b25, "PE", pe_k), ohlc(b20, "CE", ce_k), ohlc(b20, "PE", pe_k)
    if c25 is not None and p25 is not None:
        r["v_1525_open"] = round(float(c25.open) + float(p25.open), 2)
        r["v_1525_typical"] = round(sum((float(x.open) + float(x.high) + float(x.low) + float(x.close)) / 4 for x in (c25, p25)), 2)
    else:
        r["v_1525_open"] = np.nan; r["v_1525_typical"] = np.nan
    r["v_1520_open"] = round(float(c20.open) + float(p20.open), 2) if (c20 is not None and p20 is not None) else np.nan
    r["best_close_val"] = round(best_val, 2) if best_val >= 0 else np.nan
    r["best_close_time"] = best_t
    r["best_high_val"] = round(best_hi, 2) if pd.notna(best_hi) else np.nan
    # ratios vs entry
    for c in [f"v_{t.strftime('%H%M')}" for t in EXITS] + ["settle_val", "best_close_val", "best_high_val", "v_1520_open", "v_1525_open", "v_1525_typical"]:
        r[c + "_x"] = round(r[c] / entry, 3) if pd.notna(r.get(c)) and entry > 0 else np.nan
    if lot:
        lots = int(BUDGET // (entry * lot))
        r["lots"] = lots
        for c in ("v_1510", "v_1515", "v_1520", "v_1525", "settle_val", "best_close_val", "v_1525_open", "v_1525_typical"):
            r["pnl_" + c] = round(lots * (r[c] - entry) * lot) if pd.notna(r.get(c)) else np.nan
    return r


def summarize(t, label):
    cols = [f"v_{x.strftime('%H%M')}" for x in EXITS] + ["v_1520_open", "v_1525_open", "v_1525_typical", "settle_val", "best_close_val", "best_high_val"]
    print(f"\n=== {label}: value / entry at each exit (n={len(t)}) ===")
    print(f"{'exit':16s} {'n':>3s} {'median x':>9s} {'mean x':>7s} {'win%':>5s}   (win = value > entry)")
    for c in cols:
        x = t[c + "_x"].dropna()
        if len(x) == 0:
            continue
        print(f"{c:16s} {len(x):3d} {x.median():9.2f} {x.mean():7.2f} {(x > 1).mean() * 100:5.0f}")
    bt = t.best_close_time.value_counts()
    print("best close-exit bar (>=15:15): " + ", ".join(f"{k} x{v}" for k, v in bt.items()))
    if "pnl_settle_val" in t.columns:
        for sub, lab in ((t, "all days"), (t[t.rule == "BUY"], "rule-BUY days only"), (t[t.name != "BANKEX"], "ex-BANKEX 27 Aug"),
                         (t[t.filled_intrinsic != True] if "filled_intrinsic" in t.columns else t, "days with complete bars")):
            if len(sub) == 0:
                continue
            print(f"Rs at Rs{BUDGET:,} sizing, {lab} (n={len(sub)}): 15:10 {sub.pnl_v_1510.sum():+,.0f} | 15:15 {sub.pnl_v_1515.sum():+,.0f} | 15:20 {sub.pnl_v_1520.sum():+,.0f} | "
                  f"15:25 {sub.pnl_v_1525.sum():+,.0f} | settlement {sub.pnl_settle_val.sum():+,.0f} | best-close (hindsight) {sub.pnl_best_close_val.sum():+,.0f}"
                  + (f" | 15:25-open {sub.pnl_v_1525_open.sum():+,.0f} / 15:25-typical {sub.pnl_v_1525_typical.sum():+,.0f} on the {int(sub.pnl_v_1525_open.notna().sum())} OHLC days" if sub.pnl_v_1525_open.notna().any() else ""))


if __name__ == "__main__":
    rows = []
    for idx, iso in IDX_DAYS:
        d = load_index(idx)
        day = d[d.ts.dt.date == dt.date.fromisoformat(iso)]
        r = evaluate(day, idx, iso, LOTS[idx])
        if r:
            rows.append(r)
        else:
            print(f"{idx} {iso}: unusable")
    ti = pd.DataFrame(rows)
    ti.to_csv(ROOT / "outputs" / "model" / "exit_timing_index.csv", index=False)
    show = ["name", "date", "rule", "prem_pct", "auction_pct", "entry", "v_1510", "v_1515", "v_1520", "v_1525", "v_1530", "v_1535",
            "settle_val", "best_close_val", "best_close_time", "best_high_val"]
    pd.set_option("display.width", 250)
    print(ti[show].to_string(index=False))
    summarize(ti, "INDEX (15 CAS expiries)")

    srows = []
    files = sorted(glob.glob(str(HERE / "dhan_export_stocks100" / "rolling_options_*.csv"))) + \
            [str(EXP / f"rolling_options_{s}.csv") for s in ("TATASTEEL", "YESBANK", "SBIN", "PNB", "IDEA", "IDFCFIRSTB", "RBLBANK", "BANDHANBNK")]
    seen = set()
    for f in files:
        sym = Path(f).stem.replace("rolling_options_", "")
        if sym in seen or not Path(f).exists():
            continue
        seen.add(sym)
        try:
            d = pd.read_csv(f)
        except Exception:
            continue
        if d.empty or "ts" not in d.columns:
            continue
        d["ts"] = pd.to_datetime(d.ts, errors="coerce")
        d = d.dropna(subset=["ts"])
        for c in ("high", "low"):
            if c not in d.columns:
                d[c] = np.nan
        day = d[d.ts.dt.date == dt.date(2026, 8, 25)]
        if day.empty:
            continue
        r = evaluate(day[["ts", "side", "strike", "spot", "close", "high", "low"]].drop_duplicates(["ts", "side", "strike"]), sym, "2026-08-25")
        if r:
            srows.append(r)
    ts_ = pd.DataFrame(srows)
    ts_.to_csv(ROOT / "outputs" / "model" / "exit_timing_stocks.csv", index=False)
    summarize(ts_, "STOCKS (25 Aug monthly expiry)")
    print("\nstocks: top 12 by best close-exit multiple")
    print(ts_.sort_values("best_close_val_x", ascending=False)[["name", "prem_pct", "auction_pct", "entry", "v_1515", "v_1520", "v_1525", "settle_val",
                                                                 "best_close_val", "best_close_time", "best_close_val_x", "settle_val_x"]].head(12).to_string(index=False))
    print("\nwrote outputs/model/exit_timing_index.csv, exit_timing_stocks.csv")
