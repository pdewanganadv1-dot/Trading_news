#!/usr/bin/env python3
"""
sensex_expiry_pick.py -- live SENSEX expiry-day OTM pick from YOUR Dhan option chain.

Pulls the real SENSEX option chain from your Dhan account, computes max-pain, and
prints the exact nearest-OTM strike to buy (toward the pin) and its live premium (LTP).
Buyer-only, cheap-OTM, CAS-expiry logic. This is NOT advice -- read the caveat it prints.

RUN IT IN YOUR DHAN-ENABLED ENVIRONMENT (the one whose network can reach api.dhan.co):

    export DHAN_ACCESS_TOKEN="<your current, valid Dhan token>"
    export DHAN_CLIENT_ID="<your Dhan client id>"
    python3 sensex_expiry_pick.py

Optional env overrides:
    EE_SECURITY_ID   default 51 = SENSEX index (IDX_I).  13=NIFTY, 25=BANKNIFTY.
    EE_BAND_PTS      default 60.  Sit-out zone: if |spot - max_pain| <= band, no edge.

Zero third-party deps (uses only the Python standard library).
"""
import os, sys, json, time, datetime
import urllib.request, urllib.error

API = "https://api.dhan.co/v2"
SEG = "IDX_I"
SECURITY_ID = int(os.environ.get("EE_SECURITY_ID", "51"))    # 51 = SENSEX
BAND_PTS = float(os.environ.get("EE_BAND_PTS", "60"))

TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
CLIENT = os.environ.get("DHAN_CLIENT_ID", "").strip()


def _post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("access-token", TOKEN)
    if CLIENT:
        req.add_header("client-id", CLIENT)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode()
        except Exception:
            msg = ""
        return None, "HTTP %s: %s" % (e.code, msg[:400])
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def die(msg):
    print("\n[STOP] " + msg)
    sys.exit(1)


if not TOKEN:
    die("DHAN_ACCESS_TOKEN is not set. `export DHAN_ACCESS_TOKEN=...` first.")
if not CLIENT:
    print("[warn] DHAN_CLIENT_ID not set -- the option-chain endpoint usually needs it. "
          "If you get HTTP 401/400, set it and rerun.")

# --- 1) expiry list -------------------------------------------------------
el, err = _post("/optionchain/expirylist", {"UnderlyingScrip": SECURITY_ID, "UnderlyingSeg": SEG})
if err:
    hint = ""
    if "401" in err or "403" in err:
        hint = ("\n  -> token invalid/expired, OR your account's DATA API is not enabled "
                "(it is a separate switch from the trading API). Regenerate the token / enable Data API on Dhan.")
    elif "429" in err or "Rate" in err or "DH-90" in err:
        hint = "\n  -> rate limited (1 req / 3s). Wait a few seconds and rerun."
    die("expiry-list call failed: " + err + hint)

expiries = el if isinstance(el, list) else (el.get("data") or el.get("Data") or [])
if not expiries:
    die("no expiries returned. Check EE_SECURITY_ID (51=SENSEX) and that the Data API is enabled. "
        "Raw: " + json.dumps(el)[:300])

today = datetime.date.today().isoformat()
future = sorted([e for e in expiries if str(e) >= today]) or sorted(map(str, expiries))
expiry = future[0]

# --- 2) option chain (respect 1-req / 3s rate limit) ----------------------
time.sleep(3.2)
resp, err = _post("/optionchain", {"UnderlyingScrip": SECURITY_ID, "UnderlyingSeg": SEG, "Expiry": expiry})
if err:
    die("option-chain call failed: " + err + "  (wait 3s between calls; recheck token / Data API)")

d = resp.get("data") if isinstance(resp, dict) else None
d = d or {}
spot = float(d.get("last_price") or 0)
oc = d.get("oc") or {}
if not spot or not oc:
    die("empty chain returned. Raw: " + json.dumps(resp)[:300])

rows = []
for k, v in oc.items():
    try:
        strike = float(k)
    except Exception:
        continue
    ce = (v or {}).get("ce") or {}
    pe = (v or {}).get("pe") or {}
    rows.append((strike,
                 float(ce.get("oi") or 0), float(ce.get("last_price") or 0),
                 float(pe.get("oi") or 0), float(pe.get("last_price") or 0)))
rows.sort()
if not rows:
    die("no strikes parsed from the chain.")

strikes = [r[0] for r in rows]
ce_oi = {r[0]: r[1] for r in rows}
ce_ltp = {r[0]: r[2] for r in rows}
pe_oi = {r[0]: r[3] for r in rows}
pe_ltp = {r[0]: r[4] for r in rows}


