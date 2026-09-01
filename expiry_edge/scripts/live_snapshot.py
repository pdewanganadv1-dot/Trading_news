"""Live expiry-day snapshot via the option-chain endpoint (works mid-session, full chain).

Prints spot, FULL-CHAIN max pain, nearest-OTM legs with premiums/OI, the strangle gates and
verdict (same thresholds as strangle_1505.py), and saves the whole chain state to a JSON so
later snapshots can compute pin migration and per-strike OI deltas.

Usage: python3 live_snapshot.py --index NIFTY [--budget 20000] [--tag 1300]
"""
import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
IDS = {"NIFTY": 13, "SENSEX": 51, "BANKNIFTY": 25, "FINNIFTY": 27, "MIDCPNIFTY": 442, "BANKEX": 69}
LOTS = {"NIFTY": 65, "SENSEX": 20, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120, "BANKEX": 30}
MAX_PREM_PCT, MAX_NEED_PCT = 0.25, 0.35


def main(index, budget, tag):
    tok = os.environ["DHAN_ACCESS_TOKEN"]
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        p = tok.split(".")[1]
        cid = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["dhanClientId"]
    H = {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}
    B = "https://api.dhan.co/v2"
    r = requests.post(f"{B}/optionchain/expirylist", headers=H,
                      json={"UnderlyingScrip": IDS[index], "UnderlyingSeg": "IDX_I"}, timeout=30)
    if r.status_code in (401, 403):
        sys.exit("[STOP] 401/403 — token expired/invalid or Data API off. Regenerate on Dhan.")
    expiry = r.json()["data"][0]
    time.sleep(3)
    r = requests.post(f"{B}/optionchain", headers=H,
                      json={"UnderlyingScrip": IDS[index], "UnderlyingSeg": "IDX_I", "Expiry": expiry},
                      timeout=60)
    data = r.json()["data"]
    spot = float(data["last_price"])
    oc = data["oc"]
    chain = {}
    for ks, e in oc.items():
        k = float(ks)
        chain[k] = {"ce": float((e.get("ce") or {}).get("last_price") or 0),
                    "pe": float((e.get("pe") or {}).get("last_price") or 0),
                    "ce_oi": float((e.get("ce") or {}).get("oi") or 0),
                    "pe_oi": float((e.get("pe") or {}).get("oi") or 0)}
    strikes = sorted(chain)
    # full-chain max pain
    best, best_pain = None, None
    for s in strikes:
        pain = sum(chain[k]["ce_oi"] * (s - k) for k in strikes if s > k) + \
               sum(chain[k]["pe_oi"] * (k - s) for k in strikes if s < k)
        if best_pain is None or pain < best_pain:
            best, best_pain = s, pain
    mp = best
    ce_k = min(k for k in strikes if k > spot)
    pe_k = max(k for k in strikes if k < spot)
    ce_p, pe_p = chain[ce_k]["ce"], chain[pe_k]["pe"]
    comb = ce_p + pe_p
    prem_pct = comb / spot * 100
    need_up = (ce_k - spot) + comb
    need_dn = (spot - pe_k) + comb
    need_pct = min(need_up, need_dn) / spot * 100
    verdict = ("SKIP-EXPENSIVE" if prem_pct > MAX_PREM_PCT
               else "SKIP-GEOMETRY" if need_pct > MAX_NEED_PCT else "BUY")
    lot = LOTS[index]
    lots = int(budget // (comb * lot)) if comb > 0 else 0
    now = (dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)).strftime("%H:%M IST")

    print(f"{index} live snapshot {now} | expiry {expiry}")
    print(f"  spot {spot:.1f}   FULL-CHAIN max-pain {mp:.0f}   (pin dist {mp - spot:+.0f} pts)")
    print(f"  legs: {ce_k:.0f} CE @ {ce_p:.2f} (OI {chain[ce_k]['ce_oi']/1e5:.1f}L) + "
          f"{pe_k:.0f} PE @ {pe_p:.2f} (OI {chain[pe_k]['pe_oi']/1e5:.1f}L)")
    print(f"  combined {comb:.2f} = {prem_pct:.3f}% of spot (cap {MAX_PREM_PCT}%)   "
          f"breakeven +{need_up:.0f}/-{need_dn:.0f} (easier {need_pct:.3f}%, cap {MAX_NEED_PCT}%)")
    print(f"  STRANGLE VERDICT: {verdict}" + (f" — {lots} lots each leg = Rs {lots * comb * lot:,.0f}"
                                              if verdict == "BUY" else ""))
    # compare with earlier snapshot for pin migration + OI deltas
    snapdir = HERE / "live_snaps"
    snapdir.mkdir(exist_ok=True)
    today = dt.date.today().isoformat()
    prev = sorted(snapdir.glob(f"{index}_{today}_*.json"))
    if prev:
        old = json.loads(prev[0].read_text())
        dmp = mp - old["max_pain"]
        print(f"  vs {old['tag']}: pin {old['max_pain']:.0f} -> {mp:.0f} ({dmp:+.0f}), "
              f"spot {old['spot']:.1f} -> {spot:.1f} ({spot - old['spot']:+.1f})")
        if abs(dmp) >= (strikes[1] - strikes[0]):
            direction = "UP" if dmp > 0 else "DOWN"
            print(f"  *** PIN MIGRATING {direction} — that side is the STRONG directional pick ***")
        else:
            print("  no pin migration so far")
        tot_ce_old = sum(v["ce_oi"] for v in old["chain"].values())
        tot_pe_old = sum(v["pe_oi"] for v in old["chain"].values())
        tot_ce = sum(v["ce_oi"] for v in chain.values())
        tot_pe = sum(v["pe_oi"] for v in chain.values())
        print(f"  chain OI since {old['tag']}: CE {(tot_ce/tot_ce_old-1)*100:+.1f}%  PE {(tot_pe/tot_pe_old-1)*100:+.1f}%"
              + ("   !! growing BOTH sides — blast-fuel flag" if tot_ce > tot_ce_old and tot_pe > tot_pe_old else ""))
    (snapdir / f"{index}_{today}_{tag}.json").write_text(
        json.dumps({"tag": tag, "ts": now, "spot": spot, "max_pain": mp, "expiry": expiry,
                    "chain": {str(k): v for k, v in chain.items()}}, default=float))
    print(f"\nSUMMARY: {verdict} {index} | spot {spot:.0f} pin {mp:.0f} | "
          f"{ce_k:.0f}CE @{ce_p:.2f} + {pe_k:.0f}PE @{pe_p:.2f} = {prem_pct:.2f}%"
          + (f" | {lots} lots Rs {lots * comb * lot:,.0f}" if verdict == "BUY" else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="NIFTY", choices=list(IDS))
    ap.add_argument("--budget", type=int, default=20000)
    ap.add_argument("--tag", default=dt.datetime.now().strftime("%H%M"))
    a = ap.parse_args()
    main(a.index, a.budget, a.tag)
