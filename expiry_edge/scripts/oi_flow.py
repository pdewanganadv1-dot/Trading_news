"""Live OI-flow readout for an expiry afternoon: who is opening/closing near ATM in the last hour.

For each strike ATM+-2, classifies the last hour's (premium, OI) move:
  LONG-BUILDUP  prem up,  OI up    (buyers opening)
  SHORT-BUILDUP prem down, OI up   (writers opening — pressing)
  LONG-UNWIND   prem down, OI down (buyers exiting)
  SHORT-COVER   prem up,  OI down  (writers buying back — 4/6 winning legs in Aug had this at entry)
Aug 2026 findings (outputs/model/oi_buildup_vs_strategy.csv): 17/24 bought legs sat against
seller-friendly flow — that is WHY tickets are cheap; SHORT-COVER at entry was the best state;
chain-wide OI growth on BOTH sides late (only BANKEX 27 Aug) preceded the -837 pt blast.

Usage: python3 oi_flow.py --index NIFTY
"""
import argparse
import base64
import datetime as dt
import json
import os
import sys
import time as _t

import pandas as pd
import requests

IDS = {"NIFTY": ("13", "NSE_FNO"), "SENSEX": ("51", "BSE_FNO"), "BANKNIFTY": ("25", "NSE_FNO"),
       "FINNIFTY": ("27", "NSE_FNO"), "MIDCPNIFTY": ("442", "NSE_FNO"), "BANKEX": ("69", "BSE_FNO")}


def classify(dp, doi):
    if doi > 0:
        return "LONG-BUILDUP " if dp > 0 else "SHORT-BUILDUP"
    if doi < 0:
        return "SHORT-COVER  " if dp > 0 else "LONG-UNWIND  "
    return "flat         "


def main(index):
    tok = os.environ["DHAN_ACCESS_TOKEN"]
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        p = tok.split(".")[1]
        cid = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["dhanClientId"]
    H = {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}
    sid, seg = IDS[index]
    today = (dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)).date()
    rows = []
    for strike in ["ATM-2", "ATM-1", "ATM", "ATM+1", "ATM+2"]:
        for side in ("CALL", "PUT"):
            got = None
            for ecode in (0, 1):
                for i in range(4):
                    r = requests.post("https://api.dhan.co/v2/charts/rollingoption", headers=H, timeout=60,
                                      json={"exchangeSegment": seg, "interval": "5", "securityId": sid,
                                            "instrument": "OPTIDX", "expiryCode": ecode, "expiryFlag": "WEEK",
                                            "strike": strike, "drvOptionType": side,
                                            "requiredData": ["close", "strike", "oi", "spot", "iv"],
                                            "fromDate": today.isoformat(),
                                            "toDate": (today + dt.timedelta(days=1)).isoformat()})
                    if r.status_code == 200:
                        break
                    if r.status_code == 429:
                        _t.sleep(3 + 2 * i)
                        continue
                    if r.status_code in (401, 403):
                        sys.exit("[STOP] 401/403 — token dead; regenerate on Dhan.")
                    break
                if r.status_code == 200 and (r.json().get("data") or {}):
                    got = r.json()["data"]
                    break
            if not got:
                continue
            for key in ("ce", "pe"):
                blk = got.get(key)
                if not blk or not blk.get("timestamp"):
                    continue
                n = len(blk["timestamp"])
                g = lambda k: (blk.get(k) or [None] * n)
                for i in range(n):
                    ts = dt.datetime.utcfromtimestamp(int(blk["timestamp"][i])) + dt.timedelta(hours=5, minutes=30)
                    rows.append([ts, key.upper(), g("strike")[i], g("spot")[i], g("close")[i], g("oi")[i], g("iv")[i]])
    d = pd.DataFrame(rows, columns=["ts", "side", "strike", "spot", "close", "oi", "iv"]).drop_duplicates(["ts", "side", "strike"])
    if d.empty:
        sys.exit("[STOP] no bars — market open? expiry day?")
    now = d.ts.max()
    t0 = now - dt.timedelta(hours=1)
    spot = float(d[d.ts == now].spot.iloc[0])
    print(f"{index} OI flow, {t0.time()} -> {now.time()}  spot {spot:.1f}")
    both_up = {"CE": 0.0, "PE": 0.0}
    for side in ("CE", "PE"):
        for k in sorted(d[d.side == side].strike.unique()):
            o = d[(d.side == side) & (d.strike == k)].sort_values("ts")
            w0 = o[o.ts <= t0]
            if w0.empty:
                continue
            dp = float(o.close.iloc[-1]) - float(w0.close.iloc[-1])
            doi = float(o.oi.iloc[-1]) - float(w0.oi.iloc[-1])
            pct = doi / max(float(w0.oi.iloc[-1]), 1) * 100
            both_up[side] += doi
            mark = " <== ATM-adjacent" if abs(k - spot) <= abs(sorted(d.strike.unique(), key=lambda x: abs(x - spot))[1] - spot) else ""
            print(f"  {side} {k:>9.0f}  {classify(dp, doi)} prem {dp:+8.2f}  OI {pct:+6.1f}%{mark}")
    if both_up["CE"] > 0 and both_up["PE"] > 0:
        print("\n  !! chain OI GROWING on BOTH sides late — writers overcommitted (BANKEX-27-Aug pattern):"
              "\n     blast fuel present, upgrade conviction on a BUY verdict.")
    elif both_up["CE"] < 0 and both_up["PE"] < 0:
        print("\n  chain OI shrinking both sides — normal expiry-day unwind, no extra signal.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="NIFTY", choices=list(IDS))
    main(ap.parse_args().index)