def pain(P):
    tot = 0.0
    for s in strikes:
        if P > s:
            tot += ce_oi[s] * (P - s)     # calls in the money
        elif P < s:
            tot += pe_oi[s] * (s - P)     # puts in the money
    return tot


max_pain = min(strikes, key=pain)
tot_ce = sum(ce_oi.values())
tot_pe = sum(pe_oi.values())
pcr = (tot_pe / tot_ce) if tot_ce else float("nan")
call_wall = max(strikes, key=lambda s: ce_oi[s])
put_wall = max(strikes, key=lambda s: pe_oi[s])
comb = sorted((ce_oi[s] + pe_oi[s] for s in strikes), reverse=True)
tot_oi = sum(comb) or 1.0
top2_share = (comb[0] + (comb[1] if len(comb) > 1 else 0)) / tot_oi

dist = spot - max_pain
bracketed = (put_wall < spot < call_wall)
clean_pin = bracketed and top2_share >= 0.30

side = None
strike_pick = None
verdict = ""
if abs(dist) <= BAND_PTS:
    verdict = "SIT OUT -- spot is within %.0f pts of max-pain (the pin is basically here; no edge)." % BAND_PTS
elif dist < 0:                                    # spot below pin -> pull UP -> CALL
    side = "CE"
    above = [s for s in strikes if s > spot]
    strike_pick = min(above) if above else None
else:                                             # spot above pin -> pull DOWN -> PUT
    side = "PE"
    below = [s for s in strikes if s < spot]
    strike_pick = max(below) if below else None

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
rel = "below" if dist < 0 else "above" if dist > 0 else "at"
line = "=" * 66
print(line)
print(" SENSEX expiry pick  |  %s" % now)
print(line)
print(" expiry         : %s" % expiry)
print(" spot           : %.2f" % spot)
print(" max-pain       : %.0f    (spot %s pin by %.0f pts)" % (max_pain, rel, abs(dist)))
print(" PCR (OI)       : %.2f" % pcr)
print(" call wall      : %.0f   (CE OI %s)" % (call_wall, format(int(ce_oi[call_wall]), ",")))
print(" put wall       : %.0f   (PE OI %s)" % (put_wall, format(int(pe_oi[put_wall]), ",")))
print(" top-2 OI share : %.0f%%     bracketed pin: %s" % (top2_share * 100, bracketed))
print(" clean pin      : %s   (heuristic: bracketed + top-2 share >= 30%%)" % ("YES" if clean_pin else "no"))
print("-" * 66)
summary = ""
if side and strike_pick is not None:
    prem = ce_ltp[strike_pick] if side == "CE" else pe_ltp[strike_pick]
    tag = "GO (clean pin)" if clean_pin else "MARGINAL (no clean pin -> smallest size, or skip)"
    print(" PICK : %s" % tag)
    print("        SENSEX %.0f %s   premium (LTP) = Rs %.2f" % (strike_pick, side, prem))
    ce_above = [s for s in strikes if s > spot]
    pe_below = [s for s in strikes if s < spot]
    if ce_above and pe_below:
        cS, pS = min(ce_above), max(pe_below)
        print("        both-sides: %.0f CE (Rs %.2f) + %.0f PE (Rs %.2f) = Rs %.2f"
              % (cS, ce_ltp[cS], pS, pe_ltp[pS], ce_ltp[cS] + pe_ltp[pS]))
    summary = "SUMMARY: %s SENSEX %.0f %s @ Rs %.2f | spot %.0f pin %.0f | clean_pin=%s" % (
        tag.split()[0], strike_pick, side, prem, spot, max_pain, "YES" if clean_pin else "no")
else:
    print(" PICK : %s" % verdict)
    summary = "SUMMARY: SIT-OUT | spot %.0f pin %.0f (%.0f pts apart)" % (spot, max_pain, abs(dist))
print("-" * 66)
print(" HONEST READ: SENSEX is the weakest version of this trade in your real data --")
print(" nearest-OTM CALL ticket won 3/8 CAS expiries, PUT 0/8, and one of the 3 (13 Aug)")
print(" was the SEBI-flagged manipulated one -> clean record ~2/8. Expected value is")
print(" NEGATIVE. Take it only on a clean pin, only as total-loss lottery money, else sit out.")
print(" Enter before ~14:55 IST (CAS freeze); hold to the 15:30 settlement.")
print(line)
print(summary)
