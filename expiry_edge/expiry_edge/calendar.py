"""Expiry-day calendar built from the trading days actually present in the data.

Rule (NSE/BSE): weekly contracts expire on the regime weekday; if that day is a
trading holiday the expiry moves to the *previous* trading day. Monthly expiry is
the last regime-weekday of the month (same holiday rule).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .config import CAS_START_DATE, MONTHLY_WEEKDAY, REGIMES


def _regime_weekday(index: str, day: dt.date):
    for start, end, wd, _ in REGIMES[index]:
        if day >= start and (end is None or day < end):
            return wd, True
    return None, False


def _monthly_weekday(index: str, day: dt.date):
    for start, end, wd in MONTHLY_WEEKDAY[index]:
        if day >= start and (end is None or day < end):
            return wd
    return None


def label_expiry_days(trading_days: pd.Series | list, index: str) -> pd.DataFrame:
    """Return a DataFrame indexed by trading date with columns:
       is_expiry (weekly or monthly), is_monthly, expiry_weekday, regime_note, cas (bool)."""
    days = sorted(pd.to_datetime(pd.Series(list(trading_days))).dt.date.unique())
    dset = set(days)
    rows = {}
    for d in days:
        rows[d] = {"is_expiry": False, "is_monthly": False, "weekday": d.weekday(),
                   "cas": d >= CAS_START_DATE}
    # --- weekly expiries: for each week, the scheduled weekday or the previous trading day
    def prev_trading(day: dt.date, floor: dt.date):
        x = day
        while x >= floor:
            if x in dset:
                return x
            x -= dt.timedelta(days=1)
        return None

    seen_weeks = set()
    for d in days:
        wd, has_weekly = _regime_weekday(index, d)
        if not has_weekly or wd is None:
            continue
        monday = d - dt.timedelta(days=d.weekday())
        key = (monday, wd)
        if key in seen_weeks:
            continue
        seen_weeks.add(key)
        sched = monday + dt.timedelta(days=wd)
        # regime must apply on the scheduled day too
        wd2, ok2 = _regime_weekday(index, sched)
        if not ok2 or wd2 != wd:
            continue
        actual = prev_trading(sched, monday)
        if actual is not None and actual in rows:
            rows[actual]["is_expiry"] = True
    # --- monthly expiries: last regime weekday of each month (holiday -> previous trading day)
    months = sorted({(d.year, d.month) for d in days})
    for (y, m) in months:
        last_day = (dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1))
        wd = _monthly_weekday(index, last_day)
        if wd is None:
            continue
        x = last_day
        while x.weekday() != wd:
            x -= dt.timedelta(days=1)
        actual = prev_trading(x, x - dt.timedelta(days=6))
        if actual is not None and actual in rows:
            rows[actual]["is_expiry"] = True
            rows[actual]["is_monthly"] = True
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    out["expiry_weekday"] = out.index.dayofweek.where(out["is_expiry"])
    return out


def regime_note(index: str, day: dt.date) -> str:
    for start, end, wd, note in REGIMES[index]:
        if day >= start and (end is None or day < end):
            return note
    return "no weekly options"
