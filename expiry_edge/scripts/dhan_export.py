"""Export the CAS-month data through the Dhan API — runs anywhere with internet
(Google Colab in a phone browser is enough; no Mac needed).

    1. Open https://colab.research.google.com  ->  New notebook
    2. Paste this whole file into a cell, set ACCESS_TOKEN (and CLIENT_ID) below, run.
    3. It writes dhan_export.zip and (in Colab) pops a download; upload that zip in the chat.

What it pulls (all via DhanHQ v2 REST, no extra packages):
  index_5m_<IDX>.csv        NIFTY / BANKNIFTY / SENSEX 5-min bars, FROM_DATE .. today   (charts/intraday, IDX_I)
  index_1m_<IDX>.csv        1-min bars for the August expiry days only                  (charts/intraday, IDX_I)
  index_daily_<IDX>.csv     daily bars, 2 years                                         (charts/historical)
  rolling_options_<IDX>.csv REAL option minute bars for the expiring weekly contract,
                            strikes ATM-3 .. ATM+3 relative to spot, CE and PE, with iv/oi/spot
                            (charts/rollingoption — Dhan's expired-options API, 5-min)
Set FULL_HISTORY = True to also pull Sep-2025 .. Jul-2026 rolling-option history (30-day chunks)
for a full real-premium validation of the model (takes a few minutes).
"""
import datetime as dt
import io
import json
import os
import time
import zipfile

import requests

# ----------------------------------------------------------------------------- settings
ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "PASTE_YOUR_DHAN_ACCESS_TOKEN_HERE")
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")            # optional; some endpoints want it
FROM_DATE = "2026-06-01"                                    # 5-min index history start (<= 90 days per call)
CAS_FROM, CAS_TO = "2026-08-01", "2026-08-28"               # rolling-option window (toDate is exclusive)
EXPIRY_DAYS = {"NIFTY": ["2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25"],
               "SENSEX": ["2026-08-06", "2026-08-13", "2026-08-20", "2026-08-27"],
               "BANKNIFTY": ["2026-08-25"]}
FULL_HISTORY = os.environ.get("DHAN_FULL_HISTORY", "0") == "1"   # set DHAN_FULL_HISTORY=1: Sep-2025..Jul-2026 rolling options (~45 weekly expiries) — needed to train/validate the auction imbalance model
OUT_DIR = "dhan_export"
BASE = "https://api.dhan.co/v2"
# Dhan security ids for indices (IDX_I segment). Verified against the scrip master at runtime if reachable.
INDEX_IDS = {"NIFTY": "13", "BANKNIFTY": "25", "SENSEX": "51", "FINNIFTY": "27"}
OPT_SEG = {"NIFTY": "NSE_FNO", "BANKNIFTY": "NSE_FNO", "SENSEX": "BSE_FNO"}
STRIKES = ["ATM-3", "ATM-2", "ATM-1", "ATM", "ATM+1", "ATM+2", "ATM+3"]
# F&O STOCKS to screen for the same CAS blast (monthly expiry).  Opt-in and heavy — each symbol is many
# rolling-option calls.  Set DHAN_STOCKS="IDEA,YESBANK,TATASTEEL,..." or edit this list; empty = skip stocks.
STOCKS = [x for x in os.environ.get("DHAN_STOCKS", "").split(",") if x.strip()]
STOCK_IDS = {}

os.makedirs(OUT_DIR, exist_ok=True)
H = {"access-token": ACCESS_TOKEN, "Content-Type": "application/json", "Accept": "application/json"}
if CLIENT_ID:
    H["client-id"] = CLIENT_ID
LOG = []


def log(msg):
    print(msg); LOG.append(msg)


