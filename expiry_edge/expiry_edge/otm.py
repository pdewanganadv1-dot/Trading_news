"""Cheap OTM 'blast' analysis: what happens to 1-, 2- and 3-strike OTM options bought
at each 5-min bar of an expiry day.

Outcomes per (bar, side, strikes-OTM):
    p0           entry premium (model price with a realistic floor)
    mult60       max premium within 60 min / p0      (did it 3x / 5x / 10x?)
    ret60        premium at +60 min / p0 - 1
    mult_close   max premium until the exit deadline / p0
    ret_close    premium at the exit deadline / p0 - 1

Premium floor: deep-OTM expiry-day options never trade at the Black-Scholes value of a
few paise — there is always a tail bid (NIFTY ~1-2 pts, SENSEX ~3-5).  We apply
max(model, FLOOR) to entry and exit alike; it makes cheap options cost more and lose
less than the bare model, which is the conservative direction for a blast study.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CONTRACT, COST_PER_SIDE, SESSION_MINUTES
from .options import bs_price, reactive_sigma_path, remaining_share, sigma_day_from_rv

PREMIUM_FLOOR = {"NIFTY": 1.5, "BANKNIFTY": 3.0, "SENSEX": 5.0}
HORIZON = 60
STEPS = (1, 2, 3)


def otm_outcomes(df1: pd.DataFrame, bars: pd.DataFrame, daily: pd.DataFrame, index: str, profile: np.ndarray,
                 cas: bool = False, floor: float | None = None, steps=STEPS) -> pd.DataFrame:
    """bars: the model dataset rows (from model.build_dataset) for the days to evaluate —
    provides minute, new_high/new_low and the score features.  Returns one row per
    (date, minute, side, k)."""
    step = CONTRACT[index]["strike_step"]
    floor = PREMIUM_FLOOR[index] if floor is None else floor
    R = remaining_share(profile, cas=cas)
    exit_min = 370 if not cas else 360
    dd = daily.copy(); dd.index = dd.index.date
    by_day = {d: g for d, g in df1.groupby("date")}
    rows = []
    for date, bd in bars.groupby("date", sort=False):
        date_ = pd.Timestamp(date).date()
        if date_ not in by_day or not np.isfinite(dd.at[date_, "rv20"]):
            continue
        g = by_day[date_]
        S = np.full(SESSION_MINUTES, np.nan); S[g["minute"].values] = g["close"].values
        S = pd.Series(S).ffill().bfill().values
        sigma0 = sigma_day_from_rv(dd.at[date_, "rv20"])
        sig_m = reactive_sigma_path(S, profile, sigma0)
        V_after = sig_m ** 2 * R[1:SESSION_MINUTES + 1]
        cache = {}

        def path(K, kind):
            key = (K, kind)
            if key not in cache:
                cache[key] = np.maximum(bs_price(S, np.full(SESSION_MINUTES, K), V_after, kind), floor)
            return cache[key]

        for r in bd.itertuples(index=False):
            m0 = int(r.minute)
            if m0 > exit_min - 15:
                continue
            S0 = S[m0]
            K0 = float(np.round(S0 / step) * step)
            m1 = min(m0 + HORIZON, exit_min)
            for side, kind, sgn in (("CE", "CE", 1), ("PE", "PE", -1)):
                for k in steps:
                    K = K0 + sgn * k * step
                    P = path(K, kind)
                    p0 = P[m0]
                    seg = P[m0:m1 + 1]
                    segc = P[m0:exit_min + 1]
                    rows.append({"date": date_, "minute": m0, "side": side, "k": k, "K": K, "S0": S0,
                                 "otm_pts": sgn * (K - S0), "p0": p0,
                                 "mult60": seg.max() / p0, "ret60": seg[-1] / p0 - 1,
                                 "mult_close": segc.max() / p0, "ret_close": segc[-1] / p0 - 1,
                                 "new_high": int(r.new_high), "new_low": int(r.new_low),
                                 "score": getattr(r, "score", np.nan), "is_expiry": int(r.is_expiry)})
    out = pd.DataFrame(rows)
    cost = COST_PER_SIDE[index]
    out["net60"] = ((out["p0"] * (1 + out["ret60"]) - cost) / (out["p0"] + cost)) - 1
    out["net_close"] = ((out["p0"] * (1 + out["ret_close"]) - cost) / (out["p0"] + cost)) - 1
    # a 'take the blast' exit: sell at 5x if touched within 60 min, else at 60 min
    hit5 = out["mult60"] >= 5
    out["net_take5"] = np.where(hit5, (5 * out["p0"] - cost) / (out["p0"] + cost) - 1, out["net60"])
    hit3 = out["mult60"] >= 3
    out["net_take3"] = np.where(hit3, (3 * out["p0"] - cost) / (out["p0"] + cost) - 1, out["net60"])
    out["hour"] = pd.cut(out["minute"], [-1, 104, 164, 224, 284, 344, 375],
                         labels=["09:15-11", "11-12", "12-13", "13-14", "14-15", "15-15:30"])
    return out


def blast_table(o: pd.DataFrame, by, tag: str = "60") -> pd.DataFrame:
    mult = f"mult{tag}" if tag == "60" else "mult_close"
    net = "net60" if tag == "60" else "net_close"
    g = o.groupby(list(by), observed=True)
    t = pd.DataFrame({
        "n": g.size(),
        "p0_med": g["p0"].median(),
        "p_2x": g[mult].apply(lambda s: (s >= 2).mean() * 100),
        "p_3x": g[mult].apply(lambda s: (s >= 3).mean() * 100),
        "p_5x": g[mult].apply(lambda s: (s >= 5).mean() * 100),
        "p_10x": g[mult].apply(lambda s: (s >= 10).mean() * 100),
        "mult_med": g[mult].median(),
        "net_mean": g[net].mean() * 100,
        "net_median": g[net].median() * 100,
        "p_net_pos": g[net].apply(lambda s: (s > 0).mean() * 100),
        "p_lose_half": g[net].apply(lambda s: (s <= -0.5).mean() * 100),
        "take3_mean": g["net_take3"].mean() * 100,
        "take5_mean": g["net_take5"].mean() * 100,
    })
    return t.round(2)
