"""Pre-open auction anatomy for NSE's new regime (pre-open call auction extended to F&O stocks,
live Mon 7 Sep 2026). Measures, per instrument, what the auction-discovered 09:15 open did:

  gap_pct            prev close -> 09:15 open (overnight gap absorbed by the auction)
  range_915_pct      high-low of the 09:15 5-min bar / open (post-auction price discovery; a wide bar
                     means the auction print was NOT the clearing level)
  drift_915_930_pct  09:15 open -> 09:30 (does the auction level hold, continue or fade)
  continuation       drift has the gap's sign (old regime, |gap| >= 0.5%: see baseline stats printed)

Indices are compared with the old-regime baseline (outputs/model/preopen_baseline_old_regime.csv,
Jun-Aug 2026: median |gap| NIFTY 0.32%, BANKNIFTY 0.36%, SENSEX 0.39%) and with the 09:15-bar range
distribution from scripts/dhan_export/index_5m_<IDX>.csv. Stocks have no old-regime pre-open bars
in the repo, so they get the cross-section only.

Appends to outputs/model/preopen_week1.csv (one row per date x instrument, re-runs overwrite).
Usage: python3 preopen_day1.py [--date 2026-09-07]
"""
import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IDX = {"NIFTY": "13", "SENSEX": "51", "BANKNIFTY": "25"}
STOCKS = ["TATASTEEL", "INFY", "ADANIENT", "HDFCBANK", "SBIN", "RELIANCE",
          "ICICIBANK", "AXISBANK", "HCLTECH", "ADANIPORTS"]
BIG_GAP = 0.5


def headers():
    tok = os.environ.get("DHAN_ACCESS_TOKEN")
    if not tok:
        sys.exit("[STOP] DHAN_ACCESS_TOKEN env var missing")
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        p = tok.split(".")[1]
        cid = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["dhanClientId"]
    return {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}


