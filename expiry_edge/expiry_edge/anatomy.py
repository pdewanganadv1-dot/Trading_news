"""Model-free anatomy of expiry days: where in the day the movement happens, how big
the last hour is, and how the realised day compares with the opening straddle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SESSION_MINUTES
from .options import atm_strike, remaining_share, sigma_day_from_rv, straddle_price

BUCKETS = [(0, 15, "09:15-09:30"), (15, 30, "09:30-09:45"), (30, 60, "09:45-10:15"), (60, 120, "10:15-11:15"),
           (120, 180, "11:15-12:15"), (180, 240, "12:15-13:15"), (240, 300, "13:15-14:15"),
           (300, 345, "14:15-15:00"), (345, 360, "15:00-15:15"), (360, 375, "15:15-15:30")]


def bucket_table(profile_exp: np.ndarray, profile_non: np.ndarray) -> pd.DataFrame:
    rows = []
    for a, b, lab in BUCKETS:
        rows.append({"window": lab, "minutes": b - a, "expiry_var_share_pct": profile_exp[a:b].sum() * 100,
                     "nonexpiry_var_share_pct": profile_non[a:b].sum() * 100})
    t = pd.DataFrame(rows)
    t["expiry_var_per_min_bp"] = t["expiry_var_share_pct"] / t["minutes"] * 100
    t["nonexpiry_var_per_min_bp"] = t["nonexpiry_var_share_pct"] / t["minutes"] * 100
    t["ratio_expiry_vs_non"] = t["expiry_var_share_pct"] / t["nonexpiry_var_share_pct"]
    return t.round(2)


def day_stats(df1: pd.DataFrame, daily: pd.DataFrame, index: str, profile: np.ndarray) -> pd.DataFrame:
    """One row per session with intraday move statistics and the modelled opening straddle."""
    R = remaining_share(profile)
    rows = []
    dd = daily.copy()
    dd.index = dd.index.date
    for date, g in df1.groupby("date"):
        S = np.full(SESSION_MINUTES, np.nan)
        S[g["minute"].values] = g["close"].values
        S = pd.Series(S).ffill().bfill().values
        hi = np.full(SESSION_MINUTES, np.nan); hi[g["minute"].values] = g["high"].values
        lo = np.full(SESSION_MINUTES, np.nan); lo[g["minute"].values] = g["low"].values
        o = S[0]
        rec = {"date": date, "open": o, "close": S[-1],
               "range_pct": (np.nanmax(hi) - np.nanmin(lo)) / o * 100,
               "oc_move_pct": (S[-1] / o - 1) * 100,
               "fh_range_pct": (np.nanmax(hi[:60]) - np.nanmin(lo[:60])) / o * 100,
               "move_0915_1430": (S[315] / o - 1) * 100,
               "move_1430_1525": (S[370] / S[315] - 1) * 100,
               "move_1500_1525": (S[370] / S[345] - 1) * 100,
               "move_1415_1525": (S[370] / S[300] - 1) * 100,
               "range_1430_1525_pct": (np.nanmax(hi[315:371]) - np.nanmin(lo[315:371])) / S[315] * 100,
               "range_1500_1525_pct": (np.nanmax(hi[345:371]) - np.nanmin(lo[345:371])) / S[345] * 100}
        if date in dd.index and np.isfinite(dd.at[date, "rv20"]):
            sig = sigma_day_from_rv(dd.at[date, "rv20"])
            K = atm_strike(o, index)
            st = float(straddle_price(o, K, sig ** 2 * R[0]))
            rec["straddle_open_pts"] = st
            rec["straddle_open_pct"] = st / o * 100
            rec["sigma_day"] = sig
        rows.append(rec)
    out = pd.DataFrame(rows).set_index("date")
    out.index = pd.to_datetime(out.index)
    ctx = daily[["is_expiry", "is_monthly", "gap_pct", "rv20", "vol_regime", "range20"]]
    out = out.join(ctx)
    out["range_gt_straddle"] = out["range_pct"] > out["straddle_open_pct"]
    out["range_gt_2straddle"] = out["range_pct"] > 2 * out["straddle_open_pct"]
    out["close_outside_straddle"] = out["oc_move_pct"].abs() > out["straddle_open_pct"]
    out["last_hour_same_sign"] = np.sign(out["move_0915_1430"]) == np.sign(out["move_1430_1525"])
    return out


def anatomy_summary(ds: pd.DataFrame) -> pd.DataFrame:
    def agg(x: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "days": len(x),
            "range_pct_med": x["range_pct"].median(),
            "range_pct_mean": x["range_pct"].mean(),
            "fh_range_pct_med": x["fh_range_pct"].median(),
            "abs_move_1430_1525_med": x["move_1430_1525"].abs().median(),
            "abs_move_1430_1525_mean": x["move_1430_1525"].abs().mean(),
            "p_abs_last_hour_gt_0.3pct": (x["move_1430_1525"].abs() > 0.3).mean() * 100,
            "p_abs_last_hour_gt_0.5pct": (x["move_1430_1525"].abs() > 0.5).mean() * 100,
            "abs_move_1500_1525_med": x["move_1500_1525"].abs().median(),
            "p_abs_1500_1525_gt_0.25pct": (x["move_1500_1525"].abs() > 0.25).mean() * 100,
            "range_1430_1525_med": x["range_1430_1525_pct"].median(),
            "last_hour_same_sign_pct": x["last_hour_same_sign"].mean() * 100,
            "straddle_open_pct_med": x["straddle_open_pct"].median(),
            "p_range_gt_straddle": x["range_gt_straddle"].mean() * 100,
            "p_range_gt_2straddle": x["range_gt_2straddle"].mean() * 100,
            "p_close_outside_straddle": x["close_outside_straddle"].mean() * 100,
        })
    return ds.groupby("is_expiry").apply(agg).round(2)
