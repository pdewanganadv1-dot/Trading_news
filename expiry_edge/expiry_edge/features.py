"""Technical features on 5-minute index bars + daily context.

All indicators are computed with only past information at each bar close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def add_features(b5: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """b5: 5-min bars [ts,date,minute,open,high,low,close]; daily: daily_table()+calendar."""
    b = b5.copy().reset_index(drop=True)
    c = b["close"]
    # --- continuous-series indicators
    b["ema9"] = ema(c, 9)
    b["ema21"] = ema(c, 21)
    b["ema50"] = ema(c, 50)
    b["rsi14"] = rsi(c, 14)
    tr = true_range(b)
    b["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    b["bb_up"] = mid + 2 * sd
    b["bb_dn"] = mid - 2 * sd
    b["bb_bw"] = (b["bb_up"] - b["bb_dn"]) / mid * 100
    b["bb_bw_pct"] = b["bb_bw"].rolling(150).rank(pct=True) * 100     # ~2 days of bars
    b["bar_range"] = b["high"] - b["low"]
    # --- session (per-day) features
    g = b.groupby("date", sort=False)
    b["bar_idx"] = g.cumcount()
    b["sess_mean"] = g["close"].transform(lambda s: s.expanding().mean())    # VWAP proxy (no volume)
    b["day_high_prev"] = g["high"].transform(lambda s: s.cummax().shift(1))
    b["day_low_prev"] = g["low"].transform(lambda s: s.cummin().shift(1))
    b["or15_high"] = g["high"].transform(lambda s: s.iloc[:3].max())
    b["or15_low"] = g["low"].transform(lambda s: s.iloc[:3].min())
    b["or30_high"] = g["high"].transform(lambda s: s.iloc[:6].max())
    b["or30_low"] = g["low"].transform(lambda s: s.iloc[:6].min())
    b["day_open"] = g["open"].transform("first")
    # --- daily context
    dd = daily.copy()
    dd.index = dd.index.date
    ctx = dd[["prev_close", "prev_high", "prev_low", "gap_pct", "rv20", "range20", "vol_regime",
              "is_expiry", "is_monthly", "cas"]]
    b = b.join(ctx, on="date")
    b["or30_width_pct"] = (b["or30_high"] - b["or30_low"]) / b["day_open"] * 100
    b["or30_rel"] = b["or30_width_pct"] / b["range20"]          # opening range vs typical day range
    b["gap_abs"] = b["gap_pct"].abs()
    return b