def post(path, body, tries=3):
    for i in range(tries):
        r = requests.post(f"{BASE}/{path}", headers=H, data=json.dumps(body), timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(2 + 2 * i); continue
        log(f"  {path} -> HTTP {r.status_code}: {r.text[:200]}")
        return None
    return None


def to_ist(ts):
    return (dt.datetime.utcfromtimestamp(int(ts)) + dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")


def write_csv(name, header, rows):
    p = os.path.join(OUT_DIR, name)
    with open(p, "w") as fh:
        fh.write(",".join(header) + "\n")
        for r in rows:
            fh.write(",".join("" if v is None else str(v) for v in r) + "\n")
    log(f"  wrote {name} ({len(rows)} rows)")


def verify_ids():
    """Look the index ids up in Dhan's public scrip master (falls back to the hard-coded ids)."""
    try:
        txt = requests.get("https://images.dhan.co/api-data/api-scrip-master.csv", timeout=60).text
        import csv
        rows = list(csv.DictReader(io.StringIO(txt)))
        for name, key in [("NIFTY", "NIFTY 50"), ("BANKNIFTY", "NIFTY BANK"), ("SENSEX", "SENSEX")]:
            hit = [r for r in rows if r.get("SEM_SEGMENT") == "I" and r.get("SEM_INSTRUMENT_NAME") == "INDEX"
                   and (r.get("SM_SYMBOL_NAME", "").strip().upper() == key or r.get("SEM_TRADING_SYMBOL", "").strip().upper() == key)]
            if hit:
                INDEX_IDS[name] = hit[0]["SEM_SMST_SECURITY_ID"].strip()
        for sym in STOCKS:
            hit = [r for r in rows if r.get("SEM_EXM_EXCH_ID") == "NSE" and r.get("SEM_SEGMENT") == "E"
                   and (r.get("SEM_TRADING_SYMBOL", "").strip().upper() == sym.strip().upper()
                        or r.get("SM_SYMBOL_NAME", "").strip().upper() == sym.strip().upper())]
            if hit:
                STOCK_IDS[sym.strip()] = hit[0]["SEM_SMST_SECURITY_ID"].strip()
        log(f"index ids: {INDEX_IDS}")
        if STOCKS:
            log(f"stock ids: {STOCK_IDS} (missing: {[s for s in STOCKS if s.strip() not in STOCK_IDS]})")
    except Exception as e:                                          # noqa: BLE001
        log(f"scrip master not reachable ({e}); using default ids {INDEX_IDS}")


def index_bars(idx, interval, d0, d1, fname):
    rows = []
    a = dt.date.fromisoformat(d0); end = dt.date.fromisoformat(d1)
    while a < end:
        b = min(a + dt.timedelta(days=89), end)
        j = post("charts/intraday", {"securityId": INDEX_IDS[idx], "exchangeSegment": "IDX_I", "instrument": "INDEX",
                                     "interval": str(interval), "fromDate": a.isoformat(), "toDate": b.isoformat()})
        if j and j.get("timestamp"):
            for i, ts in enumerate(j["timestamp"]):
                rows.append([to_ist(ts), j["open"][i], j["high"][i], j["low"][i], j["close"][i], (j.get("volume") or [0] * len(j["timestamp"]))[i]])
        a = b
        time.sleep(0.4)
    write_csv(fname, ["ts", "open", "high", "low", "close", "volume"], rows)


def index_daily(idx):
    d1 = dt.date.today() + dt.timedelta(days=1); d0 = d1 - dt.timedelta(days=740)
    j = post("charts/historical", {"securityId": INDEX_IDS[idx], "exchangeSegment": "IDX_I", "instrument": "INDEX",
                                   "expiryCode": 0, "fromDate": d0.isoformat(), "toDate": d1.isoformat()})
    rows = []
    if j and j.get("timestamp"):
        for i, ts in enumerate(j["timestamp"]):
            rows.append([to_ist(ts)[:10], j["open"][i], j["high"][i], j["low"][i], j["close"][i]])
    write_csv(f"index_daily_{idx}.csv", ["date", "open", "high", "low", "close"], rows)


def rolling_options(idx, d0, d1, fname_suffix=""):
    """Expired-options data: minute bars of the expiring weekly contract at strikes relative to spot."""
    rows = {}
    a = dt.date.fromisoformat(d0); end = dt.date.fromisoformat(d1)
    while a < end:
        b = min(a + dt.timedelta(days=29), end)
        for strike in STRIKES:
            for side in ("CALL", "PUT"):
                got = None
                for instrument, ecode in (("OPTIDX", 0), ("OPTIDX", 1), ("INDEX", 0), ("INDEX", 1)):
                    j = post("charts/rollingoption", {"exchangeSegment": OPT_SEG[idx], "interval": "5", "securityId": INDEX_IDS[idx],
                                                      "instrument": instrument, "expiryCode": ecode, "expiryFlag": "WEEK",
                                                      "strike": strike, "drvOptionType": side,
                                                      "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                                                      "fromDate": a.isoformat(), "toDate": b.isoformat()})
                    if j and j.get("data"):
                        got = j["data"]; break
                if not got:
                    log(f"  {idx} {strike} {side} {a}..{b}: no data"); continue
                for key in ("ce", "pe"):
                    blk = got.get(key)
                    if not blk or not blk.get("timestamp"):
                        continue
                    n = len(blk["timestamp"])
                    g = lambda k: (blk.get(k) or [None] * n)          # noqa: E731
                    for i in range(n):
                        t = to_ist(blk["timestamp"][i])
                        rows[(t, strike, key.upper())] = [t, strike, key.upper(), g("strike")[i], g("spot")[i], g("open")[i], g("high")[i],
                                                          g("low")[i], g("close")[i], g("iv")[i], g("volume")[i], g("oi")[i]]
                time.sleep(0.4)
        a = b
    write_csv(f"rolling_options_{idx}{fname_suffix}.csv",
              ["ts", "offset", "side", "strike", "spot", "open", "high", "low", "close", "iv", "volume", "oi"], list(rows.values()))


def main():
    t0 = time.time()
    if ACCESS_TOKEN.startswith("PASTE"):
        raise SystemExit("Set ACCESS_TOKEN first (Dhan web -> DhanHQ Trading APIs -> generate access token).")
    verify_ids()
    today = dt.date.today() + dt.timedelta(days=1)
    for idx in ("NIFTY", "BANKNIFTY", "SENSEX"):
        log(f"[{idx}] 5-min bars {FROM_DATE}..{today}")
        index_bars(idx, 5, FROM_DATE, today.isoformat(), f"index_5m_{idx}.csv")
        log(f"[{idx}] daily bars")
        index_daily(idx)
        for d in EXPIRY_DAYS.get(idx, []):
            if d <= dt.date.today().isoformat():
                index_bars(idx, 1, d, (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat(), f"index_1m_{idx}_{d}.csv")
    for idx in ("NIFTY", "SENSEX", "BANKNIFTY"):
        log(f"[{idx}] rolling (expired) option bars {CAS_FROM}..{CAS_TO}")
        rolling_options(idx, CAS_FROM, CAS_TO)
    for sym in STOCKS:
        sid = STOCK_IDS.get(sym.strip())
        if not sid:
            log(f"[{sym}] no security id — skipped"); continue
        log(f"[{sym}] stock rolling (expired) option bars {CAS_FROM}..{CAS_TO}")
        a = dt.date.fromisoformat(CAS_FROM); end = dt.date.fromisoformat(CAS_TO); rows = {}
        while a < end:
            b = min(a + dt.timedelta(days=29), end)
            for strike in STRIKES:
                for side in ("CALL", "PUT"):
                    j = None
                    for instrument, ecode in (("OPTSTK", 0), ("OPTSTK", 1)):
                        j = post("charts/rollingoption", {"exchangeSegment": "NSE_FNO", "interval": "5", "securityId": sid,
                                                          "instrument": instrument, "expiryCode": ecode, "expiryFlag": "MONTH",
                                                          "strike": strike, "drvOptionType": side,
                                                          "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                                                          "fromDate": a.isoformat(), "toDate": b.isoformat()})
                        if j and j.get("data"):
                            break
                    if not (j and j.get("data")):
                        continue
                    for key in ("ce", "pe"):
                        blk = j["data"].get(key)
                        if not blk or not blk.get("timestamp"):
                            continue
                        n = len(blk["timestamp"]); g = lambda k: (blk.get(k) or [None] * n)   # noqa: E731
                        for i in range(n):
                            t = to_ist(blk["timestamp"][i])
                            rows[(t, strike, key.upper())] = [t, strike, key.upper(), g("strike")[i], g("spot")[i], g("open")[i], g("high")[i],
                                                              g("low")[i], g("close")[i], g("iv")[i], g("volume")[i], g("oi")[i]]
                    time.sleep(0.4)
            a = b
        write_csv(f"rolling_options_{sym.strip()}.csv",
                  ["ts", "offset", "side", "strike", "spot", "open", "high", "low", "close", "iv", "volume", "oi"], list(rows.values()))
    if FULL_HISTORY:
        for idx in ("NIFTY", "BANKNIFTY"):
            log(f"[{idx}] rolling option history 2025-09-01..2026-08-01")
            rolling_options(idx, "2025-09-01", "2026-08-01", "_history")
    with open(os.path.join(OUT_DIR, "log.txt"), "w") as fh:
        fh.write("\n".join(LOG))
    zname = "dhan_export.zip"
    with zipfile.ZipFile(zname, "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(OUT_DIR):
            z.write(os.path.join(OUT_DIR, f), arcname=f)
    log(f"done in {time.time() - t0:.0f}s -> {zname}")
    try:
        from google.colab import files                              # type: ignore
        files.download(zname)
    except Exception:                                               # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
