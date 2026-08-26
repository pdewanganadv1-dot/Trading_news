"""Data loading & fetching.

Two tiers:
  1. *Offline history* (works anywhere): minute bars for NIFTY, SENSEX and BANKNIFTY
     mirrored on GitHub (MIT-licensed community datasets). Coverage:
        NIFTY     1-min  2015-01-09 .. 2024-03-27
        SENSEX    1-min  2018-03-08 .. 2024-03-22
        BANKNIFTY 1-min  2015-01-09 .. 2026-04-22
  2. *Live/recent* (run on your own machine — NSE/BSE block cloud IPs): NSE F&O
     bhavcopy (UDiFF), BSE derivatives bhavcopy, India VIX, and intraday index bars
     via yfinance (last 60 days) or the NSE charting API (openchart). See fetch_*.

All timestamps are naive IST.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .config import SESSION_LAST_BAR, SESSION_OPEN

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

GITHUB_SOURCES = {
    "NIFTY": ("https://github.com/sandeepkapri/Nifty50-Minute-Data.git", "nifty50_candlestick_data.csv"),
    "SENSEX": ("https://github.com/sandeepkapri/Sensex-Minute-Data.git", "sensex_candlestick_data.csv"),
    "BANKNIFTY": ("https://github.com/sandeepkapri/BankNifty-Data.git", "bank-nifty-1m-data.csv"),
}
RAW_FILE = {"NIFTY": "nifty50_1m.csv", "SENSEX": "sensex_1m.csv", "BANKNIFTY": "banknifty_1m.csv"}


# ----------------------------------------------------------------------------
# Offline history
# ----------------------------------------------------------------------------
def fetch_github_history(index: str, raw_dir: Path = RAW_DIR) -> Path:
    """Shallow-clone the community dataset for `index` into data/raw (idempotent)."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / RAW_FILE[index]
    if target.exists():
        return target
    url, fname = GITHUB_SOURCES[index]
    tmp = raw_dir / f"_clone_{index}"
    subprocess.run(["git", "clone", "--depth", "1", "-q", url, str(tmp)], check=True)
    (tmp / fname).rename(target)
    subprocess.run(["rm", "-rf", str(tmp)], check=False)
    return target


def load_minute(index: str, raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Return clean 1-min bars: columns [ts, open, high, low, close], regular session only."""
    path = raw_dir / RAW_FILE[index]
    if not path.exists():
        path = fetch_github_history(index, raw_dir)
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts = pd.to_datetime(df["date"] + " " + df["time"], format="%d-%m-%Y %H:%M:%S")
    out = pd.DataFrame({"ts": ts, "open": df["open"].astype(float), "high": df["high"].astype(float),
                        "low": df["low"].astype(float), "close": df["close"].astype(float)})
    out = out.sort_values("ts").drop_duplicates("ts")
    t = out["ts"].dt.time
    out = out[(t >= SESSION_OPEN) & (t <= SESSION_LAST_BAR)]
    # drop broken bars
    ok = (out[["open", "high", "low", "close"]] > 0).all(axis=1) & (out["high"] >= out["low"])
    out = out[ok]
    # keep only full-ish sessions (>= 300 bars) – drops Muhurat / truncated days
    n = out.groupby(out["ts"].dt.date)["ts"].transform("size")
    out = out[n >= 300].reset_index(drop=True)
    out["date"] = out["ts"].dt.date
    out["minute"] = ((out["ts"].dt.hour - 9) * 60 + out["ts"].dt.minute - 15).astype(int)  # 0..374
    return out


def resample(df1: pd.DataFrame, minutes: int = 5) -> pd.DataFrame:
    """Resample 1-min bars to N-min bars aligned to 09:15 (label = bar start)."""
    g = df1
    bars = (g.groupby([g["date"], g["minute"] // minutes])
             .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                  close=("close", "last"), ts=("ts", "first"), n=("close", "size")))
    bars = bars.reset_index(drop=True)
    bars["date"] = bars["ts"].dt.date
    bars["minute"] = ((bars["ts"].dt.hour - 9) * 60 + bars["ts"].dt.minute - 15).astype(int)
    bars["ts"] = bars["ts"].dt.floor(f"{minutes}min")
    return bars[["ts", "date", "minute", "open", "high", "low", "close", "n"]]


def daily_table(df1: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC + derived context (previous-day levels, gap, realised vol)."""
    d = (df1.groupby("date")
            .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 nbars=("close", "size")))
    d["prev_close"] = d["close"].shift(1)
    d["prev_high"] = d["high"].shift(1)
    d["prev_low"] = d["low"].shift(1)
    d["gap_pct"] = (d["open"] / d["prev_close"] - 1) * 100
    d["range_pct"] = (d["high"] - d["low"]) / d["open"] * 100
    d["ret_pct"] = (d["close"] / d["open"] - 1) * 100          # open-to-close
    # Parkinson intraday vol estimator (per session), then trailing mean
    park = np.log(d["high"] / d["low"]) ** 2 / (4 * np.log(2))
    d["rv_park"] = np.sqrt(park) * 100                        # % per session
    d["rv20"] = np.sqrt(park.rolling(20).mean().shift(1)) * 100   # trailing, excludes today
    d["range20"] = d["range_pct"].rolling(20).mean().shift(1)
    # trailing-1y percentile rank of rv20 (no look-ahead) -> vol regime terciles
    d["rv20_rank"] = d["rv20"].rolling(250, min_periods=60).rank(pct=True)
    d["vol_regime"] = pd.cut(d["rv20_rank"], [0, 1 / 3, 2 / 3, 1.0001], labels=["low", "mid", "high"])
    d.index = pd.to_datetime(d.index)
    d.index.name = "date"
    return d


