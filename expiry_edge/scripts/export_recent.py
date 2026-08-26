"""Export the data needed to check the CAS month (Aug 2026) — run this on YOUR machine
(home/office IP; NSE/BSE/Yahoo block cloud IPs), then upload the files in export/.

    pip install yfinance requests pandas
    python scripts/export_recent.py                      # ~1-2 minutes

Writes:
  export/index_5m.csv            NIFTY / SENSEX / BANKNIFTY 5-min bars, last 60 days (yfinance)
  export/index_daily.csv         2 years of daily bars (for trailing vol / range context)
  export/fo_<DATE>.csv           NSE F&O bhavcopy rows for index options expiring that day (real O/H/L/C per strike)
  export/bse_<DATE>.csv          BSE derivatives bhavcopy rows for SENSEX options expiring that day
Nothing is uploaded anywhere by this script; it only writes local CSV files.
"""
from __future__ import annotations

import datetime as dt
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "export"
OUT.mkdir(exist_ok=True)

YF = {"NIFTY": "^NSEI", "SENSEX": "^BSESN", "BANKNIFTY": "^NSEBANK"}
NSE_EXPIRIES = ["2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25"]        # NIFTY Tuesdays (25th = monthly)
BSE_EXPIRIES = ["2026-08-06", "2026-08-13", "2026-08-20", "2026-08-27"]        # SENSEX Thursdays (27th = monthly, if past)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}


def export_index():
    import yfinance as yf
    frames, dailies = [], []
    for idx, sym in YF.items():
        h = yf.Ticker(sym).history(interval="5m", period="60d", auto_adjust=False)
        h = h.tz_convert("Asia/Kolkata").tz_localize(None)
        f = h[["Open", "High", "Low", "Close"]].rename(columns=str.lower).reset_index().rename(columns={"Datetime": "ts", "index": "ts"})
        f.insert(0, "index", idx)
        frames.append(f)
        d = yf.Ticker(sym).history(interval="1d", period="2y", auto_adjust=False)
        d = d[["Open", "High", "Low", "Close"]].rename(columns=str.lower).reset_index().rename(columns={"Date": "ts", "index": "ts"})
        d["ts"] = pd.to_datetime(d["ts"]).dt.tz_localize(None)
        d.insert(0, "index", idx)
        dailies.append(d)
        print(f"{idx}: {len(f)} 5-min bars {f.ts.min()} .. {f.ts.max()} | {len(d)} daily bars")
    pd.concat(frames).to_csv(OUT / "index_5m.csv", index=False)
    pd.concat(dailies).to_csv(OUT / "index_daily.csv", index=False)


def export_nse_bhavcopy(date: str):
    import requests
    d = pd.Timestamp(date)
    url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    s = requests.Session(); s.headers.update(UA)
    try:
        s.get("https://www.nseindia.com", timeout=20)
        r = s.get(url, timeout=60); r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]))
        sub = df[(df["TckrSymb"].isin(["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])) & (df["FinInstrmTp"].astype(str).str.upper().isin(["IDO", "OPTIDX"]))]
        sub = sub[pd.to_datetime(sub["XpryDt"]).dt.strftime("%Y-%m-%d") == date]
        sub.to_csv(OUT / f"fo_{d:%Y%m%d}.csv", index=False)
        print(f"NSE {date}: {len(sub)} index-option rows expiring that day (from {len(df)} contracts)")
    except Exception as e:                                    # noqa: BLE001
        print(f"NSE {date}: failed ({e})")


def export_bse_bhavcopy(date: str):
    import requests
    d = pd.Timestamp(date)
    url = f"https://www.bseindia.com/download/Bhavcopy/Derivative/BhavCopy_BSE_FO_0_0_0_{d:%Y%m%d}_F_0000.CSV"
    try:
        r = requests.get(url, headers={**UA, "Referer": "https://www.bseindia.com/"}, timeout=60); r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        sub = df[df["TckrSymb"].astype(str).str.upper().str.contains("SENSEX")]
        sub = sub[pd.to_datetime(sub["XpryDt"]).dt.strftime("%Y-%m-%d") == date]
        sub.to_csv(OUT / f"bse_{d:%Y%m%d}.csv", index=False)
        print(f"BSE {date}: {len(sub)} SENSEX-option rows expiring that day (from {len(df)} contracts)")
    except Exception as e:                                    # noqa: BLE001
        print(f"BSE {date}: failed ({e})")


if __name__ == "__main__":
    export_index()
    today = dt.date.today().isoformat()
    for x in NSE_EXPIRIES:
        if x <= today:
            export_nse_bhavcopy(x)
    for x in BSE_EXPIRIES:
        if x <= today:
            export_bse_bhavcopy(x)
    print("\nDone. Upload everything in", OUT)
