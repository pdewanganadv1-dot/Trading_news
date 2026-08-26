"""Stage-3: was the 25 Aug CAS stock spike PREDICTABLE from what was visible at 15:10?

Joins the spike table (stage 1) with the option-chain setup screen (cas_stock_screen.py rows for 2026-08-25)
and tests, cross-sectionally over the shortlisted stocks:
  1. direction   — does the max-pain pull (sign of max_pain - spot at 15:10) predict the auction spike's sign?
                   ditto for last-hour momentum, and for the two combined ("agree" subset).
  2. size        — Spearman rank-corr of the setup blast_score (OI concentration x ATM share x |mp pull|)
                   with |spike| and with the spike z-score.
  3. tickets     — nearest-OTM option bought at the 15:10 print, settled on the REAL CAS close: hit rate,
                   mean, the leaderboard.
Prints the recipe to apply on the next stock monthly expiry (Tue 29 Sep 2026) and writes
outputs/stocks/stocks100_predict.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
SCR = ROOT / "outputs" / "stocks" / "cas_stock_screen.csv"
FLT = Path(__file__).resolve().parent / "dhan_export_stocks100" / "stocks100_spike_filter.csv"
SL = Path(__file__).resolve().parent / "dhan_export_stocks100" / "spike_shortlist.json"
OUTJ = ROOT / "outputs" / "stocks" / "stocks100_predict.json"

scr = pd.read_csv(SCR)
scr = scr[scr.date == "2026-08-25"].copy()
flt = pd.read_csv(FLT)
short = set(json.load(open(SL)))
df = scr.merge(flt[["symbol", "liq_rank", "aug25_CAS_spike_pct", "aug25_CAS_z", "aug25_CAS_lasthr_pct",
                    "jul28_preCAS_spike_pct", "jun30_preCAS_spike_pct", "typical_abs_eod_move"]],
               on="symbol", how="left")
df["shortlisted"] = df.symbol.isin(short)
df["spike"] = df.aug25_CAS_spike_pct.fillna(df.auction_move_pct)
df["mp_pull_dir"] = np.sign(df.max_pain - df.p1510)
df["lasthr_dir"] = np.sign(df.aug25_CAS_lasthr_pct.fillna(0))
df["spike_dir"] = np.sign(df.spike)
d = df[df.shortlisted & df.spike.notna() & (df.spike_dir != 0)].copy()

res = {"n_shortlist": int(len(d)), "n_all_screened": int(len(df))}

def hitrate(pred_dir, name):
    m = d[pred_dir != 0]
    hits = int((m[pred_dir.name] == m.spike_dir).sum()) if len(m) else 0
    n = int(len(m))
    p = float(stats.binomtest(hits, n, 0.5).pvalue) if n else np.nan
    res[name] = {"hits": hits, "n": n, "rate": round(hits / n, 2) if n else None, "binom_p": round(p, 3) if n else None}
    print(f"  {name:24s} {hits}/{n}  ({hits/n*100:.0f}%)  p={p:.3f}" if n else f"  {name}: n=0")

print(f"Shortlisted stocks with 25 Aug setup+spike: {len(d)} (of {len(df)} screened)\n")
print("1) DIRECTION — did the 15:10 signal call the auction spike's sign?")
hitrate(d.mp_pull_dir, "max_pain_pull")
hitrate(d.lasthr_dir, "last_hour_momentum")
agree = d[(d.mp_pull_dir == d.lasthr_dir) & (d.mp_pull_dir != 0)].copy()
if len(agree):
    hits = int((agree.mp_pull_dir == agree.spike_dir).sum())
    p = float(stats.binomtest(hits, len(agree), 0.5).pvalue)
    res["both_agree"] = {"hits": hits, "n": int(len(agree)), "rate": round(hits / len(agree), 2), "binom_p": round(p, 3)}
    print(f"  {'both_agree':24s} {hits}/{len(agree)}  ({hits/len(agree)*100:.0f}%)  p={p:.3f}")

print("\n2) SIZE — setup blast_score vs realised spike (Spearman):")
for target in ("spike", "aug25_CAS_z"):
    m = d[d.blast_score.notna() & d[target].notna()]
    if len(m) > 4:
        rho, p = stats.spearmanr(m.blast_score, m[target].abs())
        res[f"rho_blast_vs_{target}"] = {"rho": round(float(rho), 2), "p": round(float(p), 3), "n": int(len(m))}
        print(f"  blast_score vs |{target}|: rho={rho:+.2f}  p={p:.3f}  n={len(m)}")

print("\n3) TICKETS — nearest-OTM at the 15:10 print -> real CAS settlement:")
tick = d[d.best_option.notna()].copy()
tick["best"] = tick.best_option.apply(lambda s: json.loads(s.replace("'", '"')) if isinstance(s, str) else s)
tick["ret"] = tick.best.apply(lambda b: b["ret_pct"])
tick = tick.sort_values("ret", ascending=False)
if len(tick):
    res["tickets"] = {"n": int(len(tick)), "mean_ret_pct": round(float(tick.ret.mean()), 0),
                      "positive": int((tick.ret > 0).sum()),
                      "leaderboard": [{"symbol": r.symbol, **r.best, "spike_pct": round(r.spike, 2)}
                                      for r in tick.head(15).itertuples()]}
    print(f"  n={len(tick)}  positive={int((tick.ret>0).sum())}  mean={tick.ret.mean():+.0f}%")
    for r in tick.head(12).itertuples():
        b = r.best
        print(f"    {r.symbol:12s} {b['side']} {b['strike']:.0f}  entry {b['entry']}  settle {b['settle']}  {b['ret_pct']:+.0f}%   (spike {r.spike:+.2f}%)")

cols = ["symbol", "liq_rank", "spike", "aug25_CAS_z", "aug25_CAS_lasthr_pct", "max_pain", "p1510",
        "mp_dist_pct", "atm_oi_share", "oi_hhi", "pcr_oi", "blast_score",
        "jul28_preCAS_spike_pct", "jun30_preCAS_spike_pct"]
d[[c for c in cols if c in d.columns]].sort_values("spike", key=abs, ascending=False) \
    .to_csv(ROOT / "outputs" / "stocks" / "stocks100_predict.csv", index=False)
OUTJ.write_text(json.dumps(res, indent=2, default=float))
print("\nwrote", OUTJ, "and stocks100_predict.csv")
print("\nRECIPE for Tue 29 Sep (next stock monthly expiry): at 15:05-15:10 on the shortlist stocks, read the "
      "chain; trade (paper first) only where max-pain pull and last-hour momentum AGREE, nearest-OTM on that "
      "side, sized as total loss; log spike vs setup to grow this n beyond one expiry.")