# ----------------------------------------------------------------------------
# Live / recent fetchers – meant to run on your own machine (India IP or any
# non-cloud IP). They are best-effort wrappers; each returns a DataFrame or raises.
# ----------------------------------------------------------------------------
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}


def fetch_nse_fo_bhavcopy(date: dt.date) -> pd.DataFrame:
    """NSE F&O daily bhavcopy (UDiFF format, since 8-Jul-2024). One row per contract:
    OpnPric/HghPric/LwPric/ClsPric/SttlmPric/OpnIntrst/TtlTradgVol ... for every strike.
    On an expiry day this gives you the *actual* intraday O/H/L/C of every option.
    """
    import requests
    url = ("https://nsearchives.nseindia.com/content/fo/"
           f"BhavCopy_NSE_FO_0_0_0_{date:%Y%m%d}_F_0000.csv.zip")
    s = requests.Session()
    s.headers.update(UA)
    s.get("https://www.nseindia.com", timeout=20)          # cookies
    r = s.get(url, timeout=60)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name))
    df["TradDt"] = pd.to_datetime(df["TradDt"])
    return df


def fetch_bse_fo_bhavcopy(date: dt.date) -> pd.DataFrame:
    """BSE derivatives bhavcopy (SENSEX / BANKEX options). Same UDiFF columns as NSE."""
    import requests
    url = ("https://www.bseindia.com/download/Bhavcopy/Derivative/"
           f"BhavCopy_BSE_FO_0_0_0_{date:%Y%m%d}_F_0000.CSV")
    r = requests.get(url, headers={**UA, "Referer": "https://www.bseindia.com/"}, timeout=60)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


def fetch_yf_intraday(symbol: str = "^NSEI", interval: str = "5m", period: str = "60d") -> pd.DataFrame:
    """Recent intraday index bars via yfinance (^NSEI, ^BSESN, ^NSEBANK). 5m/15m: last 60 days;
    1m: last 7 days per request. Returns [ts, open, high, low, close] in IST."""
    import yfinance as yf
    h = yf.Ticker(symbol).history(interval=interval, period=period, auto_adjust=False)
    h = h.tz_convert("Asia/Kolkata").tz_localize(None)
    out = h[["Open", "High", "Low", "Close"]].rename(columns=str.lower).reset_index()
    out = out.rename(columns={"Datetime": "ts", "index": "ts"})
    return out


def fetch_india_vix_yf(period: str = "5y") -> pd.DataFrame:
    """Daily India VIX close via yfinance (^INDIAVIX)."""
    import yfinance as yf
    h = yf.Ticker("^INDIAVIX").history(period=period)
    out = h[["Close"]].rename(columns={"Close": "vix"})
    out.index = out.index.tz_localize(None).normalize()
    out.index.name = "date"
    return out


