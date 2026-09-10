"""Why did the directional call miss? Post-mortem of every directional signal we use, on every index
CAS expiry with 5-min option bars (Aug 2026 backtest set + the live days 1 Sep, 3 Sep, 10 Sep).

Per expiry day, from the ATM+-3 rolling-option bars (the same data v2 uses):
  mp_13 / mp_1430 / mp_1505   max-pain over the strikes present at that bar (OI-weighted intrinsic)
  pain_margin_1505            (2nd-best pain - best pain) / best pain: how decisive the pin is
  spot_13 / 1445 / 1505 / freeze (last bar <= 15:15) / settle (last bar) ; auction = settle - freeze
Signals scored against sign(auction):
  MIG_late   sign(mp_1505 - mp_13)            (v2 'pinmig' vote; the STRONG trigger)
  MIG_early  sign(mp_1430 - mp_13)            (was the migration already visible at 14:30?)
  MIG_persist MIG_early == MIG_late != 0      (visible at 14:30 AND still there at 15:05)
  PINPULL    sign(mp_1505 - spot_1505) if |gap| > 0.6 step else 0   (v1)
  TREND_day  sign(spot_1505 - spot_13)         (v2 'day' vote)
  TREND_20   sign(spot_1505 - spot_1445)       (v2 'last20' vote)
  V2         MIG_late if != 0 else PINPULL     (what expiry_pick_v2 prints: STRONG / OK)
Writes outputs/model/direction_postmortem.csv and prints hit tables.
"""
import datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXP = HERE / "dhan_export"
STEP = {"NIFTY": 50, "SENSEX": 100, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25, "BANKEX": 100}
DAYS = [("NIFTY", "2026-08-04"), ("SENSEX", "2026-08-06"), ("NIFTY", "2026-08-11"), ("SENSEX", "2026-08-13"),
        ("NIFTY", "2026-08-18"), ("SENSEX", "2026-08-20"), ("BANKNIFTY", "2026-08-25"), ("FINNIFTY", "2026-08-25"),
        ("MIDCPNIFTY", "2026-08-25"), ("NIFTY", "2026-08-25"), ("BANKEX", "2026-08-27"), ("SENSEX", "2026-08-27"),
        ("NIFTY", "2026-09-01"), ("SENSEX", "2026-09-03"), ("SENSEX", "2026-09-10")]
LIVE = {"2026-09-01", "2026-09-03", "2026-09-10"}


def load(idx):
    files = {"NIFTY": ["rolling_options_NIFTY.csv", "rolling_options_NIFTY_sep1_3.csv"],
             "SENSEX": ["rolling_options_SENSEX.csv", "rolling_options_SENSEX_aug27.csv", "rolling_options_SENSEX_sep1_3.csv",
                        "rolling_options_SENSEX_sep3.csv", "rolling_options_SENSEX_sep10.csv"]}.get(idx, [f"rolling_options_{idx}_monthly.csv", f"rolling_options_{idx}.csv"])
    parts = [pd.read_csv(EXP / f, parse_dates=["ts"]) for f in files if (EXP / f).exists()]
    d = pd.concat(parts, ignore_index=True)
    return d[["ts", "side", "strike", "spot", "close", "oi"]].drop_duplicates(["ts", "side", "strike"])


def max_pain(bar):
    """bar: rows of one timestamp (both sides). Returns (best strike, margin)."""
    ks = sorted(bar.strike.unique())
    ce = bar[bar.side == "CE"].set_index("strike").oi.reindex(ks).fillna(0)
    pe = bar[bar.side == "PE"].set_index("strike").oi.reindex(ks).fillna(0)
    pains = []
    for s in ks:
        pains.append(sum(ce[k] * (s - k) for k in ks if s > k) + sum(pe[k] * (k - s) for k in ks if s < k))
    order = np.argsort(pains)
    best, second = pains[order[0]], pains[order[1]] if len(pains) > 1 else np.nan
    return ks[order[0]], (second - best) / best if best else np.nan


def at(day, t):
    b = day[day.ts.dt.time <= t]
    return b[b.ts == b.ts.max()] if len(b) else pd.DataFrame()


def sgn(x, tol=0.0):
    return 0 if abs(x) <= tol else (1 if x > 0 else -1)


