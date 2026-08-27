"""Why did exactly these 13 stocks blast on 25 Aug?

Decomposition: a nearest-OTM call pays iff auction_move_pct >= dist_to_strike_pct.
For each of the 54 stocks with chain data, measure both legs plus candidate drivers:
  - dist_to_strike_pct at 15:10 and the strike-grid step (step/spot) - the mechanical leg
  - auction move, last-hour move, day move to 15:10 - the flow/momentum leg
  - chain state: pcr_oi, atm_oi_share, day change in call/put OI (short covering),
    late-day option volume share
  - NIFTY50 membership (approx 2025 list - index expiry was the same day)
Group medians blasters vs rest with Mann-Whitney p, and the same NIFTY test on all
210 stocks' auction spikes. Writes outputs/stocks/why_blast.csv + .json.
"""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
OUT = HERE / "dhan_export_stocks100"
ROOT = HERE.parent
DAY = dt.date(2026, 8, 25)
TFRZ = dt.time(15, 10)

NIFTY50 = {"ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
           "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
           "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK",
           "INDUSINDBK", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", "NESTLEIND",
           "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM",
           "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"}

blast = pd.read_csv(ROOT / "outputs" / "stocks" / "stocks100_1pm_blast.csv").set_index("symbol")
flt = pd.read_csv(OUT / "stocks100_spike_filter.csv").set_index("symbol")
scr = pd.read_csv(ROOT / "outputs" / "stocks" / "cas_stock_screen.csv")
scr = scr[scr.date == "2026-08-25"].set_index("symbol")

rows = []
for sym in blast.index:
    f = OUT / f"rolling_options_{sym}.csv"
    if not f.exists():
        continue
    d = pd.read_csv(f, parse_dates=["ts"])
    d = d[d.ts.dt.date == DAY]
    if d.empty:
        continue
    d = d.assign(time=d.ts.dt.time)
    pre = d[d.time <= TFRZ]
    spot1510 = float(pre.spot.iloc[-1])
    spot_open = float(d.spot.iloc[0])

    ce = d[d.side == "CE"]
    strikes = np.sort(ce.strike.unique())
    step = float(np.median(np.diff(strikes))) if len(strikes) > 1 else np.nan
    otm = strikes[strikes > spot1510]
    near = float(otm[0]) if len(otm) else np.nan
    dist_pct = (near / spot1510 - 1) * 100 if near == near else np.nan

    def oi_chg(side_df):
        g = side_df.sort_values("ts").groupby("strike").oi
        return float((g.last() - g.first()).sum())

    near_ce = ce[ce.strike == near].sort_values("ts")
    doi_near = float(near_ce.oi.iloc[-1] - near_ce.oi.iloc[0]) if len(near_ce) > 1 else np.nan
    late = d[d.time >= dt.time(14, 15)]
    vol_tot = d.volume.sum()

    b = blast.loc[sym]
    fl = flt.loc[sym] if sym in flt.index else None
    sc = scr.loc[sym] if sym in scr.index else None
    rows.append({
        "symbol": sym, "blasted": bool(b.opt_ret_1510_pct > 0),
        "auction_pct": b.stk_auction_pct, "dist_to_strike_pct": round(dist_pct, 2),
        "margin_pct": round(b.stk_auction_pct - dist_pct, 2),
        "strike_step_pct": round(step / spot1510 * 100, 2),
        "lasthr_pct": fl.aug25_CAS_lasthr_pct if fl is not None else np.nan,
        "day_to_1510_pct": round((spot1510 / spot_open - 1) * 100, 2),
        "typical_abs_eod_move": fl.typical_abs_eod_move if fl is not None else np.nan,
        "liq_rank": fl.liq_rank if fl is not None else np.nan,
        "pcr_oi": sc.pcr_oi if sc is not None else np.nan,
        "atm_oi_share": sc.atm_oi_share if sc is not None else np.nan,
        "oi_hhi": sc.oi_hhi if sc is not None else np.nan,
        "mp_dist_pct": sc.mp_dist_pct if sc is not None else np.nan,
        "call_oi_chg_day": oi_chg(ce), "put_oi_chg_day": oi_chg(d[d.side == "PE"]),
        "doi_near_strike": doi_near,
        "late_vol_share": round(float(late.volume.sum()) / vol_tot, 2) if vol_tot else np.nan,
        "nifty50": sym in NIFTY50,
    })

w = pd.DataFrame(rows).sort_values(["blasted", "margin_pct"], ascending=[False, False])
w.to_csv(ROOT / "outputs" / "stocks" / "why_blast.csv", index=False)

bl, nb = w[w.blasted], w[~w.blasted]
res = {"n_blast": len(bl), "n_rest": len(nb)}
print(f"blasters={len(bl)}  rest={len(nb)}\n")
print(f"{'feature':22s} {'blast_med':>10s} {'rest_med':>10s} {'MWU_p':>8s}")
for c in ("auction_pct", "dist_to_strike_pct", "strike_step_pct", "lasthr_pct", "day_to_1510_pct",
          "typical_abs_eod_move", "liq_rank", "pcr_oi", "atm_oi_share", "oi_hhi", "mp_dist_pct",
          "call_oi_chg_day", "put_oi_chg_day", "doi_near_strike", "late_vol_share"):
    a, b_ = bl[c].dropna(), nb[c].dropna()
    if len(a) < 3 or len(b_) < 3:
        continue
    p = float(stats.mannwhitneyu(a, b_).pvalue)
    res[c] = {"blast_med": round(float(a.median()), 3), "rest_med": round(float(b_.median()), 3), "p": round(p, 4)}
    print(f"{c:22s} {a.median():>10.3f} {b_.median():>10.3f} {p:>8.4f}")

# how far does pure mechanics go: classify blast by dist alone / by margin recipe
for thr in (0.4, 0.5, 0.6):
    pred = w.dist_to_strike_pct <= thr
    acc = (pred == w.blasted).mean()
    print(f"\npredict blast iff dist<= {thr}%: accuracy {acc:.0%}  (catches {int((pred & w.blasted).sum())}/13, false alarms {int((pred & ~w.blasted).sum())})")

# NIFTY membership vs auction spike, all 210
f210 = flt.reset_index()
f210["nifty50"] = f210.symbol.isin(NIFTY50)
a = f210[f210.nifty50].aug25_CAS_spike_pct.dropna()
b_ = f210[~f210.nifty50].aug25_CAS_spike_pct.dropna()
p = float(stats.mannwhitneyu(a, b_).pvalue)
res["nifty_spike_210"] = {"nifty_med": round(float(a.median()), 2), "other_med": round(float(b_.median()), 2),
                          "n": [int(len(a)), int(len(b_))], "p": round(p, 4)}
print(f"\nNIFTY50 members' auction spike (210 stocks): median {a.median():+.2f}% (n={len(a)}) vs others {b_.median():+.2f}% (n={len(b_)})  p={p:.4f}")

(ROOT / "outputs" / "stocks" / "why_blast.json").write_text(json.dumps(res, indent=2, default=float))
print("\nwrote outputs/stocks/why_blast.csv + .json")
