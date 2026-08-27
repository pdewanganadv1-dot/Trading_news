"""Afternoon anatomy of 25 Aug (first CAS stock expiry), one row per screened stock:
stock at 1:00 PM -> 15:10 freeze -> real CAS close, and the nearest-OTM option's
price at 1 PM / 15:10 / settlement (intrinsic on the CAS close).
Writes outputs/stocks/stocks100_1pm_blast.csv and prints the full table.
"""
import datetime as dt
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "dhan_export_stocks100"
ROOT = HERE.parent
DAY = "2026-08-25"
T1PM, TFRZ = dt.time(13, 0), dt.time(15, 10)

closes = pd.read_csv(OUT / "stock_close.csv").set_index("symbol").cas_close.to_dict()
scr = pd.read_csv(ROOT / "outputs" / "stocks" / "cas_stock_screen.csv")
scr = scr[scr.date == DAY].set_index("symbol")
short = set(json.load(open(OUT / "spike_shortlist.json")))

rows = []
for f in sorted(OUT.glob("rolling_options_*.csv")):
    sym = f.stem.replace("rolling_options_", "")
    if sym not in scr.index or sym not in closes:
        continue
    best = scr.loc[sym, "best_option"]
    if not isinstance(best, str):
        continue
    b = json.loads(best.replace("'", '"'))
    d = pd.read_csv(f, parse_dates=["ts"])
    d = d[d.ts.dt.date == dt.date.fromisoformat(DAY)]
    if d.empty:
        continue
    d["time"] = d.ts.dt.time

    def last_at(df, t):
        m = df[df.time <= t]
        return float(m.close.iloc[-1]) if len(m) else None

    spot1 = d[d.time <= T1PM].spot.iloc[-1] if len(d[d.time <= T1PM]) else None
    spotf = d[d.time <= TFRZ].spot.iloc[-1] if len(d[d.time <= TFRZ]) else None
    cas = float(closes[sym])
    opt = d[(d.side == b["side"]) & (d.strike == b["strike"])]
    o1, of = last_at(opt, T1PM), last_at(opt, TFRZ)
    settle = b["settle"]
    rows.append({
        "symbol": sym, "shortlisted": sym in short,
        "spot_1pm": spot1, "spot_1510": spotf, "cas_close": cas,
        "stk_1pm_to_1510_pct": round((spotf / spot1 - 1) * 100, 2) if spot1 and spotf else None,
        "stk_auction_pct": round((cas / spotf - 1) * 100, 2) if spotf else None,
        "otm": f"{b['side']} {b['strike']:.0f}",
        "opt_1pm": o1, "opt_1510": of, "opt_settle": settle,
        "opt_ret_1510_pct": b["ret_pct"],
        "opt_ret_1pm_pct": round((settle / o1 - 1) * 100, 0) if o1 else None,
    })

t = pd.DataFrame(rows).sort_values("opt_ret_1510_pct", ascending=False)
t.to_csv(ROOT / "outputs" / "stocks" / "stocks100_1pm_blast.csv", index=False)
pd.set_option("display.width", 250)
print(t.to_string(index=False))
print(f"\n{len(t)} stocks; wrote outputs/stocks/stocks100_1pm_blast.csv")
