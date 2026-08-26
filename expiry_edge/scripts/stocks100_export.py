"""Export expiry-week rolling option bars (Dhan v2 rollingoption) for the top-100 liquid F&O stocks.

Reads top100.json from stocks100_rank.py; writes rolling_options_<SYM>.csv (same schema as dhan_export.py)
into dhan_export_stocks100/.  Window 2026-08-20 .. 2026-08-26 (toDate exclusive) covers the run-in and the
25 Aug monthly expiry — the first stock-option settlement under CAS.  Resume-safe: existing files are skipped.
"""
import base64
import datetime as dt
import json
import os
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "dhan_export_stocks100"
BASE = "https://api.dhan.co/v2"
FROM, TO = "2026-08-20", "2026-08-26"
STRIKES = ["ATM-3", "ATM-2", "ATM-1", "ATM", "ATM+1", "ATM+2", "ATM+3"]

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


def to_ist(ts):
    return (dt.datetime.utcfromtimestamp(int(ts)) + dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")


def export_stock(sym, sid):
    rows = {}
    for strike in STRIKES:
        for side in ("CALL", "PUT"):
            j = None
            for ecode in (0, 1):
                j = post("charts/rollingoption", {"exchangeSegment": "NSE_FNO", "interval": "5", "securityId": sid,
                                                  "instrument": "OPTSTK", "expiryCode": ecode, "expiryFlag": "MONTH",
                                                  "strike": strike, "drvOptionType": side,
                                                  "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                                                  "fromDate": FROM, "toDate": TO})
                if j and j.get("data"):
                    break
            if not (j and j.get("data")):
                continue
            for key in ("ce", "pe"):
                blk = j["data"].get(key)
                if not blk or not blk.get("timestamp"):
                    continue
                n = len(blk["timestamp"])
                g = lambda k: (blk.get(k) or [None] * n)  # noqa: E731
                for i in range(n):
                    t = to_ist(blk["timestamp"][i])
                    rows[(t, strike, key.upper())] = [t, strike, key.upper(), g("strike")[i], g("spot")[i], g("open")[i],
                                                      g("high")[i], g("low")[i], g("close")[i], g("iv")[i], g("volume")[i], g("oi")[i]]
            time.sleep(0.35)
    p = OUT / f"rolling_options_{sym}.csv"
    with open(p, "w") as fh:
        fh.write("ts,offset,side,strike,spot,open,high,low,close,iv,volume,oi\n")
        for r in rows.values():
            fh.write(",".join("" if v is None else str(v) for v in r) + "\n")
    return len(rows)


def main():
    top = json.load(open(OUT / "top100.json"))
    t0 = time.time()
    for n, (sym, sid) in enumerate(top.items(), 1):
        f = OUT / f"rolling_options_{sym}.csv"
        if f.exists() and f.stat().st_size > 100:
            print(f"[{n}/100] {sym}: exists, skip")
            continue
        nrows = export_stock(sym, sid)
        print(f"[{n}/100] {sym}: {nrows} rows  ({time.time() - t0:.0f}s elapsed)", flush=True)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
