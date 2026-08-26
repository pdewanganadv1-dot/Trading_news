"""Auction anatomy: for each CAS expiry in a Dhan export, the features that plausibly DRIVE the closing-auction
move — computed model-free from the real option OI at 15:10 and the index bars — paired with the move itself.

    python scripts/cas_auction_anatomy.py --zip dhan_export.zip

The thesis (see the "Why the auction blasts" write-up): the auction is a thin book (< 1% of daily turnover), so
the close move ≈ net order imbalance ÷ depth.  On an expiry day the imbalance is created by (a) settlement/hedging
flows tied to where the expiring open interest sits, (b) passive / rebalance flows (bigger on monthly expiries),
and (c) — 13 Aug 2026 — deliberate manipulation.  The daytime chart is a weak proxy.  This script builds the
measurable proxies known by 15:10 so the thesis can be tested as expiries accumulate:

  max_pain           strike minimising total intrinsic paid to option buyers (the classic pin magnet)
  spot_vs_maxpain    (15:10 spot − max_pain) / spot, %  — sign is the direction the pin would pull the close
  oi_concentration   Herfindahl of OI across strikes (0 = spread out, 1 = all at one strike) — thinner ⇒ movable
  atm_oi_share       share of total OI within one strike of spot — how much sits where it can flip ITM/OTM
  pre_auction_range  the day's high-low to 15:10, % — the weak daytime proxy, kept for contrast
  monthly            monthly expiry (heavier passive/settlement flow) vs weekly
Target: auction_move_pct = (CAS close − 15:10) / 15:10, %  (and its absolute value).
Writes outputs/cas_month/cas_auction_anatomy.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from expiry_edge.config import CAS_START_DATE, CONTRACT                           # noqa: E402
from cas_month_check_real import build_bars5, build_daily, load_export            # noqa: E402

OUT = ROOT / "outputs" / "cas_month"
EXIT_T = dt.time(15, 10)


def max_pain(oi_by_strike_ce: dict, oi_by_strike_pe: dict, strikes: list) -> float:
    """Strike that minimises total intrinsic paid out to CE+PE holders, given the OI at each strike."""
    best, bestpay = strikes[0], None
    for S in strikes:
        pay = sum(oi_by_strike_ce.get(K, 0) * max(S - K, 0) for K in strikes) + \
              sum(oi_by_strike_pe.get(K, 0) * max(K - S, 0) for K in strikes)
        if bestpay is None or pay < bestpay:
            bestpay, best = pay, S
    return float(best)


def anatomy_for(idx: str, date, bars5, d, opt) -> dict | None:
    today = bars5[bars5["date"] == date].sort_values("minute")
    pre = today[today["ts"].dt.time <= EXIT_T]
    if not len(pre):
        return None
    p1510 = float(pre["close"].iloc[-1]); close_cas = float(d.loc[pd.Timestamp(date), "close"])
    od = opt[(opt["ts"].dt.date == date) & (opt["ts"].dt.time <= EXIT_T)]
    if not len(od):
        return None
    last_ts = od["ts"].max(); snap = od[od["ts"] == last_ts]
    ce = snap[snap["side"] == "CE"]; pe = snap[snap["side"] == "PE"]
    oi_ce = {float(r.strike): float(r.oi or 0) for r in ce.itertuples()}
    oi_pe = {float(r.strike): float(r.oi or 0) for r in pe.itertuples()}
    strikes = sorted(set(oi_ce) | set(oi_pe))
    if len(strikes) < 3:
        return None
    tot_oi = {K: oi_ce.get(K, 0) + oi_pe.get(K, 0) for K in strikes}
    T = sum(tot_oi.values()) or 1.0
    mp = max_pain(oi_ce, oi_pe, strikes)
    step = CONTRACT[idx]["strike_step"]
    atm_share = sum(v for K, v in tot_oi.items() if abs(K - p1510) <= step) / T
    hhi = sum((v / T) ** 2 for v in tot_oi.values())
    return {"index": idx, "date": str(date), "p1510": p1510, "cas_close": close_cas,
            "auction_move": close_cas - p1510, "auction_move_pct": (close_cas / p1510 - 1) * 100,
            "abs_move_pct": abs(close_cas / p1510 - 1) * 100,
            "max_pain": mp, "spot_vs_maxpain_pct": (p1510 - mp) / p1510 * 100,
            "oi_concentration": hhi, "atm_oi_share": atm_share * 100,
            "pre_auction_range_pct": float((pre["high"].max() - pre["low"].min()) / pre["open"].iloc[0] * 100),
            "monthly": bool(d.loc[pd.Timestamp(date), "is_monthly"]),
            "auction_toward_maxpain": bool(np.sign(close_cas - p1510) == np.sign(mp - p1510)) if abs(mp - p1510) > 1e-9 else None}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--zip", required=True); ap.add_argument("--since", default=CAS_START_DATE.isoformat())
    a = ap.parse_args(); since = pd.Timestamp(a.since).date()
    files = load_export(Path(a.zip)); out = {}
    for idx in ("NIFTY", "SENSEX", "BANKNIFTY"):
        if any(f"{p}_{idx}.csv" not in files for p in ("index_5m", "index_daily", "rolling_options")):
            continue
        bars5 = build_bars5(files[f"index_5m_{idx}.csv"]); d = build_daily(files[f"index_daily_{idx}.csv"], idx)
        opt = files[f"rolling_options_{idx}.csv"].copy(); opt["ts"] = pd.to_datetime(opt["ts"])
        dates = [x for x in sorted(set(opt["ts"].dt.date)) if x >= since and x in d.index.date and bool(d.loc[pd.Timestamp(x), "is_expiry"])]
        rows = [r for r in (anatomy_for(idx, x, bars5, d, opt) for x in dates) if r]
        if rows:
            out[idx] = rows
            print(f"[{idx}] {len(rows)} expiries")
            for r in rows:
                print(f"  {r['date']}  move {r['auction_move']:+7.1f} ({r['auction_move_pct']:+.2f}%)  maxpain {r['max_pain']:.0f} "
                      f"spot-mp {r['spot_vs_maxpain_pct']:+.2f}%  OIconc {r['oi_concentration']:.2f}  ATMoi {r['atm_oi_share']:.0f}%  "
                      f"toward_pin={r['auction_toward_maxpain']}")
    (OUT / "cas_auction_anatomy.json").write_text(json.dumps(out, indent=0))
    print("wrote", OUT / "cas_auction_anatomy.json")


if __name__ == "__main__":
    main()