rows = []
for idx, iso in DAYS:
    d = load(idx)
    day = d[d.ts.dt.date == dt.date.fromisoformat(iso)]
    if day.empty:
        print(f"{idx} {iso}: no bars"); continue
    b13, b1430, b1445, b1505, bfrz = (at(day, dt.time(h, m)) for h, m in ((13, 0), (14, 30), (14, 45), (15, 5), (15, 15)))
    b1405 = at(day, dt.time(14, 5))
    if b13.empty or b1505.empty:
        print(f"{idx} {iso}: missing 13:00 or 15:05 bar"); continue
    mp13, _ = max_pain(b13); mp1430, _ = max_pain(b1430); mp1505, margin = max_pain(b1505)
    s13, s1445, s1505 = float(b13.spot.iloc[0]), float(b1445.spot.iloc[0]), float(b1505.spot.iloc[0])
    freeze = float(bfrz.spot.iloc[0]); settle = float(day.sort_values("ts").spot.iloc[-1])
    auction = settle - freeze
    step = STEP[idx]
    r = {"index": idx, "date": iso, "live": iso in LIVE, "mp_13": mp13, "mp_1430": mp1430, "mp_1505": mp1505,
         "pain_margin_1505": round(margin, 3), "spot_13": s13, "spot_1445": s1445, "spot_1505": s1505,
         "freeze": freeze, "settle": settle, "auction": round(auction, 1), "auction_pct": round(auction / freeze * 100, 3),
         "MIG_late": sgn(mp1505 - mp13), "MIG_early": sgn(mp1430 - mp13),
         "PINPULL": sgn(mp1505 - s1505, 0.6 * step), "pin_gap": round(mp1505 - s1505, 1),
         "TREND_day": sgn(s1505 - s13), "TREND_20": sgn(s1505 - s1445), "AUCTION": sgn(auction)}
    # writer-pressing signal: which side's OI grew more (and premium fell more) 14:05 -> 15:05 on the 2 nearest OTM strikes
    def side_stats(side):
        ks_all = sorted(day.strike.unique())
        near = [k for k in ks_all if k > s1505][:2] if side == "CE" else [k for k in ks_all if k < s1505][-2:]
        o0 = b1405[(b1405.side == side) & b1405.strike.isin(near)]; o1 = b1505[(b1505.side == side) & b1505.strike.isin(near)]
        if o0.empty or o1.empty:
            return np.nan, np.nan
        doi = (o1.oi.sum() / max(o0.oi.sum(), 1) - 1) * 100
        dp = (o1.close.sum() / max(o0.close.sum(), 1e-9) - 1) * 100
        return doi, dp
    ce_doi, ce_dp = side_stats("CE"); pe_doi, pe_dp = side_stats("PE")
    r.update({"CE_dOI_pct": round(ce_doi, 1), "PE_dOI_pct": round(pe_doi, 1), "CE_dprem_pct": round(ce_dp, 1), "PE_dprem_pct": round(pe_dp, 1)})
    r["PRESS"] = 0 if np.isnan(ce_doi) or np.isnan(pe_doi) or abs(ce_doi - pe_doi) < 5 else (1 if ce_doi > pe_doi else -1)   # more call writing -> up
    r["CHEAP"] = 0 if np.isnan(ce_dp) or np.isnan(pe_dp) or abs(ce_dp - pe_dp) < 5 else (1 if ce_dp < pe_dp else -1)       # the leg that got cheaper -> blasts
    r["MIG_persist"] = r["MIG_late"] if (r["MIG_early"] == r["MIG_late"] != 0) else 0
    r["V2"] = r["MIG_late"] if r["MIG_late"] else r["PINPULL"]
    rows.append(r)

t = pd.DataFrame(rows)
t.to_csv(ROOT / "outputs" / "model" / "direction_postmortem.csv", index=False)
pd.set_option("display.width", 250)
print(t[["index", "date", "mp_13", "mp_1430", "mp_1505", "pain_margin_1505", "spot_1505", "pin_gap", "freeze", "settle",
         "auction", "auction_pct", "MIG_early", "MIG_late", "MIG_persist", "PINPULL", "TREND_day", "TREND_20", "V2", "AUCTION"]].to_string(index=False))
print()
print(t[["index", "date", "CE_dOI_pct", "PE_dOI_pct", "CE_dprem_pct", "PE_dprem_pct", "PRESS", "CHEAP", "AUCTION", "auction_pct"]].to_string(index=False))

print("\nHIT RATES vs auction sign (fires = signal != 0):")
for sig in ("MIG_late", "MIG_early", "MIG_persist", "PINPULL", "TREND_day", "TREND_20", "V2", "PRESS", "CHEAP"):
    f = t[t[sig] != 0]
    hit = int((f[sig] == f.AUCTION).sum())
    fl = f[f.live]; hl = int((fl[sig] == fl.AUCTION).sum())
    big = f[f.auction_pct.abs() >= 0.15]; hb = int((big[sig] == big.AUCTION).sum())
    print(f"  {sig:12s} all {hit}/{len(f)}   live-only {hl}/{len(fl)}   |auction|>=0.15%: {hb}/{len(big)}")
print("\nAGREEMENT combos:")
both = t[(t.MIG_late != 0) & (t.PINPULL != 0)]
agree = both[both.MIG_late == both.PINPULL]; conf = both[both.MIG_late != both.PINPULL]
print(f"  MIG_late & PINPULL agree: {int((agree.MIG_late == agree.AUCTION).sum())}/{len(agree)} right;  conflict days: {len(conf)} "
      f"(migration right {int((conf.MIG_late == conf.AUCTION).sum())}, pin-pull right {int((conf.PINPULL == conf.AUCTION).sum())})")
pm = t[t.MIG_persist != 0]
print(f"  MIG_persist fires {len(pm)} days: right {int((pm.MIG_persist == pm.AUCTION).sum())}; late-only migration (MIG_late!=0, MIG_early==0): "
      f"{len(t[(t.MIG_late != 0) & (t.MIG_early == 0)])} days, right {int(((t.MIG_late != 0) & (t.MIG_early == 0) & (t.MIG_late == t.AUCTION)).sum())}")
print("\nwrote outputs/model/direction_postmortem.csv")
