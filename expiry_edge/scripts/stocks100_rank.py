"""Rank all NSE F&O stock underlyings by August cash liquidity (mean daily turnover) via Dhan v2.

Writes (in expiry_edge/scripts/dhan_export_stocks100/):
  stocks100_rank.csv   symbol,security_id,days,mean_turnover_cr,close_25aug,prev_close_24aug
  stock_close.csv      symbol,date,cas_close   (25 Aug 2026 daily close = the CAS close)
  top100.json          {symbol: security_id} for the top 100 by turnover
"""
import base64
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "dhan_export_stocks100"
OUT.mkdir(exist_ok=True)
BASE = "https://api.dhan.co/v2"

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
cid = os.environ.get("DHAN_CLIENT_ID")
if not cid:  # container env lacks it; the JWT payload carries dhanClientId
    p = TOKEN.split(".")[1]
    cid = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["dhanClientId"]
H = {"access-token": TOKEN, "client-id": cid, "Content-Type": "application/json", "Accept": "application/json"}


def post(path, body, tries=4):
    for i in range(tries):
        r = requests.post(f"{BASE}/{path}", headers=H, json=body, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(3 + 2 * i)
            continue
        return None
    return None


def universe():
    txt = requests.get("https://images.dhan.co/api-data/api-scrip-master.csv", timeout=120).text
    m = pd.read_csv(pd.io.common.StringIO(txt), low_memory=False)
    opt = m[(m.SEM_EXM_EXCH_ID == "NSE") & (m.SEM_INSTRUMENT_NAME == "OPTSTK")]
    pat = re.compile(r"^(.+)-[A-Z][a-z]{2}\d{4}-")
    unds = sorted({mm.group(1) for s in opt.SEM_TRADING_SYMBOL if (mm := pat.match(str(s)))})
    eq = m[(m.SEM_EXM_EXCH_ID == "NSE") & (m.SEM_SEGMENT == "E") & (m.SEM_SERIES.fillna("") == "EQ")]
    eqmap = dict(zip(eq.SEM_TRADING_SYMBOL.str.strip(), eq.SEM_SMST_SECURITY_ID.astype(str)))
    missing = [u for u in unds if u not in eqmap]
    return {u: eqmap[u] for u in unds if u in eqmap}, missing


def main():
    uni, missing = universe()
    print(f"{len(uni)} F&O underlyings matched to NSE EQ ids (unmatched: {missing})")
    rows = []
    for n, (sym, sid) in enumerate(sorted(uni.items()), 1):
        j = post("charts/historical", {"securityId": sid, "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
                                       "expiryCode": 0, "fromDate": "2026-08-01", "toDate": "2026-08-27"})
        if not (j and j.get("timestamp")):
            print(f"  [{n}] {sym}: no daily data")
            time.sleep(0.25)
            continue
        d = pd.DataFrame({"ts": j["timestamp"], "close": j["close"],
                          "volume": j.get("volume") or [0] * len(j["timestamp"])})
        d["date"] = d.ts.map(lambda t: (dt.datetime.utcfromtimestamp(int(t)) + dt.timedelta(hours=5, minutes=30)).date())
        d["turnover"] = d.close * d.volume
        c25 = d.loc[d.date == dt.date(2026, 8, 25), "close"]
        c24 = d.loc[d.date == dt.date(2026, 8, 24), "close"]
        rows.append({"symbol": sym, "security_id": sid, "days": len(d),
                     "mean_turnover_cr": round(d.turnover.mean() / 1e7, 2),
                     "close_25aug": round(float(c25.iloc[0]), 2) if len(c25) else None,
                     "prev_close_24aug": round(float(c24.iloc[0]), 2) if len(c24) else None})
        time.sleep(0.25)
    df = pd.DataFrame(rows).sort_values("mean_turnover_cr", ascending=False).reset_index(drop=True)
    df.to_csv(OUT / "stocks100_rank.csv", index=False)
    top = df.head(100)
    cl = top[top.close_25aug.notna()][["symbol", "close_25aug"]].copy()
    cl["date"] = "2026-08-25"
    cl.rename(columns={"close_25aug": "cas_close"})[["symbol", "date", "cas_close"]].to_csv(OUT / "stock_close.csv", index=False)
    json.dump(dict(zip(top.symbol, top.security_id)), open(OUT / "top100.json", "w"), indent=0)
    print(f"\nranked {len(df)}; top 100 saved. Top 15 by Aug mean daily turnover (Rs cr):")
    print(top.head(15)[["symbol", "mean_turnover_cr", "close_25aug"]].to_string(index=False))


if __name__ == "__main__":
    main()
