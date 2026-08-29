"""No-prediction CAS strangle tool — buy BOTH nearest-OTM CE and PE just before the 15:15
freeze on an index expiry day, hold to the auction settlement.

Encodes the three lessons from the Aug 2026 backtest (12 CAS index expiries):
  1. ENTRY AT 15:05 — tested 15:10 too: it LOST money relative to 15:05 (+62.7k vs +129.0k
     unfiltered on the Aug set) because the pre-freeze drift usually continues into the auction;
     entering earlier captures drift + auction. Override with --entry HH:MM to re-test.
  2. SKIP EXPENSIVE — combined premium > 0.25% of spot loses even on big auctions
     (6 Aug SENSEX: +169 pt auction, 404 pts of premium, -62%).
  3. SKIP BAD GEOMETRY — when even the easier side needs > 0.35% of spot to break even
     (premium + strike distance), the trade needs an above-p75 auction. Aug reference:
     median |auction| 0.25%, p75 0.31%, max 1.22% (BANKEX).
Budget sizing: lots = budget // (combined premium x lot).

Usage:
  python3 strangle_1505.py --index SENSEX [--budget 20000]     live, on an expiry day ~15:10 IST
  python3 strangle_1505.py --backtest [--budget 20000]         score the rule on all 12 Aug expiries
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOTS = {"NIFTY": 65, "SENSEX": 20, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120, "BANKEX": 30}
IDS = {"NIFTY": ("13", "NSE_FNO"), "SENSEX": ("51", "BSE_FNO"), "BANKNIFTY": ("25", "NSE_FNO"),
       "FINNIFTY": ("27", "NSE_FNO"), "MIDCPNIFTY": ("442", "NSE_FNO"), "BANKEX": ("69", "BSE_FNO")}
T_ENTRY = dt.time(15, 5)           # 15:05 beat 15:10 in backtest: pre-freeze drift continues into the auction
MAX_PREM_PCT = 0.25                # lesson 2
MAX_NEED_PCT = 0.35                # lesson 3
EXPIRIES = [("NIFTY", "2026-08-04"), ("SENSEX", "2026-08-06"), ("NIFTY", "2026-08-11"),
            ("SENSEX", "2026-08-13"), ("NIFTY", "2026-08-18"), ("SENSEX", "2026-08-20"),
            ("BANKNIFTY", "2026-08-25"), ("FINNIFTY", "2026-08-25"), ("MIDCPNIFTY", "2026-08-25"),
            ("NIFTY", "2026-08-25"), ("BANKEX", "2026-08-27"), ("SENSEX", "2026-08-27")]


def evaluate(day: pd.DataFrame, budget: int, lot: int):
    """One expiry day's bars -> verdict + trade + (if settled) outcome."""
    day = day.assign(time=day.ts.dt.time)
    pre = day[day.time <= T_ENTRY]
    if pre.empty:
        return None
    spot = float(pre.sort_values("ts").spot.iloc[-1])
    settle = float(day.sort_values("ts").spot.iloc[-1])
    ks = sorted(day.strike.unique())
    try:
        ce_k = min(k for k in ks if k > spot)
        pe_k = max(k for k in ks if k < spot)
    except ValueError:
        return None

    def prem(side, k):
        o = pre[(pre.side == side) & (pre.strike == k)].sort_values("ts")
        return float(o.close.iloc[-1]) if len(o) else None

    ce_p, pe_p = prem("CE", ce_k), prem("PE", pe_k)
    if ce_p is None or pe_p is None:
        return None
    comb = ce_p + pe_p
    need_up = (ce_k - spot) + comb
    need_dn = (spot - pe_k) + comb
    prem_pct = comb / spot * 100
    need_pct = min(need_up, need_dn) / spot * 100
    if prem_pct > MAX_PREM_PCT:
        verdict = "SKIP-EXPENSIVE"
    elif need_pct > MAX_NEED_PCT:
        verdict = "SKIP-GEOMETRY"
    else:
        verdict = "BUY"
    lots = int(budget // (comb * lot)) if comb > 0 else 0
    ce_i = max(0.0, settle - ce_k)
    pe_i = max(0.0, pe_k - settle)
    return {"spot": spot, "settle": settle, "ce_k": ce_k, "pe_k": pe_k, "ce_p": ce_p, "pe_p": pe_p,
            "comb": comb, "prem_pct": prem_pct, "need_up": need_up, "need_dn": need_dn,
            "need_pct": need_pct, "verdict": verdict, "lots": lots,
            "deployed": round(lots * comb * lot), "received": round(lots * (ce_i + pe_i) * lot),
            "net": round(lots * (ce_i + pe_i - comb) * lot)}


def load(idx):
    if idx in ("NIFTY", "SENSEX"):
        d = pd.read_csv(HERE / "dhan_export" / f"rolling_options_{idx}.csv", parse_dates=["ts"])
        if idx == "SENSEX":
            e = pd.read_csv(HERE / "dhan_export" / "rolling_options_SENSEX_aug27.csv", parse_dates=["ts"])
            d = pd.concat([d, e[["ts", "offset", "side", "strike", "spot", "close", "volume", "oi"]]])
        return d
    f = HERE / "dhan_export" / f"rolling_options_{idx}_monthly.csv"
    return pd.read_csv(f if f.exists() else HERE / "dhan_export" / f"rolling_options_{idx}.csv",
                       parse_dates=["ts"])


def backtest(budget):
    rows = []
    for idx, iso in EXPIRIES:
        d = load(idx)
        day = d[d.ts.dt.date == dt.date.fromisoformat(iso)]
        r = evaluate(day, budget, LOTS[idx])
        if not r:
            print(f"{idx} {iso}: unusable")
            continue
        rows.append({"index": idx, "date": iso, "auction": round(r["settle"] - r["spot"], 1), **r})
        t = rows[-1]
        traded = t["verdict"] == "BUY"
        print(f"{idx:10s} {iso}  {t['verdict']:14s} prem {t['prem_pct']:.2f}%  need {t['need_pct']:.2f}%  "
              f"{t['lots'] if traded else 0:>2d} lots  net {t['net'] if traded else 0:+8,}")
    t = pd.DataFrame(rows)
    t["taken_net"] = t.net.where(t.verdict == "BUY", 0)
    t["taken_dep"] = t.deployed.where(t.verdict == "BUY", 0)
    t.to_csv(ROOT / "outputs" / "model" / "strangle_filtered_backtest.csv", index=False)
    print(f"\nFILTERED  : {int((t.verdict == 'BUY').sum())}/12 taken, deployed Rs {t.taken_dep.sum():,}, "
          f"net Rs {t.taken_net.sum():+,}")
    print(f"UNFILTERED: 12/12 taken, deployed Rs {t.deployed.sum():,}, net Rs {t.net.sum():+,}")
    skipped = t[t.verdict != "BUY"]
    if len(skipped):
        print("skipped: " + "; ".join(f"{r.index_} {r.date} ({r.verdict}, would-be {r.net:+,})"
                                      for r in skipped.rename(columns={'index': 'index_'}).itertuples()))


def live(index, budget):
    import base64
    import os
    import time as _t
    import requests

    tok = os.environ["DHAN_ACCESS_TOKEN"]
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        p = tok.split(".")[1]
        cid = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["dhanClientId"]
    H = {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}
    sid, seg = IDS[index]
    today = (dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)).date()
    rows = {}
    for strike in ["ATM-2", "ATM-1", "ATM", "ATM+1", "ATM+2"]:
        for side in ("CALL", "PUT"):
            got = None
            for ecode in (0, 1):
                for i in range(4):
                    r = requests.post("https://api.dhan.co/v2/charts/rollingoption", headers=H, timeout=60,
                                      json={"exchangeSegment": seg, "interval": "5", "securityId": sid,
                                            "instrument": "OPTIDX", "expiryCode": ecode, "expiryFlag": "WEEK",
                                            "strike": strike, "drvOptionType": side,
                                            "requiredData": ["open", "high", "low", "close", "iv", "volume",
                                                             "strike", "oi", "spot"],
                                            "fromDate": today.isoformat(),
                                            "toDate": (today + dt.timedelta(days=1)).isoformat()})
                    if r.status_code == 200:
                        break
                    if r.status_code == 429:
                        _t.sleep(3 + 2 * i)
                        continue
                    if r.status_code in (401, 403):
                        sys.exit("[STOP] 401/403 — token expired/invalid or Data API off. Regenerate on Dhan.")
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
                    rows[(ts, strike, key.upper())] = [ts, key.upper(), g("strike")[i], g("spot")[i], g("close")[i]]
    day = pd.DataFrame(rows.values(), columns=["ts", "side", "strike", "spot", "close"])
    if day.empty:
        sys.exit("[STOP] no option bars — expiry day? market open?")
    lot = LOTS[index]
    r = evaluate(day, budget, lot)
    if not r:
        sys.exit("[STOP] could not price both legs.")
    print("=" * 66)
    print(f" {index} CAS strangle  |  bars to {day.ts.max()}  |  budget Rs {budget:,}")
    print("=" * 66)
    print(f" spot {r['spot']:.1f}   legs: {r['ce_k']:.0f} CE @ {r['ce_p']:.2f}  +  {r['pe_k']:.0f} PE @ {r['pe_p']:.2f}")
    print(f" combined {r['comb']:.2f} pts = {r['prem_pct']:.2f}% of spot (limit {MAX_PREM_PCT}%)")
    print(f" breakeven: +{r['need_up']:.0f} up / -{r['need_dn']:.0f} down; easier side {r['need_pct']:.2f}% "
          f"(limit {MAX_NEED_PCT}%; Aug median auction 0.25%, max 1.22%)")
    print("-" * 66)
    if r["verdict"] != "BUY":
        print(f" VERDICT: {r['verdict']} — do not take it today.")
        print(f"\nSUMMARY: {r['verdict']} {index} strangle | prem {r['prem_pct']:.2f}% need {r['need_pct']:.2f}%")
    else:
        cost = round(r["comb"] * lot)
        print(f" VERDICT: BUY {r['lots']} lot(s) of each leg  (Rs {cost:,}/strangle-lot, "
              f"total Rs {r['deployed']:,}) — hold to 15:30 settlement, total-loss sizing.")
        print(f"\nSUMMARY: BUY {index} {r['ce_k']:.0f}CE @ {r['ce_p']:.2f} + {r['pe_k']:.0f}PE @ {r['pe_p']:.2f} "
              f"x{r['lots']} lots = Rs {r['deployed']:,}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--index", default="SENSEX", choices=list(IDS))
    ap.add_argument("--budget", type=int, default=20000)
    ap.add_argument("--entry", default=None, help="HH:MM entry bar override (default 15:05)")
    a = ap.parse_args()
    if a.entry:
        h, mm = a.entry.split(":")
        T_ENTRY = dt.time(int(h), int(mm))
        import sys as _s
        _s.modules[__name__].T_ENTRY = T_ENTRY
    backtest(a.budget) if a.backtest else live(a.index, a.budget)
