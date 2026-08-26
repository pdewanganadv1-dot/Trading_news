"""Stage-1 CAS spike filter — ONE cheap intraday call per F&O stock, no option data.

For every NSE F&O underlying (from stocks100_rank.csv), pull 5-min cash bars 2026-06-01..2026-08-25 in a
single charts/intraday call, then measure on each monthly expiry (Tue 30 Jun, Tue 28 Jul pre-CAS; Tue 25 Aug
first CAS settlement):
    last_hour_pct   14:15 -> 15:10 move (the pre-freeze run-in)
    spike_pct       15:10 print -> close  (25 Aug: the REAL CAS close from the daily pull -> the auction move;
                     Jun/Jul: last intraday bar ~15:25 -> old VWAP-close regime, the baseline)
    z               |spike| vs the stock's own non-expiry-day |15:10->eod| distribution (median/MAD)
Writes stocks100_spike_filter.csv (all stocks x 3 expiries) and spike_shortlist.json — the stocks worth
spending option-data calls on: top-100 liquidity AND on 25 Aug (|spike| >= 1.0% or z >= 3).
"""
import base64
import datetime as dt
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "dhan_export_stocks100"
BASE = "https://api.dhan.co/v2"
EXPIRIES = {"2026-06-30": "jun30_preCAS", "2026-07-28": "jul28_preCAS", "2026-08-25": "aug25_CAS"}
FREEZE = dt.time(15, 10)

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
cid = os.environ.get("DHAN_CLIENT_ID")
if not cid:
    p = TOKEN.split(".")[1]
    cid = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["dhanClientId"]
H = {"access-token": TOKEN, "client-id": cid, "Content-Type": "application/json", "Accept": "application/json"}


def post(path, body, tries=4):
    for i in range(tries):
        try:
            r = requests.post(f"{BASE}/{path}", headers=H, json=body, timeout=60)
        except requests.RequestException:
            time.sleep(2 + 2 * i)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(3 + 2 * i)
            continue
        return None
    return None


def day_metrics(day: pd.DataFrame, cas_close: float | None):
    """(last_hour_pct, spike_pct, p1510) for one date's 5-min bars."""
    pre = day[day.time <= FREEZE]
    if len(pre) < 10:
        return None
    p1510 = float(pre.close.iloc[-1])
    a1415 = pre[pre.time >= dt.time(14, 15)]
    lasthr = (p1510 / float(a1415.close.iloc[0]) - 1) * 100 if len(a1415) else np.nan
    close = cas_close if cas_close else float(day.close.iloc[-1])
    return lasthr, (close / p1510 - 1) * 100, p1510


def main():
    rank = pd.read_csv(OUT / "stocks100_rank.csv")
    rows, t0 = [], time.time()
    for n, r in rank.iterrows():
        sym, sid = r.symbol, str(r.security_id)
        j = post("charts/intraday", {"securityId": sid, "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
                                     "interval": "5", "fromDate": "2026-06-01", "toDate": "2026-08-26"})
        if not (j and j.get("timestamp")):
            print(f"[{n + 1}/{len(rank)}] {sym}: no intraday")
            time.sleep(0.2)
            continue
        d = pd.DataFrame({"ts": [dt.datetime.utcfromtimestamp(int(t)) + dt.timedelta(hours=5, minutes=30) for t in j["timestamp"]],
                          "close": j["close"]})
        d["date"] = d.ts.dt.date
        d["time"] = d.ts.dt.time
        # baseline: |15:10 -> last bar| on NON-expiry days
        base = []
        for date, day in d.groupby("date"):
            if str(date) in EXPIRIES:
                continue
            m = day_metrics(day, None)
            if m:
                base.append(abs(m[1]))
        med = float(np.median(base)) if base else np.nan
        mad = float(np.median(np.abs(np.array(base) - med))) if base else np.nan
        row = {"symbol": sym, "liq_rank": n + 1, "mean_turnover_cr": r.mean_turnover_cr,
               "typical_abs_eod_move": round(med, 3), "n_base_days": len(base)}
        for iso, tag in EXPIRIES.items():
            day = d[d.date == dt.date.fromisoformat(iso)]
            cas_close = float(r.close_25aug) if (tag == "aug25_CAS" and pd.notna(r.close_25aug)) else None
            m = day_metrics(day, cas_close) if len(day) else None
            if m:
                lasthr, spike, _ = m
                z = (abs(spike) - med) / (1.4826 * mad) if mad and mad > 0 else np.nan
                row |= {f"{tag}_lasthr_pct": round(lasthr, 2), f"{tag}_spike_pct": round(spike, 2),
                        f"{tag}_z": round(z, 1)}
        rows.append(row)
        if (n + 1) % 25 == 0:
            print(f"[{n + 1}/{len(rank)}] ... {time.time() - t0:.0f}s", flush=True)
        time.sleep(0.2)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "stocks100_spike_filter.csv", index=False)
    sel = df[(df.liq_rank <= 100) & ((df.aug25_CAS_spike_pct.abs() >= 1.0) | (df.aug25_CAS_z >= 3))]
    sel = sel.sort_values("aug25_CAS_spike_pct", key=abs, ascending=False)
    json.dump(sel.symbol.tolist(), open(OUT / "spike_shortlist.json", "w"))
    print(f"\n{len(df)} stocks screened in {time.time() - t0:.0f}s; shortlist = {len(sel)}")
    cols = ["symbol", "liq_rank", "aug25_CAS_spike_pct", "aug25_CAS_z", "aug25_CAS_lasthr_pct",
            "jul28_preCAS_spike_pct", "jun30_preCAS_spike_pct", "typical_abs_eod_move"]
    print(sel[[c for c in cols if c in sel.columns]].to_string(index=False))


if __name__ == "__main__":
    main()