def fetch_openchart_intraday(symbol: str = "NIFTY", interval: str = "1m",
                             start: dt.date | None = None, end: dt.date | None = None) -> pd.DataFrame:
    """Intraday index history through NSE's charting API using the `openchart` package
    (pip install openchart). Works from a normal home/office IP. Returns 1-min bars."""
    from openchart import NSEData
    nse = NSEData()
    nse.download()                                             # symbol master
    end = end or dt.date.today()
    start = start or (end - dt.timedelta(days=365))
    df = nse.historical(symbol=symbol, exchange="NSE", start=dt.datetime.combine(start, dt.time()),
                        end=dt.datetime.combine(end, dt.time()), interval=interval)
    df = df.rename(columns=str.lower)
    df = df.rename(columns={"timestamp": "ts", "datetime": "ts"})
    return df[["ts", "open", "high", "low", "close"]]


def append_recent_to_raw(index: str, new_bars: pd.DataFrame, raw_dir: Path = RAW_DIR) -> Path:
    """Append 1-min bars (columns ts/open/high/low/close) to the raw CSV in the community
    file's format so the rest of the toolkit picks them up automatically."""
    path = raw_dir / RAW_FILE[index]
    old = pd.read_csv(path)
    add = pd.DataFrame({
        "Instrument": {"NIFTY": "Nifty50", "SENSEX": "Sensex", "BANKNIFTY": "Banknifty"}[index],
        "Date": pd.to_datetime(new_bars["ts"]).dt.strftime("%d-%m-%Y"),
        "Time": pd.to_datetime(new_bars["ts"]).dt.strftime("%H:%M:%S"),
        "Open": new_bars["open"], "High": new_bars["high"], "Low": new_bars["low"], "Close": new_bars["close"],
    })
    merged = pd.concat([old, add], ignore_index=True)
    merged["_ts"] = pd.to_datetime(merged["Date"] + " " + merged["Time"], format="%d-%m-%Y %H:%M:%S")
    merged = merged.sort_values("_ts").drop_duplicates("_ts").drop(columns="_ts")
    merged.to_csv(path, index=False)
    return path


# ----------------------------------------------------------------------------
# Dhan (DhanHQ v2) — works from any machine with internet, token from env DHAN_ACCESS_TOKEN
# ----------------------------------------------------------------------------
DHAN_INDEX_IDS = {"NIFTY": "13", "BANKNIFTY": "25", "SENSEX": "51", "FINNIFTY": "27"}


def _dhan_post(path: str, body: dict) -> dict:
    import json
    import requests
    token = os.environ.get("DHAN_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("set DHAN_ACCESS_TOKEN (Dhan web -> DhanHQ Trading APIs -> access token)")
    h = {"access-token": token, "Content-Type": "application/json", "Accept": "application/json"}
    if os.environ.get("DHAN_CLIENT_ID"):
        h["client-id"] = os.environ["DHAN_CLIENT_ID"]
    r = requests.post(f"https://api.dhan.co/v2/{path}", headers=h, data=json.dumps(body), timeout=60)
    r.raise_for_status()
    return r.json()


def _dhan_frame(j: dict) -> pd.DataFrame:
    ts = pd.to_datetime(pd.Series(j["timestamp"]), unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return pd.DataFrame({"ts": ts, "open": j["open"], "high": j["high"], "low": j["low"], "close": j["close"]})


def fetch_dhan_intraday(index: str, interval: int = 5, start: dt.date | None = None, end: dt.date | None = None) -> pd.DataFrame:
    """Index bars (IDX_I) at 1/5/15/25/60 minutes, up to 90 days per call (chunks automatically)."""
    end = end or (dt.date.today() + dt.timedelta(days=1))
    start = start or (end - dt.timedelta(days=90))
    frames = []
    a = start
    while a < end:
        b = min(a + dt.timedelta(days=89), end)
        j = _dhan_post("charts/intraday", {"securityId": DHAN_INDEX_IDS[index], "exchangeSegment": "IDX_I", "instrument": "INDEX",
                                           "interval": str(interval), "fromDate": a.isoformat(), "toDate": b.isoformat()})
        if j.get("timestamp"):
            frames.append(_dhan_frame(j))
        a = b
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["ts", "open", "high", "low", "close"])


def fetch_dhan_daily(index: str, days: int = 740) -> pd.DataFrame:
    end = dt.date.today() + dt.timedelta(days=1)
    j = _dhan_post("charts/historical", {"securityId": DHAN_INDEX_IDS[index], "exchangeSegment": "IDX_I", "instrument": "INDEX",
                                         "expiryCode": 0, "fromDate": (end - dt.timedelta(days=days)).isoformat(), "toDate": end.isoformat()})
    return _dhan_frame(j)
