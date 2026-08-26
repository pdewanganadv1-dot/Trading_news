"""Make a SYNTHETIC dhan_export.zip in exactly the layout scripts/dhan_export.py produces.

    python scripts/make_synthetic_export.py [--out synthetic_dhan_export.zip] [--seed 7]

Purpose: a smoke test for scripts/cas_month_check_real.py when no real Dhan export is at hand
(random-walk index bars + Black-Scholes option bars with noise).  Nothing in it is market data —
do not read anything into the numbers it produces.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import math
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.options import bs_price                                  # noqa: E402

EXPIRY_DAYS = {"NIFTY": ["2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25"],
               "SENSEX": ["2026-08-06", "2026-08-13", "2026-08-20"],
               "BANKNIFTY": ["2026-08-25"]}
LEVEL = {"NIFTY": 24800.0, "SENSEX": 81200.0, "BANKNIFTY": 55600.0}
STEP = {"NIFTY": 50, "SENSEX": 100, "BANKNIFTY": 100}
DAILY_VOL = {"NIFTY": 0.0075, "SENSEX": 0.0075, "BANKNIFTY": 0.010}
STRIKES = ["ATM-3", "ATM-2", "ATM-1", "ATM", "ATM+1", "ATM+2", "ATM+3"]
BARS = 75                      # 09:15 .. 15:25 bar starts
TODAY = dt.date(2026, 8, 26)


def business_days(end: dt.date, n: int) -> list[dt.date]:
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return days[::-1]


def bridge(rng, o: float, c: float, n: int, vol: float) -> np.ndarray:
    """Brownian bridge of n closes from o to c with a U-shaped intraday vol profile."""
    w = np.linspace(1.0, 0.45, n) ** 2; w[-8:] *= 1.8; w /= w.sum()
    z = rng.standard_normal(n) * np.sqrt(w) * vol * o
    path = o + np.cumsum(z)
    path += (c - path[-1]) * np.linspace(1 / n, 1, n)                       # pin the end
    return path


def csv(header, rows) -> str:
    buf = io.StringIO(); buf.write(",".join(header) + "\n")
    for r in rows:
        buf.write(",".join("" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v)) for v in r) + "\n")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="synthetic_dhan_export.zip")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    files = {}
    days = business_days(TODAY, 520)
    for idx in ("NIFTY", "SENSEX", "BANKNIFTY"):
        vol = DAILY_VOL[idx]; step = STEP[idx]
        exp_days = {dt.date.fromisoformat(x) for x in EXPIRY_DAYS[idx]}
        # ---- daily random walk (close-to-close), open = prev close + gap
        closes = LEVEL[idx] * np.exp(np.cumsum(rng.standard_normal(len(days)) * vol - 0.5 * vol ** 2))
        daily, bars5, bars1, opts = [], [], {}, []
        prev_close = closes[0]
        for i, d in enumerate(days):
            gap = rng.standard_normal() * vol * 0.4
            o = prev_close * (1 + gap)
            c = closes[i] if i else o
            day_vol = vol * (1.6 if d in exp_days else 1.0) * rng.uniform(0.7, 1.4)
            if d in exp_days and rng.uniform() < 0.6:                          # make some expiry days trend so the score has something to fire on
                c = o * (1 + rng.choice([-1, 1]) * rng.uniform(0.009, 0.016)); day_vol *= 1.8
            if d >= dt.date(2026, 6, 1):
                path = bridge(rng, o, c, BARS, day_vol)
                # 5-min OHLC from the close path
                rows = []
                for b in range(BARS):
                    bo = o if b == 0 else path[b - 1]
                    bc = path[b]
                    wig = abs(rng.standard_normal()) * day_vol * o * 0.25
                    hi, lo = max(bo, bc) + wig, min(bo, bc) - wig
                    ts = dt.datetime.combine(d, dt.time(9, 15)) + dt.timedelta(minutes=5 * b)
                    rows.append([ts.strftime("%Y-%m-%d %H:%M:%S"), float(bo), float(hi), float(lo), float(bc), 0])
                bars5 += rows
                hi_d, lo_d = max(r[2] for r in rows), min(r[3] for r in rows)
                # closing auction: the daily close is the last bar close +/- an auction move
                c = rows[-1][4] * (1 + rng.standard_normal() * 0.0018) if d >= dt.date(2026, 8, 3) else rows[-1][4]
                if d in exp_days:
                    bars1[d] = rows                                           # 1-min file: same bars (format only)
                    # ---- rolling (expired) option bars: strikes relative to the bar's spot, CE and PE
                    sig_day = day_vol * 1.15                                  # "implied" a bit above realised
                    for b, r in enumerate(rows):
                        spot = r[4]; atm = round(spot / step) * step
                        rem = max(1.0 - b / (BARS + 3), 0.0) * 0.75 + 0.25 * (1.0 if b < BARS - 1 else 0.6)   # 25% reserved for the auction
                        V = (sig_day ** 2) * rem
                        for off_i, off in enumerate(range(-3, 4)):
                            K = atm + off * step
                            for side, kind in (("CE", "CE"), ("PE", "PE")):
                                px = [max(float(bs_price(s, K, V, kind)), 0.05) for s in (r[1], r[2], r[3], r[4])]
                                noise = 1 + rng.standard_normal() * 0.03
                                oo, hh, ll, cc = px[0] * noise, max(px) * noise * 1.01, min(px) * noise * 0.99, px[3] * noise
                                opts.append([r[0], STRIKES[off_i], side, float(K), float(spot), oo, hh, ll, cc,
                                             sig_day * math.sqrt(252) * 100, int(rng.integers(500, 90000)), int(rng.integers(1e4, 5e6))])
            else:
                rng_ = abs(rng.standard_normal()) * day_vol * o
                hi_d, lo_d = max(o, c) + rng_ * 0.6, min(o, c) - rng_ * 0.6
            daily.append([d.isoformat(), float(o), float(hi_d), float(lo_d), float(c)])
            prev_close = c
        files[f"index_daily_{idx}.csv"] = csv(["date", "open", "high", "low", "close"], daily)
        files[f"index_5m_{idx}.csv"] = csv(["ts", "open", "high", "low", "close", "volume"], bars5)
        for d, rows in bars1.items():
            files[f"index_1m_{idx}_{d.isoformat()}.csv"] = csv(["ts", "open", "high", "low", "close", "volume"], rows)
        files[f"rolling_options_{idx}.csv"] = csv(["ts", "offset", "side", "strike", "spot", "open", "high", "low", "close", "iv", "volume", "oi"], opts)
    files["log.txt"] = "SYNTHETIC export from scripts/make_synthetic_export.py — not market data\n"
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, txt in files.items():
            z.writestr(name, txt)
    print("wrote", a.out, "files:", ", ".join(sorted(files)))


if __name__ == "__main__":
    main()
