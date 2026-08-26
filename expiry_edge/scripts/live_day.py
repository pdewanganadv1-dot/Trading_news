"""Expiry-day live helper — run on your own machine during the session.

Pulls today's 5-min index bars (yfinance ^NSEI / ^BSESN / ^NSEBANK, or the NSE charting
API through `openchart`), recomputes the same features/signals used in the backtest,
prints which triggers have fired so far today, and calls the scorecard for each.

  pip install yfinance openchart            # either source is enough
  python scripts/live_day.py --index NIFTY --source yf
  python scripts/live_day.py --index NIFTY --source openchart

NSE/BSE and Yahoo block cloud IPs, so this script is for a home/office machine.
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.calendar import label_expiry_days          # noqa: E402
from expiry_edge.data import daily_table, fetch_openchart_intraday, fetch_yf_intraday, resample  # noqa: E402
from expiry_edge.features import add_features               # noqa: E402
from expiry_edge.signals import signal_library              # noqa: E402

YF = {"NIFTY": "^NSEI", "SENSEX": "^BSESN", "BANKNIFTY": "^NSEBANK"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="NIFTY", choices=list(YF))
    ap.add_argument("--source", default="yf", choices=["yf", "openchart"])
    a = ap.parse_args()

    if a.source == "yf":
        bars = fetch_yf_intraday(YF[a.index], interval="1m", period="7d")      # 1-min, last 7 days
        bars["ts"] = pd.to_datetime(bars["ts"])
    else:
        bars = fetch_openchart_intraday(a.index, "1m", start=dt.date.today() - dt.timedelta(days=45))
        bars["ts"] = pd.to_datetime(bars["ts"])
    bars = bars.dropna()
    t = bars["ts"].dt.time
    bars = bars[(t >= dt.time(9, 15)) & (t <= dt.time(15, 29))].copy()
    bars["date"] = bars["ts"].dt.date
    bars["minute"] = ((bars["ts"].dt.hour - 9) * 60 + bars["ts"].dt.minute - 15).astype(int)
    m = bars[["ts", "open", "high", "low", "close", "date", "minute"]]
    d = daily_table(m).join(label_expiry_days(daily_table(m).index, a.index))
    # rv20 needs 20 sessions – with only 7 days of 1-min data fall back to what we have
    if d["rv20"].isna().all():
        park = np.log(d["high"] / d["low"]) ** 2 / (4 * np.log(2))
        d["rv20"] = np.sqrt(park.expanding().mean().shift(1)) * 100
        d["range20"] = d["range_pct"].expanding().mean().shift(1)
    b5 = resample(m, 5)
    f = add_features(b5, d)
    today = f["date"].max()
    ft = f[f["date"] == today]
    print(f"\n{a.index} — {today}  expiry day: {bool(d.loc[str(today), 'is_expiry']) if str(today) in d.index.astype(str) else 'unknown'}")
    print(f"open {ft['open'].iloc[0]:.1f}  last {ft['close'].iloc[-1]:.1f}  gap {ft['gap_pct'].iloc[0]:+.2f}%  "
          f"OR30 width {ft['or30_width_pct'].iloc[0]:.2f}% ({ft['or30_rel'].iloc[0]:.2f}x typical day range)  vol regime {ft['vol_regime'].iloc[0]}")
    lib = signal_library(f)
    fired = []
    for name, (lm, sm) in lib.items():
        if name.startswith("BASELINE"):
            continue
        for direction, mask in (("long", lm), ("short", sm)):
            hits = f.loc[mask.fillna(False) & (f["date"] == today), "ts"]
            for ts in hits:
                fired.append((ts, name, direction))
    fired.sort()
    if not fired:
        print("no triggers fired yet today")
    for ts, name, direction in fired[-12:]:
        print(f"  {ts:%H:%M}  {name:<22} {direction}")
    if fired:
        ts, name, direction = fired[-1]
        gap = float(ft["gap_pct"].iloc[0]); vol = str(ft["vol_regime"].iloc[0]); orr = float(ft["or30_rel"].iloc[0])
        cmd = [sys.executable, str(ROOT / "scripts" / "scorecard.py"), "--index", a.index, "--signal", name,
               "--time", f"{ts:%H:%M}", "--direction", direction, "--gap", f"{gap:.2f}"]
        if vol in ("low", "mid", "high"):
            cmd += ["--vol", vol]
        if np.isfinite(orr):
            cmd += ["--or-rel", f"{orr:.2f}"]
        print("\nScorecard for the latest trigger:")
        subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
