"""Bar-level dataset for the buy-probability model.

One row per 5-minute bar (entry at the bar close).  Features use only information
available at that moment; labels come from following the ATM options along the
real 1-minute index path for the next 60 minutes with the synthetic 0DTE pricer.

Labels
  y_straddle30 : ATM straddle bought now touches >= +30% within 60 min   (WHEN to buy)
  y_up60       : index is higher 60 min later                            (WHICH side)
  ce_mfe60 / pe_mfe60 / ce_ret60 / pe_ret60 : single-leg outcomes for P&L evaluation
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CONTRACT, COST_PER_SIDE, SESSION_MINUTES, VRP_MULTIPLIER
from .features import add_features
from .options import bs_price, reactive_sigma_path, remaining_share, sigma_day_from_rv

HORIZON = 60
FEATURES = ["tod", "is_expiry", "is_monthly", "gap_abs", "gap_signed", "rv20_rank", "rv20", "or30_rel",
            "range_sofar_rel", "pos_in_range", "dist_high_atr", "dist_low_atr", "ret15", "ret30", "ret60",
            "bar_range_atr", "rsi14", "bb_bw_pct", "ema_spread_atr", "var_spent_ratio", "new_high", "new_low",
            "or_break_up", "or_break_dn", "weekday"]


def build_dataset(df1: pd.DataFrame, b5: pd.DataFrame, daily: pd.DataFrame, index: str, profile: np.ndarray,
                  dates=None, cas: bool = False, reactive_iv: bool = True) -> pd.DataFrame:
    step = CONTRACT[index]["strike_step"]
    f = add_features(b5, daily)
    if dates is not None:
        keep = set(pd.to_datetime(pd.Series(list(dates))).dt.date)
        f = f[f["date"].isin(keep)]
    R = remaining_share(profile, cas=cas)
    cum_prof = np.concatenate([[0.0], np.cumsum(profile)])          # share of variance elapsed by minute m
    exit_min = 370 if not cas else 360
    dd = daily.copy()
    dd.index = dd.index.date
    by_day = {d: g for d, g in df1.groupby("date")}
    rows = []
    for date, fd in f.groupby("date", sort=False):
        if date not in by_day or date not in dd.index or not np.isfinite(dd.at[date, "rv20"]):
            continue
        g = by_day[date]
        S = np.full(SESSION_MINUTES, np.nan)
        S[g["minute"].values] = g["close"].values
        S = pd.Series(S).ffill().bfill().values
        sigma0 = sigma_day_from_rv(dd.at[date, "rv20"])
        sig_m = reactive_sigma_path(S, profile, sigma0) if reactive_iv else np.full(SESSION_MINUTES, sigma0)
        V_after = sig_m ** 2 * R[1:SESSION_MINUTES + 1]
        fd = fd.sort_values("minute")
        # realised variance so far from 5-min closes (first bar: open->close), matching what a chart can compute
        c5 = fd["close"].values
        r5 = np.log(c5 / np.concatenate([[fd["open"].values[0]], c5[:-1]]))
        r2cum5 = np.cumsum(r5 ** 2)
        cache = {}

        def path(K, kind):
            key = (K, kind)
            if key not in cache:
                cache[key] = bs_price(S, np.full(SESSION_MINUTES, K), V_after, kind)
            return cache[key]

        closes = fd["close"].values
        highs, lows = fd["high"].values, fd["low"].values
        mins = fd["minute"].values.astype(int)
        atr_a = fd["atr14"].values
        cols = {c: fd[c].values for c in ["is_expiry", "is_monthly", "gap_pct", "rv20", "or30_rel", "day_open",
                                          "range20", "rsi14", "bb_bw_pct", "ema9", "ema21", "or30_high", "or30_low"]}
        rv_rank = dd.at[date, "rv20_rank"] if "rv20_rank" in dd.columns else np.nan
        wday = pd.Timestamp(date).dayofweek
        day_hi = np.maximum.accumulate(highs)
        day_lo = np.minimum.accumulate(lows)
        prev_hi = np.concatenate([[np.nan], day_hi[:-1]])
        prev_lo = np.concatenate([[np.nan], day_lo[:-1]])
        n = len(fd)
        for i in range(n):
            m0 = mins[i] + 4
            if m0 > exit_min - 15:
                break
            S0 = S[m0]
            K = float(np.round(S0 / step) * step)
            ce, pe = path(K, "CE"), path(K, "PE")
            m1 = min(m0 + HORIZON, exit_min)
            st = ce + pe
            st0, ce0, pe0 = st[m0], ce[m0], pe[m0]
            seg_s, seg_c, seg_p = st[m0:m1 + 1], ce[m0:m1 + 1], pe[m0:m1 + 1]
            idx_seg = S[m0:m1 + 1]
            atr = atr_a[i] if np.isfinite(atr_a[i]) and atr_a[i] > 0 else np.nan
            rng = (day_hi[i] - day_lo[i])
            exp_var = sigma0 ** 2 * max(cum_prof[m0 + 1], 1e-6)
            range20 = cols["range20"][i]
            rows.append({
                "date": date, "minute": m0, "S0": S0, "K": K, "sigma_day": sigma0,
                "tod": m0 / SESSION_MINUTES,
                "is_expiry": int(bool(cols["is_expiry"][i])), "is_monthly": int(bool(cols["is_monthly"][i])),
                "gap_abs": abs(cols["gap_pct"][i]), "gap_signed": cols["gap_pct"][i],
                "rv20_rank": rv_rank, "rv20": cols["rv20"][i], "or30_rel": cols["or30_rel"][i] if i >= 5 else np.nan,
                "range_sofar_rel": rng / cols["day_open"][i] * 100 / range20 if range20 > 0 else np.nan,
                "pos_in_range": (closes[i] - day_lo[i]) / rng if rng > 0 else 0.5,
                "dist_high_atr": (day_hi[i] - closes[i]) / atr if atr else np.nan,
                "dist_low_atr": (closes[i] - day_lo[i]) / atr if atr else np.nan,
                "ret15": (closes[i] / closes[i - 3] - 1) * 100 if i >= 3 else 0.0,
                "ret30": (closes[i] / closes[i - 6] - 1) * 100 if i >= 6 else 0.0,
                "ret60": (closes[i] / closes[i - 12] - 1) * 100 if i >= 12 else 0.0,
                "bar_range_atr": (highs[i] - lows[i]) / atr if atr else np.nan,
                "rsi14": cols["rsi14"][i], "bb_bw_pct": cols["bb_bw_pct"][i],
                "ema_spread_atr": (cols["ema9"][i] - cols["ema21"][i]) / atr if atr else np.nan,
                "var_spent_ratio": r2cum5[i] / exp_var,
                "new_high": int(i > 0 and closes[i] > prev_hi[i]), "new_low": int(i > 0 and closes[i] < prev_lo[i]),
                "or_break_up": int(i >= 6 and closes[i] > cols["or30_high"][i] and (i == 6 or closes[i - 1] <= cols["or30_high"][i])),
                "or_break_dn": int(i >= 6 and closes[i] < cols["or30_low"][i] and (i == 6 or closes[i - 1] >= cols["or30_low"][i])),
                "weekday": wday,
                "st0": st0, "ce0": ce0, "pe0": pe0,
                "st_mfe60": seg_s.max() / st0 - 1, "st_ret60": seg_s[-1] / st0 - 1,
                "ce_mfe60": seg_c.max() / ce0 - 1, "ce_ret60": seg_c[-1] / ce0 - 1,
                "pe_mfe60": seg_p.max() / pe0 - 1, "pe_ret60": seg_p[-1] / pe0 - 1,
                "idx_ret60": (idx_seg[-1] / S0 - 1) * 100,
                "idx_mfe_up60": (idx_seg.max() / S0 - 1) * 100, "idx_mfe_dn60": (1 - idx_seg.min() / S0) * 100,
            })
    out = pd.DataFrame(rows)
    out["y_straddle30"] = (out["st_mfe60"] >= 0.30).astype(int)
    out["y_up60"] = (out["idx_ret60"] > 0).astype(int)
    out["y_leg50"] = (np.maximum(out["ce_mfe60"], out["pe_mfe60"]) >= 0.50).astype(int)   # best leg (hindsight)
    return out
