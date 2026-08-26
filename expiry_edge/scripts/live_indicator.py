"""Expiry-day BUY-SCORE indicator — live or replay.

Prints, for every 5-minute bar of the session, the buy-score (probability that an ATM
straddle bought at that bar touches +30% within 60 min), the breakout direction, and the
verdict (GO / LEAN / NO / WAIT).  Alerts are the bars where verdict is GO or LEAN.

Live (on your own machine; NSE/BSE/Yahoo block cloud IPs):
    pip install yfinance
    python scripts/live_indicator.py --index NIFTY --source yf
    python scripts/live_indicator.py --index NIFTY --source openchart      # NSE charting API
    DHAN_ACCESS_TOKEN=... python scripts/live_indicator.py --index NIFTY --source dhan   # Dhan API (works from Colab/anywhere)
Replay a historical date from the cached data (works anywhere):
    python scripts/live_indicator.py --index NIFTY --replay 2023-11-23
Loop live every 5 minutes:
    python scripts/live_indicator.py --index NIFTY --source yf --watch
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.calendar import label_expiry_days                       # noqa: E402
from expiry_edge.config import CAS_START_DATE, VRP_MULTIPLIER              # noqa: E402
from expiry_edge.data import daily_table, fetch_dhan_daily, fetch_dhan_intraday, fetch_openchart_intraday, fetch_yf_intraday, resample  # noqa: E402
from expiry_edge.features import add_features                            # noqa: E402
from expiry_edge.options import bs_price, reactive_sigma_path, remaining_share, sigma_day_from_rv   # noqa: E402
from expiry_edge.otm import PREMIUM_FLOOR                                 # noqa: E402
from expiry_edge.config import CONTRACT                                    # noqa: E402
from expiry_edge.score import BuyScore, engineer, live_features         # noqa: E402

YF = {"NIFTY": "^NSEI", "SENSEX": "^BSESN", "BANKNIFTY": "^NSEBANK"}
LAST_ENTRY_MINUTE = 340          # 14:59 bar close — leave >= 15 min before the 15:15 CAS cutoff


def load_replay(index: str, date: str):
    m = pd.read_parquet(ROOT / f"data/{index}_1m.parquet")
    d = pd.read_parquet(ROOT / f"data/{index}_daily.parquet")
    day = pd.Timestamp(date).date()
    hist = m[m["date"] <= day]
    return hist, d[d.index <= pd.Timestamp(date)]


def load_live(index: str, source: str):
    if source == "yf":
        bars = fetch_yf_intraday(YF[index], interval="5m", period="60d")
        daily = fetch_yf_intraday(YF[index], interval="1d", period="2y")
    elif source == "dhan":
        bars = fetch_dhan_intraday(index, 5)
        daily = fetch_dhan_daily(index)
    else:
        bars = fetch_openchart_intraday(index, "5m", start=dt.date.today() - dt.timedelta(days=120))
        daily = fetch_openchart_intraday(index, "1d", start=dt.date.today() - dt.timedelta(days=800))
    bars["ts"] = pd.to_datetime(bars["ts"]); daily["ts"] = pd.to_datetime(daily["ts"])
    t = bars["ts"].dt.time
    bars = bars[(t >= dt.time(9, 15)) & (t <= dt.time(15, 29))].dropna().copy()
    bars["date"] = bars["ts"].dt.date
    bars["minute"] = ((bars["ts"].dt.hour - 9) * 60 + bars["ts"].dt.minute - 15).astype(int)
    # daily table from daily bars (better than from 60 days of 5-min bars)
    dd = daily.dropna().copy()
    dd["date"] = dd["ts"].dt.date
    d = dd.groupby("date").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    d["nbars"] = 1
    d["prev_close"] = d["close"].shift(1); d["prev_high"] = d["high"].shift(1); d["prev_low"] = d["low"].shift(1)
    d["gap_pct"] = (d["open"] / d["prev_close"] - 1) * 100
    d["range_pct"] = (d["high"] - d["low"]) / d["open"] * 100
    park = np.log(d["high"] / d["low"]) ** 2 / (4 * np.log(2))
    d["rv20"] = np.sqrt(park.rolling(20).mean().shift(1)) * 100
    d["range20"] = d["range_pct"].rolling(20).mean().shift(1)
    d["rv20_rank"] = d["rv20"].rolling(250, min_periods=60).rank(pct=True)
    d["vol_regime"] = pd.cut(d["rv20_rank"], [0, 1 / 3, 2 / 3, 1.0001], labels=["low", "mid", "high"])
    d.index = pd.to_datetime(d.index); d.index.name = "date"
    # today's row may be missing from the daily feed while the session is open: build it from the 5-min bars
    today = bars["date"].max()
    if pd.Timestamp(today) not in d.index:
        tb = bars[bars["date"] == today]
        prev = d.iloc[-1]
        row = {"open": tb["open"].iloc[0], "high": tb["high"].max(), "low": tb["low"].min(), "close": tb["close"].iloc[-1], "nbars": 1,
               "prev_close": prev["close"], "prev_high": prev["high"], "prev_low": prev["low"],
               "gap_pct": (tb["open"].iloc[0] / prev["close"] - 1) * 100, "range_pct": np.nan,
               "rv20": np.sqrt(park.tail(20).mean()) * 100, "range20": d["range_pct"].tail(20).mean(),
               "rv20_rank": np.nan}
        d.loc[pd.Timestamp(today)] = row
        d["rv20_rank"] = d["rv20"].rolling(250, min_periods=60).rank(pct=True)
        d["vol_regime"] = pd.cut(d["rv20_rank"], [0, 1 / 3, 2 / 3, 1.0001], labels=["low", "mid", "high"])
    return bars, d


def run_once(index: str, bars5: pd.DataFrame, d: pd.DataFrame, model: BuyScore, cum_profile: np.ndarray, quiet=False):
    d = d.join(label_expiry_days(d.index, index), rsuffix="_cal") if "is_expiry" not in d.columns else d
    f = add_features(bars5, d)
    today = f["date"].max()
    ft = f[f["date"] == today]
    drow = d.loc[pd.Timestamp(today)]
    is_exp, is_mon = int(bool(drow["is_expiry"])), int(bool(drow["is_monthly"]))
    sigma0 = sigma_day_from_rv(drow["rv20"])
    raw = live_features(bars5[bars5["date"] == today], ft, drow, sigma0, cum_profile, is_exp, is_mon, pd.Timestamp(today).dayofweek)
    eng = engineer(raw)
    p = model.score(eng)
    raw["score"] = p
    raw["dir"] = np.where(raw["new_high"] == 1, 1, np.where(raw["new_low"] == 1, -1, 0))
    cas = pd.Timestamp(today).date() >= CAS_START_DATE
    verd = []
    for _, r in raw.iterrows():
        if cas and r["minute"] > LAST_ENTRY_MINUTE:
            verd.append("CUTOFF (CAS: no new entries after 15:00)")
        else:
            verd.append(model.verdict(r["score"], int(r["dir"])))
    raw["verdict"] = verd
    # ---- strike suggestion with model premium estimates (nearest OTM priced <= cap, never 3 OTM)
    step = CONTRACT[index]["strike_step"]; floor = PREMIUM_FLOOR[index]
    cap = {"NIFTY": 10.0, "BANKNIFTY": 25.0, "SENSEX": 30.0}[index]
    R = remaining_share(cum_profile[1:] - cum_profile[:-1], cas=cas)
    S_today = bars5[bars5["date"] == today].sort_values("minute")
    S1 = np.repeat(S_today["close"].values, 5)[:375]                      # 5-min closes stretched to a minute grid
    S1 = np.concatenate([S1, np.full(375 - len(S1), S1[-1])]) if len(S1) < 375 else S1
    sig_m = reactive_sigma_path(S1, cum_profile[1:] - cum_profile[:-1], sigma0)
    sugg = []
    for _, r in raw.iterrows():
        m0 = int(r["minute"]); S0 = r["close"]
        V = sig_m[min(m0, 374)] ** 2 * R[min(m0 + 1, 375)]
        K0 = float(np.round(S0 / step) * step)
        side = 1 if r["dir"] == 1 else (-1 if r["dir"] == -1 else (1 if S0 >= K0 else -1))
        kind = "CE" if side == 1 else "PE"
        prem = [max(float(bs_price(S0, K0 + side * k * step, V, kind)), floor) for k in (0, 1, 2)]
        k = 1 if prem[1] <= cap else (2 if prem[2] <= cap else 1)
        sugg.append(f"{K0 + side * k * step:.0f} {kind} ~{prem[k]:.1f} (ATM~{prem[0]:.0f}/+1~{prem[1]:.1f}/+2~{prem[2]:.1f})")
    raw["strike"] = sugg
    if not quiet:
        print(f"\n{index} {today}  expiry={'YES' if is_exp else 'no'}{' (monthly)' if is_mon else ''}  gap {drow['gap_pct']:+.2f}%  "
              f"rv20 {drow['rv20']:.2f}% (rank {drow.get('rv20_rank', np.nan):.2f})  sigma_day {sigma0*100:.2f}%  CAS={'yes' if cas else 'no'}")
        print(f"{'time':>5} {'close':>9} {'score':>6} {'varSpent':>8} {'rangeRel':>8} {'posEdge':>7} {'dir':>4}  verdict")
        for _, r in raw.iterrows():
            hh, mm = 9 + (r['minute'] + 15) // 60, (r['minute'] + 15) % 60
            flag = f"  <-- ALERT  buy {r['strike']}" if r["verdict"].startswith(("GO", "LEAN")) else ""
            print(f"{hh:02d}:{mm:02d} {r['close']:9.1f} {r['score']*100:5.0f}% {r['var_spent_ratio']:8.2f} {r['range_sofar_rel']:8.2f} "
                  f"{abs(r['pos_in_range']-0.5)*2:7.2f} {int(r['dir']):4d}  {r['verdict']}{flag}")
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="NIFTY", choices=list(YF))
    ap.add_argument("--source", default="yf", choices=["yf", "openchart", "dhan"], help="dhan needs env DHAN_ACCESS_TOKEN")
    ap.add_argument("--replay", default=None, help="YYYY-MM-DD: replay a cached historical session")
    ap.add_argument("--watch", action="store_true", help="re-run every 5 minutes (live)")
    a = ap.parse_args()
    model = BuyScore()
    prof = np.load(ROOT / f"outputs/{'NIFTY' if a.index == 'SENSEX' else a.index}_profile_expiry.npy")
    cum_profile = np.concatenate([[0.0], np.cumsum(prof)])
    if a.replay:
        m, d = load_replay(a.index, a.replay)
        bars5 = resample(m, 5)
        run_once(a.index, bars5, d, model, cum_profile)
        return
    while True:
        bars5, d = load_live(a.index, a.source)
        run_once(a.index, bars5, d, model, cum_profile)
        if not a.watch:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
