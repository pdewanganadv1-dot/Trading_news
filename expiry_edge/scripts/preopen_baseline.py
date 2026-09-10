"""Old-regime (Jun-Aug 2026) pre-open baseline for the indices, with the SAME definitions as
preopen_day1.py so week-1 percentiles are like-for-like:
  open_915           OPEN of the 09:15 five-minute bar (the auction-discovered open)
  gap_pct            open_915 / previous trading day's close - 1   (prev close from index_daily_<IDX>.csv,
                     i.e. the official close; fallback: last 5-min bar of the previous day)
  range_915_pct      (high - low) / open of the 09:15 bar
  drift_915_930_pct  close of the 09:25 bar / open_915 - 1          (09:15 -> 09:30)
The earlier file (kept as preopen_baseline_old_regime_v1_barclose.csv) used the 09:15 bar CLOSE as
"open" and a 09:20-09:35 drift, which overstated |gap| by ~40% and flipped the big-gap continuation
rate. Usage: python3 preopen_baseline.py
"""
import datetime as dt
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
rows = []
for idx in ("NIFTY", "SENSEX", "BANKNIFTY"):
    m = pd.read_csv(HERE / "dhan_export" / f"index_5m_{idx}.csv", parse_dates=["ts"])
    m["date"] = m.ts.dt.date
    daily = pd.read_csv(HERE / "dhan_export" / f"index_daily_{idx}.csv")
    dcol = "ts" if "ts" in daily.columns else ("date" if "date" in daily.columns else daily.columns[0])
    daily["d"] = pd.to_datetime(daily[dcol]).dt.date
    dclose = dict(zip(daily.d, daily.close))
    dates = sorted(m.date.unique())
    for i, d in enumerate(dates[1:], 1):
        day = m[m.date == d].sort_values("ts")
        b0 = day[day.ts.dt.time == dt.time(9, 15)]
        if b0.empty:
            continue
        b0 = b0.iloc[0]
        pre930 = day[day.ts.dt.time < dt.time(9, 30)]
        prev_d = dates[i - 1]
        prev_close = dclose.get(prev_d)
        if prev_close is None:
            prev_close = float(m[m.date == prev_d].sort_values("ts").close.iloc[-1])
        rows.append({"index": idx, "date": d.isoformat(), "prev_close": round(float(prev_close), 2),
                     "open_915": float(b0.open), "gap_pct": round((float(b0.open) / prev_close - 1) * 100, 3),
                     "range_915_pct": round((float(b0.high) - float(b0.low)) / float(b0.open) * 100, 3),
                     "close_925": float(pre930.close.iloc[-1]),
                     "drift_915_930_pct": round((float(pre930.close.iloc[-1]) / float(b0.open) - 1) * 100, 3)})
b = pd.DataFrame(rows)
out = ROOT / "outputs" / "model" / "preopen_baseline_old_regime.csv"
b.to_csv(out, index=False)
for idx, g in b.groupby("index"):
    big = g[g.gap_pct.abs() >= 0.5]
    cont = int(((big.gap_pct > 0) == (big.drift_915_930_pct > 0)).sum())
    print(f"{idx:10s} n={len(g)}  {g.date.min()}..{g.date.max()}  median|gap| {g.gap_pct.abs().median():.3f}%  "
          f"median 9:15 range {g.range_915_pct.median():.3f}%  median|drift| {g.drift_915_930_pct.abs().median():.3f}%  "
          f"big-gap(>=0.5%) continuation {cont}/{len(big)}")
print(f"wrote {out.relative_to(ROOT)} ({len(b)} rows)")
