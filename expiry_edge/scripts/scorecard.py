"""Expiry-day entry scorecard — the decision tool.

Given the index, the technical trigger that just fired, the clock, and the day's
context (gap, vol regime, opening-range width), it looks up what happened
historically to an ATM option bought in that situation on expiry days, compares it
with a random entry at the same hour, and prints a GO / LEAN / NO verdict.

Examples
  python scripts/scorecard.py --index NIFTY --signal ORB30 --time 10:20 --gap 0.6
  python scripts/scorecard.py --index BANKNIFTY --signal LateBreak_1415 --time 14:40 --vol low
  python scripts/scorecard.py --index NIFTY --rank --time 14:30      # rank all triggers for that hour
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.evaluate import summarize   # noqa: E402

OUT = ROOT / "outputs"
HOURS = ["09:15-10", "10-11", "11-12", "12-13", "13-14", "14-15", "15-15:30"]


def hour_bucket(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    minute = (h - 9) * 60 + m - 15
    edges = [-1, 44, 104, 164, 224, 284, 344, 375]
    for i in range(len(edges) - 1):
        if edges[i] < minute <= edges[i + 1]:
            return HOURS[i]
    return HOURS[-1]


def load(index: str) -> pd.DataFrame:
    r = pd.read_parquet(OUT / f"{index}_events_expiry.parquet")
    r["gap_bucket"] = pd.cut(r["gap_abs"], [-0.01, 0.2, 0.5, 100], labels=["<0.2%", "0.2-0.5%", ">0.5%"])
    r["or_bucket"] = pd.cut(r["or30_rel"], [-0.01, 0.4, 0.7, 100], labels=["<40%", "40-70%", ">70%"])
    return r


def verdict(row: pd.Series, base: pd.Series) -> str:
    net = row["ret60_net_mean"]
    lift = row["p_mfe60_ge50"] - base["p_mfe60_ge50"]
    if row["n"] < 15:
        return "INSUFFICIENT SAMPLE (n<15) — treat as NO"
    if net > 5 and lift > 5:
        return "GO — positive net expectancy and premium-expansion odds above the hour's baseline"
    if net > 0 or lift > 5:
        return "LEAN — marginal; only with small size and no tight stop"
    return "NO — historically no edge for a buyer here (baseline-like or worse)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="NIFTY", choices=["NIFTY", "SENSEX", "BANKNIFTY"])
    ap.add_argument("--signal", default=None, help="trigger name, e.g. ORB30, LateBreak_1415, RangeExp, RSI_mom ...")
    ap.add_argument("--time", default=None, help="HH:MM (IST) when the trigger fired")
    ap.add_argument("--direction", default=None, choices=["long", "short"], help="signal direction (optional split)")
    ap.add_argument("--gap", type=float, default=None, help="today's opening gap in %% (signed or absolute)")
    ap.add_argument("--vol", default=None, choices=["low", "mid", "high"], help="vol regime (trailing-20d realised vol tercile)")
    ap.add_argument("--or-rel", dest="or_rel", type=float, default=None, help="30-min opening range as a fraction of the 20-day avg day range (e.g. 0.5)")
    ap.add_argument("--rank", action="store_true", help="rank all triggers for the given hour")
    a = ap.parse_args()

    r = load(a.index)
    hb = hour_bucket(a.time) if a.time else None
    base_all = r[r.signal == "BASELINE_every_bar"]
    base = summarize(base_all[base_all.hour_bucket == hb] if hb else base_all).iloc[0]

    if a.rank or not a.signal:
        sub = r[r.hour_bucket == hb] if hb else r
        t = summarize(sub).sort_values("ret60_net_mean", ascending=False)
        cols = ["n", "idx_hit30", "p_mfe60_ge50", "p_mfe60_ge100", "ret60_net_mean", "p_ret60_pos", "p0_med"]
        print(f"\n{a.index} expiry days — triggers ranked for hour bucket {hb or 'ALL'} (ATM option, net of costs)\n")
        print(t[cols].to_string())
        print("\nBaseline (random entry, same hour):", {k: float(base[k]) for k in ["p_mfe60_ge50", "ret60_net_mean"]})
        return

    sub = r[r.signal == a.signal]
    filt = []
    if hb:
        sub = sub[sub.hour_bucket == hb]; filt.append(f"hour={hb}")
    if a.direction:
        sub = sub[sub.direction == (1 if a.direction == "long" else -1)]; filt.append(f"direction={a.direction}")
    if a.gap is not None:
        g = abs(a.gap); lab = "<0.2%" if g < 0.2 else ("0.2-0.5%" if g <= 0.5 else ">0.5%")
        sub = sub[sub.gap_bucket == lab]; filt.append(f"gap={lab}")
    if a.vol:
        sub = sub[sub.vol_regime == a.vol]; filt.append(f"vol={a.vol}")
    if a.or_rel is not None:
        lab = "<40%" if a.or_rel < 0.4 else ("40-70%" if a.or_rel <= 0.7 else ">70%")
        sub = sub[sub.or_bucket == lab]; filt.append(f"OR30/typical-range={lab}")
    if len(sub) == 0:
        print("No historical events match these filters. Loosen them (drop --gap/--vol/--or-rel).")
        return
    row = summarize(sub).iloc[0]
    print(f"\n=== {a.index} expiry-day scorecard: {a.signal} [{', '.join(filt) or 'no filters'}] ===")
    print(f"historical events: {int(row['n'])} on {int(row['days'])} expiry days")
    print(f"index moved your way after 30 min:        {row['idx_hit30']:.0f}%  (median best excursion in 60 min: {row['idx_mfe60_med']:.2f}%)")
    print(f"ATM premium touched +50% within 30/60 min: {row['p_mfe30_ge50']:.0f}% / {row['p_mfe60_ge50']:.0f}%   (baseline this hour: {base['p_mfe60_ge50']:.0f}%)")
    print(f"ATM premium touched +100% within 60 min:   {row['p_mfe60_ge100']:.0f}%   (baseline: {base['p_mfe60_ge100']:.0f}%)")
    print(f"mean 60-min return, net of costs:          {row['ret60_net_mean']:+.1f}%  (baseline: {base['ret60_net_mean']:+.1f}%)  | positive in {row['p_ret60_pos']:.0f}% of cases")
    print(f"bracket SL-30%/TP+50%/60-min:              {row['bracket_mean']:+.1f}%  (TP hit {row['bracket_win']:.0f}%)  <- capping winners usually destroys the edge")
    print(f"typical ATM premium at entry (model):      {row['p0_med']:.0f} pts")
    print("\nVERDICT:", verdict(row, base))
    print("\nCAS clock (since 3-Aug-2026): cash index stops at 15:15; auction sets the close ~15:35; options trade to 15:40.")
    print("Decide by 15:15. Anything held past 15:15 is a bet on the auction print, not on the chart.")


if __name__ == "__main__":
    main()