def post(H, path, body):
    for i in range(4):
        r = requests.post(f"https://api.dhan.co/v2/{path}", headers=H, json=body, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(2 + 2 * i)
            continue
        if r.status_code in (401, 403):
            sys.exit("[STOP] 401/403 — token stale; regenerate on Dhan and update the env var.")
        print(f"  {path} HTTP {r.status_code}: {r.text[:120]}")
        return None
    return None


def bars(j):
    if not j or not j.get("timestamp"):
        return pd.DataFrame()
    d = pd.DataFrame({k: j[k] for k in ("timestamp", "open", "high", "low", "close") if k in j})
    d["ts"] = pd.to_datetime(d.timestamp, unit="s") + pd.Timedelta(hours=5, minutes=30)
    return d


def measure(H, name, kind, sid, seg, inst, day):
    hist = bars(post(H, "charts/historical", {"securityId": sid, "exchangeSegment": seg, "instrument": inst,
                                              "expiryCode": 0, "fromDate": (day - dt.timedelta(days=10)).isoformat(),
                                              "toDate": (day + dt.timedelta(days=1)).isoformat()}))
    time.sleep(1)
    intra = bars(post(H, "charts/intraday", {"securityId": sid, "exchangeSegment": seg, "instrument": inst,
                                             "interval": "5", "fromDate": day.isoformat(),
                                             "toDate": (day + dt.timedelta(days=1)).isoformat()}))
    time.sleep(1)
    if hist.empty or intra.empty:
        print(f"  {name}: no data (hist {len(hist)} / intra {len(intra)})")
        return None
    prev = hist[hist.ts.dt.date < day]
    t = intra[intra.ts.dt.date == day].sort_values("ts")
    if prev.empty or t.empty:
        print(f"  {name}: prev-close or today's bars missing")
        return None
    prev_close = float(prev.close.iloc[-1])
    b0 = t.iloc[0]
    open_915 = float(b0.open)
    pre930 = t[t.ts.dt.time < dt.time(9, 30)]
    close_930 = float(pre930.close.iloc[-1])
    gap = (open_915 / prev_close - 1) * 100
    drift = (close_930 / open_915 - 1) * 100
    return {"date": day.isoformat(), "instrument": name, "kind": kind,
            "prev_date": prev.ts.iloc[-1].date().isoformat(), "prev_close": prev_close,
            "open_915": open_915, "first_bar": b0.ts.strftime("%H:%M"),
            "gap_pct": round(gap, 3),
            "range_915_pct": round((float(b0.high) - float(b0.low)) / open_915 * 100, 3),
            "close_930": close_930, "drift_915_930_pct": round(drift, 3),
            "continuation": (gap > 0) == (drift > 0) if abs(gap) >= BIG_GAP else None,
            "last_ts": t.ts.iloc[-1].strftime("%H:%M"), "last": float(t.close.iloc[-1]),
            "day_pct_so_far": round((float(t.close.iloc[-1]) / prev_close - 1) * 100, 3)}


def baseline_stats():
    """Old-regime reference per index: |gap| distribution, big-gap continuation, 09:15-bar range."""
    b = pd.read_csv(ROOT / "outputs" / "model" / "preopen_baseline_old_regime.csv")
    out = {}
    for idx, g in b.groupby("index"):
        big = g[g.gap_pct.abs() >= BIG_GAP]
        st = {"n": len(g), "abs_gaps": g.gap_pct.abs().sort_values().to_numpy(),
              "med_abs_gap": float(g.gap_pct.abs().median()),
              "cont": int(((big.gap_pct > 0) == (big.drift_915_930_pct > 0)).sum()), "n_big": len(big)}
        f = HERE / "dhan_export" / f"index_5m_{idx}.csv"
        if f.exists():
            m = pd.read_csv(f, parse_dates=["ts"])
            m = m[m.ts.dt.time == dt.time(9, 15)]
            rng = ((m.high - m.low) / m.open * 100)
            st["ranges"] = rng.sort_values().to_numpy()
            st["med_range"] = float(rng.median())
        out[idx] = st
    return out


def pct_rank(arr, x):
    return float((arr <= x).mean() * 100) if arr is not None and len(arr) else float("nan")


def main(day):
    H = headers()
    ids = json.loads((HERE / "dhan_export_stocks100" / "top100.json").read_text())
    rows = []
    for name, sid in IDX.items():
        r = measure(H, name, "index", sid, "IDX_I", "INDEX", day)
        if r:
            rows.append(r)
    for sym in STOCKS:
        if sym not in ids:
            print(f"  {sym}: not in top100.json")
            continue
        r = measure(H, sym, "stock", str(ids[sym]), "NSE_EQ", "EQUITY", day)
        if r:
            rows.append(r)
    if not rows:
        sys.exit("[STOP] nothing measured")
    base = baseline_stats()
    for r in rows:
        st = base.get(r["instrument"])
        r["base_med_abs_gap"] = round(st["med_abs_gap"], 3) if st else None
        r["gap_pctile_vs_base"] = round(pct_rank(st["abs_gaps"], abs(r["gap_pct"])), 0) if st else None
        r["base_med_range_915"] = round(st.get("med_range", float("nan")), 3) if st else None
        r["range_pctile_vs_base"] = round(pct_rank(st.get("ranges"), r["range_915_pct"]), 0) if st else None
    w = pd.DataFrame(rows)
    out = ROOT / "outputs" / "model" / "preopen_week1.csv"
    if out.exists():
        old = pd.read_csv(out)
        old = old[~((old.date == day.isoformat()) & old.instrument.isin(w.instrument))]
        w = pd.concat([old, w], ignore_index=True)
    w.to_csv(out, index=False)

    print(f"\nPRE-OPEN DAY {day}  (auction-discovered 09:15 open; bars to {rows[0]['last_ts']})")
    print(f"{'inst':11s} {'prev':>10s} {'open915':>10s} {'gap%':>7s} {'rng915%':>8s} {'drift930%':>10s} {'day%':>7s}  vs old regime")
    for r in rows:
        cmp = ""
        if r["base_med_abs_gap"] is not None:
            cmp = (f"|gap| p{r['gap_pctile_vs_base']:.0f} (med {r['base_med_abs_gap']:.2f}%), "
                   f"9:15 range p{r['range_pctile_vs_base']:.0f} (med {r['base_med_range_915']:.2f}%)")
        c = "" if r["continuation"] is None else ("  CONT" if r["continuation"] else "  FADE")
        print(f"{r['instrument']:11s} {r['prev_close']:>10.1f} {r['open_915']:>10.1f} {r['gap_pct']:>+7.2f} "
              f"{r['range_915_pct']:>8.2f} {r['drift_915_930_pct']:>+10.2f} {r['day_pct_so_far']:>+7.2f}  {cmp}{c}")
    stk = w[(w.date == day.isoformat()) & (w.kind == "stock")]
    if len(stk):
        print(f"\nstocks (n={len(stk)}): median |gap| {stk.gap_pct.abs().median():.2f}%, "
              f"median 9:15 range {stk.range_915_pct.median():.2f}%, median drift {stk.drift_915_930_pct.median():+.2f}%; "
              f"big gaps (>= {BIG_GAP}%): {int((stk.gap_pct.abs() >= BIG_GAP).sum())}, "
              f"of which continued {int(stk.continuation.fillna(False).astype(bool).sum())}")
    for idx, st in base.items():
        print(f"old regime {idx}: n={st['n']}, median |gap| {st['med_abs_gap']:.2f}%, big-gap continuation "
              f"{st['cont']}/{st['n_big']}, median 9:15 range {st.get('med_range', float('nan')):.2f}%")
    print(f"\nwrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)).date().isoformat())
    main(dt.date.fromisoformat(ap.parse_args().date))
