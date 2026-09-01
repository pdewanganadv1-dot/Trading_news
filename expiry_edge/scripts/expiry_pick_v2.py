"""Expiry pick v2 — learnings from 25 Aug (stock CAS blast) and 27 Aug (SENSEX put blast).

What changed vs sensex_expiry_pick.py (v1):
  v1 bet on the max-pain PULL (fade the move back to the pin). Both post-mortems showed the pin
  CHASES price on trend days (max pain migrated 77400->77300 on 27 Aug while price fell), and the
  auction EXTENDS the move; on 25 Aug stocks, last-hour momentum called the auction 79% vs 48% for
  max-pain pull. v2 therefore trades WITH the late trend, uses pin MIGRATION (not pin distance) as
  confirmation, never buys both sides, and enters as late as possible before the 15:15 freeze.

Modes:
  --backtest      score candidate direction signals on all Aug CAS index expiries from the local
                  rolling-option exports, then show the v2 rule's picks vs v1's on each expiry.
  (default live)  pull today's chain for --index (SENSEX/NIFTY) via the Dhan API and print the pick.

Signals evaluated at decision time T (last bar <= 15:05 IST; enter before the 15:15 freeze):
  lasthr   spot(T) - spot(T-60m)
  day      spot(T) - spot(13:00)
  last20   spot(T) - spot(T-20m)
  pinmig   maxpain(T) - maxpain(13:00)   (OI-weighted over the visible ATM+-3 strikes)
  v1pin    sign(maxpain(T) - spot(T))    (the old pull logic, for comparison)
  v2       majority vote of day, last20, pinmig; ties -> last20; all-flat -> sit out
Outcome = sign(settlement - freeze price); ticket = nearest-OTM on the chosen side at its last
traded premium <= T, settled at intrinsic on the CAS close.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOTS = {"SENSEX": 20, "NIFTY": 75}
EXPIRIES = {"NIFTY": ["2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25"],
            "SENSEX": ["2026-08-06", "2026-08-13", "2026-08-20", "2026-08-27"]}
T_DEC = dt.time(15, 5)     # decision bar (last <= this); freeze 15:15
T_EARLY = dt.time(13, 0)


def max_pain(day: pd.DataFrame, t: dt.time) -> float | None:
    """OI-based max pain over visible strikes using the last OI reading <= t."""
    snap = day[day.time <= t].sort_values("ts").groupby(["side", "strike"]).oi.last().reset_index()
    strikes = sorted(snap.strike.unique())
    if len(strikes) < 3:
        return None
    best, best_pain = None, None
    for s in strikes:
        pain = 0.0
        for r in snap.itertuples():
            if r.side == "CE" and s > r.strike:
                pain += r.oi * (s - r.strike)
            elif r.side == "PE" and s < r.strike:
                pain += r.oi * (r.strike - s)
        if best_pain is None or pain < best_pain:
            best, best_pain = s, pain
    return float(best)


def day_features(day: pd.DataFrame):
    """Everything the rule needs for one expiry day; None if the day is unusable."""
    day = day.assign(time=day.ts.dt.time)
    spot = day.groupby("ts").spot.last()
    spot.index = pd.to_datetime(spot.index)

    def spot_at(t):
        m = spot[spot.index.time <= t]
        return float(m.iloc[-1]) if len(m) else None

    t_dec_real = max(t for t in day.time.unique() if t <= T_DEC)
    s_dec = spot_at(T_DEC)
    s_13 = spot_at(T_EARLY)
    s_1h = spot_at(dt.time(14, 5))
    s_20 = spot_at(dt.time(14, 45))
    freeze = spot_at(dt.time(15, 15))
    settle = float(spot.iloc[-1])
    if None in (s_dec, s_13, s_1h, s_20, freeze):
        return None
    mp_dec = max_pain(day, T_DEC)
    mp_13 = max_pain(day, T_EARLY)
    step = float(np.median(np.diff(sorted(day.strike.unique()))))
    return {"day": day, "s_dec": s_dec, "s_13": s_13, "s_1h": s_1h, "s_20": s_20,
            "freeze": freeze, "settle": settle, "mp_dec": mp_dec, "mp_13": mp_13,
            "step": step, "t_dec_real": t_dec_real}


def signals(f, flat_pts):
    sg = lambda x: 0 if abs(x) < flat_pts else (1 if x > 0 else -1)
    return {"lasthr": sg(f["s_dec"] - f["s_1h"]), "day": sg(f["s_dec"] - f["s_13"]),
            "last20": sg(f["s_dec"] - f["s_20"]),
            "pinmig": sg((f["mp_dec"] or 0) - (f["mp_13"] or 0)) if f["mp_dec"] and f["mp_13"] else 0,
            "v1pin": sg((f["mp_dec"] or f["s_dec"]) - f["s_dec"])}


def v2_direction(sig):
    """Hybrid rule — the best of the 5 strategies on the Aug backtest (+10,500/8 expiries vs
    +6,146 pin-pull-only, +6,190 always-call, -4,673 always-put, +68 momentum-majority):
    pin MIGRATION direction when the pin moved intraday (2/2 on direction), else the classic
    pin-pull, else sit out. Momentum votes turned out to be coin flips at the INDEX level
    (they only worked cross-sectionally on stocks) and are printed as diagnostics only."""
    if sig["pinmig"] != 0:
        return sig["pinmig"], "STRONG"
    if sig["v1pin"] != 0:
        return sig["v1pin"], "OK"
    return 0, "SIT-OUT"


def ticket(f, direction):
    """Nearest-OTM on `direction` at its last premium <= T_DEC -> intrinsic at settle."""
    if direction == 0:
        return None
    day, s, settle = f["day"], f["s_dec"], f["settle"]
    side = "CE" if direction > 0 else "PE"
    ks = sorted(day.strike.unique())
    otm = [k for k in ks if (k > s if direction > 0 else k < s)]
    if not otm:
        return None
    k = otm[0] if direction > 0 else otm[-1]
    o = day[(day.side == side) & (day.strike == k) & (day.time <= T_DEC)].sort_values("ts")
    if o.empty:
        return None
    prem = float(o.close.iloc[-1])
    intr = max(0.0, (settle - k) if direction > 0 else (k - settle))
    return {"side": side, "strike": k, "premium": prem, "settle": round(intr, 2),
            "ret_pct": round((intr / prem - 1) * 100, 0) if prem > 0 else None}


def backtest():
    rows = []
    for idx, days in EXPIRIES.items():
        base = pd.read_csv(HERE / "dhan_export" / f"rolling_options_{idx}.csv", parse_dates=["ts"])
        extra = HERE / "dhan_export" / "rolling_options_SENSEX_aug27.csv"
        if idx == "SENSEX" and extra.exists():
            e = pd.read_csv(extra, parse_dates=["ts"])
            base = pd.concat([base, e[["ts", "offset", "side", "strike", "spot", "close", "volume", "oi"]]])
        for iso in days:
            day = base[base.ts.dt.date == dt.date.fromisoformat(iso)]
            if day.empty:
                print(f"{idx} {iso}: no data")
                continue
            f = day_features(day)
            if not f:
                print(f"{idx} {iso}: unusable")
                continue
            flat = 0.0004 * f["s_dec"]                     # ~0.04% = flat
            sig = signals(f, flat)
            auc = f["settle"] - f["freeze"]
            out = 0 if abs(auc) < 1e-9 else (1 if auc > 0 else -1)
            d2, grade = v2_direction(sig)
            row = {"index": idx, "date": iso, "freeze": round(f["freeze"], 1),
                   "settle": round(f["settle"], 1), "auction_pts": round(auc, 1), "outcome": out,
                   **{f"sig_{k}": v for k, v in sig.items()}, "v2_dir": d2, "v2_grade": grade}
            for name, d in [("v1", sig["v1pin"]), ("v2", d2)]:
                tk = ticket(f, d)
                row[f"{name}_ticket"] = (f"{tk['side']} {tk['strike']:.0f} @ {tk['premium']:.2f} -> "
                                         f"{tk['settle']:.2f} ({tk['ret_pct']:+.0f}%)") if tk else "sit out"
                row[f"{name}_ret"] = tk["ret_pct"] if tk else 0.0
                row[f"{name}_pnl_lot"] = round((tk["settle"] - tk["premium"]) * LOTS[idx], 0) if tk else 0.0
            rows.append(row)
    t = pd.DataFrame(rows)
    outdir = ROOT / "outputs" / "model"
    t.to_csv(outdir / "pick_v2_backtest.csv", index=False)

    print("=== per-expiry ===")
    for r in t.itertuples():
        print(f"{r.index:6s} {r.date}  auction {r.auction_pts:+8.1f}  "
              f"v1: {r.v1_ticket:38s} v2[{r.v2_grade:6s}]: {r.v2_ticket}")
    print("\n=== direction hit-rate vs auction sign (n with a call) ===")
    summary = {}
    for c in ("sig_lasthr", "sig_day", "sig_last20", "sig_pinmig", "sig_v1pin", "v2_dir"):
        m = t[(t[c] != 0) & (t.outcome != 0)]
        hits = int((m[c] == m.outcome).sum())
        summary[c] = {"hits": hits, "n": len(m)}
        print(f"  {c:12s} {hits}/{len(m)}")
    for name in ("v1", "v2"):
        tot = float(t[f"{name}_pnl_lot"].sum())
        wins = int((t[f"{name}_ret"] > 0).sum())
        traded = int((t[f"{name}_ticket"] != "sit out").sum())
        summary[name] = {"pnl_per_1lot_rs": tot, "wins": wins, "traded": traded}
        print(f"\n{name}: traded {traded}/8, wins {wins}, total P&L (1 lot each) Rs {tot:+,.0f}")
    (outdir / "pick_v2_backtest.json").write_text(json.dumps(summary, indent=2, default=float))
    print("\nwrote outputs/model/pick_v2_backtest.csv + .json")


def live(index):
    import base64
    import os
    import time as _time
    import requests

    tok = os.environ["DHAN_ACCESS_TOKEN"]
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        p = tok.split(".")[1]
        cid = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["dhanClientId"]
    H = {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}
    ids = {"SENSEX": ("51", "BSE_FNO"), "NIFTY": ("13", "NSE_FNO")}
    sid, seg = ids[index]
    today = (dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)).date()
    rows = {}
    for strike in ["ATM-3", "ATM-2", "ATM-1", "ATM", "ATM+1", "ATM+2", "ATM+3"]:
        for side in ("CALL", "PUT"):
            for i in range(4):
                r = requests.post("https://api.dhan.co/v2/charts/rollingoption", headers=H, timeout=60,
                                  json={"exchangeSegment": seg, "interval": "5", "securityId": sid,
                                        "instrument": "OPTIDX", "expiryCode": 0, "expiryFlag": "WEEK",
                                        "strike": strike, "drvOptionType": side,
                                        "requiredData": ["close", "volume", "strike", "oi", "spot"],
                                        "fromDate": today.isoformat(),
                                        "toDate": (today + dt.timedelta(days=1)).isoformat()})
                if r.status_code == 200:
                    break
                if r.status_code == 429:
                    _time.sleep(3 + 2 * i)
                    continue
                if r.status_code in (401, 403):
                    sys.exit(f"[STOP] HTTP {r.status_code}: token expired/invalid or Data API disabled; "
                             "regenerate on Dhan.")
                break  # e.g. DH-905 on this expiryCode -> try the next one
            j = r.json()
            for key in ("ce", "pe"):
                blk = (j.get("data") or {}).get(key)
                if not blk or not blk.get("timestamp"):
                    continue
                n = len(blk["timestamp"])
                g = lambda k: (blk.get(k) or [None] * n)
                for i in range(n):
                    t = dt.datetime.utcfromtimestamp(int(blk["timestamp"][i])) + dt.timedelta(hours=5, minutes=30)
                    rows[(t, strike, key.upper())] = [t, key.upper(), g("strike")[i], g("spot")[i],
                                                      g("close")[i], g("oi")[i]]
    day = pd.DataFrame(rows.values(), columns=["ts", "side", "strike", "spot", "close", "oi"])
    if day.empty:
        sys.exit("[STOP] no option data returned — is today an expiry/trading day?")
    f = day_features(day)
    if not f:
        sys.exit("[STOP] not enough bars yet (need 13:00 onward). Run after ~14:10 IST.")
    sig = signals(f, 0.0004 * f["s_dec"])
    d2, grade = v2_direction(sig)
    tk = ticket(f, d2)
    lot = LOTS[index]
    now = day.ts.max()
    print("=" * 66)
    print(f" {index} expiry pick v2 (momentum, not pin)  |  data to {now}")
    print("=" * 66)
    print(f" spot: {f['s_dec']:.1f} @ {f['t_dec_real']}   13:00: {f['s_13']:.1f}   "
          f"14:05: {f['s_1h']:.1f}   14:45: {f['s_20']:.1f}")
    print(f" max-pain now: {f['mp_dec']}   at 13:00: {f['mp_13']}   "
          f"(migration {'' if not (f['mp_dec'] and f['mp_13']) else f['mp_dec'] - f['mp_13']:+.0f} pts)")
    print(f" votes  day: {sig['day']:+d}  last20: {sig['last20']:+d}  pinmig: {sig['pinmig']:+d}"
          f"   (old v1 pin-pull would say: {sig['v1pin']:+d})")
    print("-" * 66)
    if d2 == 0 or not tk:
        print(" PICK : SIT OUT — pin stable and at spot; no migration, no pull.")
        print(f"\nSUMMARY: SIT-OUT | spot {f['s_dec']:.0f}")
    else:
        dist = abs(tk["strike"] - f["s_dec"])
        far = dist > 0.0015 * f["s_dec"]        # ~1.5x the month's median auction move
        tag = f"{grade}{'/FAR-STRIKE' if far else ''}"
        print(f" PICK : {tag}  {index} {tk['strike']:.0f} {tk['side']}  premium (last) = Rs {tk['premium']:.2f}")
        print(f"        strike is {dist:.0f} pts from spot ({dist / f['s_dec'] * 100:.2f}%) — Aug auctions "
              f"median ~0.15% of spot; FAR-STRIKE = needs an outsized auction, treat as pure lottery")
        print(f"        1 lot ({lot}) = Rs {tk['premium'] * lot:,.0f}  — total-loss sizing, enter before 15:15 freeze")
        print(" STRONG = pin migrated intraday (trade its direction); OK = classic pin-pull only.")
        print(f"\nSUMMARY: {tag} {index} {tk['strike']:.0f} {tk['side']} @ Rs {tk['premium']:.2f} "
              f"| spot {f['s_dec']:.0f} | 1 lot Rs {tk['premium'] * lot:,.0f}")
    print("\n Backtest (8 Aug CAS expiries): see outputs/model/pick_v2_backtest.csv — v2 traded with the")
    print(" late trend; v1's pin-pull was a coin flip and lost on 27 Aug (bought the CALL into a put blast).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--index", default="SENSEX", choices=["SENSEX", "NIFTY"])
    a = ap.parse_args()
    backtest() if a.backtest else live(a.index)
