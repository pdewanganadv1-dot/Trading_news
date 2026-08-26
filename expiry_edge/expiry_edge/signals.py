"""Signal definitions -> event table.

Each signal function receives the feature frame (5-min bars with features) and
returns a boolean Series for LONG triggers and one for SHORT triggers, evaluated at
bar close.  `build_events` turns them into rows:
    date, minute (bar START minute; entry happens at the bar's close = minute+4),
    signal, direction (+1 long/-1 short), plus day-context columns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _first_in_day(mask: pd.Series, dates: pd.Series) -> pd.Series:
    """Keep only the first True per day."""
    return mask & (~mask.groupby(dates).cumsum().shift(1).fillna(0).astype(bool))


def _cross_up(a: pd.Series, b) -> pd.Series:
    return (a > b) & (a.shift(1) <= (b.shift(1) if isinstance(b, pd.Series) else b))


def _cross_dn(a: pd.Series, b) -> pd.Series:
    return (a < b) & (a.shift(1) >= (b.shift(1) if isinstance(b, pd.Series) else b))


def signal_library(f: pd.DataFrame) -> dict:
    """Return {name: (long_mask, short_mask)}.  All masks are aligned to f.index."""
    d = f["date"]
    c, o = f["close"], f["open"]
    same_day = f["bar_idx"] > 0
    sig = {}

    # 1. Opening-range breakouts (first close beyond the range, after the range forms)
    after15 = f["bar_idx"] >= 3
    sig["ORB15"] = (_first_in_day((c > f["or15_high"]) & after15, d),
                    _first_in_day((c < f["or15_low"]) & after15, d))
    after30 = f["bar_idx"] >= 6
    sig["ORB30"] = (_first_in_day((c > f["or30_high"]) & after30, d),
                    _first_in_day((c < f["or30_low"]) & after30, d))
    # ORB30 with a "tight range" filter: opening range < 60% of a typical day range
    tight = f["or30_rel"] < 0.6
    sig["ORB30_tight"] = (sig["ORB30"][0] & tight, sig["ORB30"][1] & tight)

    # 2. EMA 9/21 crossovers, optionally trend-aligned with EMA50
    up = _cross_up(f["ema9"], f["ema21"]) & same_day
    dn = _cross_dn(f["ema9"], f["ema21"]) & same_day
    sig["EMA9x21"] = (up, dn)
    sig["EMA9x21_trend"] = (up & (c > f["ema50"]), dn & (c < f["ema50"]))

    # 3. RSI momentum (60/40) and mean-reversion (exit from 30/70)
    r = f["rsi14"]
    sig["RSI_mom"] = (_cross_up(r, 60) & same_day, _cross_dn(r, 40) & same_day)
    sig["RSI_rev"] = (_cross_up(r, 30) & same_day, _cross_dn(r, 70) & same_day)

    # 4. Bollinger squeeze -> expansion (bandwidth in bottom quintile, close outside band)
    squeeze = f["bb_bw_pct"].shift(1) < 20
    sig["BB_squeeze_break"] = ((c > f["bb_up"]) & squeeze & same_day, (c < f["bb_dn"]) & squeeze & same_day)

    # 5. Range-expansion bar (5-min range > 2.5x ATR14) in the bar's direction
    big = f["bar_range"] > 2.5 * f["atr14"].shift(1)
    sig["RangeExp"] = (big & (c > o) & same_day, big & (c < o) & same_day)

    # 6. Previous-day high/low breaks (first close beyond, any time)
    sig["PDH_PDL"] = (_first_in_day(c > f["prev_high"], d), _first_in_day(c < f["prev_low"], d))

    # 7. Session-mean (VWAP proxy) reclaim / loss, after 10:00
    late = f["minute"] >= 45
    sig["SessMean_cross"] = (_cross_up(c, f["sess_mean"]) & late, _cross_dn(c, f["sess_mean"]) & late)

    # 8. Late-day breakout of the day's range (after 14:15), first occurrence
    late2 = f["minute"] >= 300
    sig["LateBreak_1415"] = (_first_in_day((c > f["day_high_prev"]) & late2, d),
                             _first_in_day((c < f["day_low_prev"]) & late2, d))
    # 9. Early breakout of day's range after first hour but before 12:00 (10:15-12:00)
    mid = (f["minute"] >= 60) & (f["minute"] < 165)
    sig["MidBreak_1015_1200"] = (_first_in_day((c > f["day_high_prev"]) & mid, d),
                                 _first_in_day((c < f["day_low_prev"]) & mid, d))

    # 10. Baseline: every bar, both directions (random-direction reference)
    allbars = same_day & (f["minute"] < 345)
    sig["BASELINE_every_bar"] = (allbars, allbars)
    return sig


def build_events(f: pd.DataFrame, only_expiry: bool | None = None) -> pd.DataFrame:
    """Flatten the signal library into an event table."""
    lib = signal_library(f)
    ctx_cols = ["date", "minute", "close", "is_expiry", "is_monthly", "cas", "gap_pct", "gap_abs",
                "rv20", "range20", "vol_regime", "or30_rel", "or30_width_pct", "bar_idx"]
    rows = []
    for name, (lm, sm) in lib.items():
        for direction, mask in ((1, lm), (-1, sm)):
            e = f.loc[mask.fillna(False), ctx_cols].copy()
            e["signal"] = name
            e["direction"] = direction
            rows.append(e)
    ev = pd.concat(rows, ignore_index=True)
    if only_expiry is not None:
        ev = ev[ev["is_expiry"] == only_expiry]
    ev["entry_minute"] = ev["minute"] + 4          # 5-min bar close (1-min bar index)
    ev["hour_bucket"] = pd.cut(ev["entry_minute"], [-1, 44, 104, 164, 224, 284, 344, 375],
                               labels=["09:15-10", "10-11", "11-12", "12-13", "13-14", "14-15", "15-15:30"])
    return ev.reset_index(drop=True)
