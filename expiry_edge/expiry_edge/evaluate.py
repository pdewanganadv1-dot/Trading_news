"""Outcome evaluation: what happened to an option bought at each signal.

For every event we buy the ATM (and 1-strike-OTM) option in the signal direction at
the 5-min bar close and follow it minute by minute along the real index path with
the synthetic 0DTE pricer.  We record fixed-horizon returns, maximum favourable
excursion (did the premium ever reach +50% / +100%?), a bracket exit (SL -30% /
TP +50% / 60-min time stop), and pure index-move statistics that need no model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CONTRACT, COST_PER_SIDE, SESSION_MINUTES
from .options import bs_price, reactive_sigma_path, remaining_share, sigma_day_from_rv

HORIZONS = (15, 30, 60)
EXIT_MINUTE_PRECAS = 370      # 15:25 – last minute we assume an option can be exited pre-CAS
SL, TP, TIME_STOP = -0.30, 0.50, 60


def _day_arrays(df1_day: pd.DataFrame):
    S = np.full(SESSION_MINUTES, np.nan)
    S[df1_day["minute"].values] = df1_day["close"].values
    S = pd.Series(S).ffill().bfill().values
    return S


def evaluate_events(events: pd.DataFrame, df1: pd.DataFrame, daily: pd.DataFrame, index: str,
                    profile: np.ndarray, cas: bool = False, sigma_col: str = "rv20",
                    reactive_iv: bool = True, vrp: float | None = None) -> pd.DataFrame:
    """Append outcome columns to `events` (one row per event).
    reactive_iv=True lets the day's implied vol update with realised vol (see
    options.reactive_sigma_path); False keeps it fixed at the opening level."""
    step = CONTRACT[index]["strike_step"]
    cost = COST_PER_SIDE[index]
    R = remaining_share(profile, cas=cas)
    exit_min = EXIT_MINUTE_PRECAS if not cas else 360      # under CAS decide by 15:15
    by_day = {d: g for d, g in df1.groupby("date")}
    dd = daily.copy()
    dd.index = dd.index.date
    out_rows = []
    for date, ev_day in events.groupby("date", sort=False):
        if date not in by_day or date not in dd.index:
            continue
        S = _day_arrays(by_day[date])
        rv = dd.at[date, sigma_col]
        if not np.isfinite(rv):
            continue
        sigma = (sigma_day_from_rv(rv) if vrp is None else sigma_day_from_rv(rv, vrp)) if sigma_col == "rv20" else rv
        sig_m = reactive_sigma_path(S, profile, sigma) if reactive_iv else np.full(SESSION_MINUTES, sigma)
        V_after = sig_m ** 2 * R[1:SESSION_MINUTES + 1]           # variance remaining after each minute's close
        cache = {}

        def path(K, kind):
            key = (K, kind)
            if key not in cache:
                cache[key] = bs_price(S, np.full(SESSION_MINUTES, K), V_after, kind)
            return cache[key]

        for r in ev_day.itertuples(index=False):
            m0 = int(r.entry_minute)
            if m0 >= exit_min - 5:
                continue
            S0 = S[m0]
            kind = "CE" if r.direction == 1 else "PE"
            K_atm = float(np.round(S0 / step) * step)
            K_otm = K_atm + step * r.direction
            rec = r._asdict()
            rec.update({"S0": S0, "K_atm": K_atm, "sigma_day": sigma})
            # ---- index-only stats (model free)
            for H in HORIZONS:
                m1 = min(m0 + H, exit_min)
                seg = S[m0:m1 + 1]
                fav = (seg - S0) * r.direction / S0 * 100
                rec[f"idx_ret{H}"] = fav[-1]
                rec[f"idx_mfe{H}"] = fav.max()
                rec[f"idx_mae{H}"] = fav.min()
            rec["idx_ret_close"] = (S[exit_min] - S0) * r.direction / S0 * 100
            # ---- option stats
            for tag, K in (("atm", K_atm), ("otm", K_otm)):
                P = path(K, kind)
                p0 = P[m0]
                rec[f"{tag}_p0"] = p0
                for H in HORIZONS:
                    m1 = min(m0 + H, exit_min)
                    seg = P[m0:m1 + 1]
                    rec[f"{tag}_ret{H}"] = seg[-1] / p0 - 1
                    rec[f"{tag}_mfe{H}"] = seg.max() / p0 - 1
                    rec[f"{tag}_mae{H}"] = seg.min() / p0 - 1
                rec[f"{tag}_ret_close"] = P[exit_min] / p0 - 1
                # bracket exit on mid prices, then costs
                seg = P[m0 + 1: min(m0 + TIME_STOP, exit_min) + 1]
                ratio = seg / p0 - 1
                hit_sl = np.where(ratio <= SL)[0]
                hit_tp = np.where(ratio >= TP)[0]
                i_sl = hit_sl[0] if len(hit_sl) else 10 ** 6
                i_tp = hit_tp[0] if len(hit_tp) else 10 ** 6
                if i_sl == i_tp == 10 ** 6:
                    exit_px, why = seg[-1], "time"
                elif i_tp < i_sl:
                    exit_px, why = p0 * (1 + TP), "tp"
                else:
                    exit_px, why = p0 * (1 + SL), "sl"
                rec[f"{tag}_bracket_ret"] = (exit_px - cost) / (p0 + cost) - 1
                rec[f"{tag}_bracket_pts"] = (exit_px - cost) - (p0 + cost)
                rec[f"{tag}_bracket_exit"] = why
                rec[f"{tag}_ret60_net"] = (P[min(m0 + 60, exit_min)] - cost) / (p0 + cost) - 1
            out_rows.append(rec)
    return pd.DataFrame(out_rows)


def summarize(res: pd.DataFrame, by=("signal",), tag: str = "atm") -> pd.DataFrame:
    """Per-group statistics for buyers."""
    g = res.groupby(list(by), observed=True)
    t = pd.DataFrame({
        "n": g.size(),
        "days": g["date"].nunique(),
        "idx_hit30": g["idx_ret30"].apply(lambda s: (s > 0).mean() * 100),
        "idx_mfe60_med": g["idx_mfe60"].median(),
        "p_mfe30_ge50": g[f"{tag}_mfe30"].apply(lambda s: (s >= 0.5).mean() * 100),
        "p_mfe60_ge50": g[f"{tag}_mfe60"].apply(lambda s: (s >= 0.5).mean() * 100),
        "p_mfe60_ge100": g[f"{tag}_mfe60"].apply(lambda s: (s >= 1.0).mean() * 100),
        "mfe60_med": g[f"{tag}_mfe60"].median() * 100,
        "ret30_mean": g[f"{tag}_ret30"].mean() * 100,
        "ret60_mean": g[f"{tag}_ret60"].mean() * 100,
        "ret60_net_mean": g[f"{tag}_ret60_net"].mean() * 100,
        "p_ret60_pos": g[f"{tag}_ret60"].apply(lambda s: (s > 0).mean() * 100),
        "bracket_mean": g[f"{tag}_bracket_ret"].mean() * 100,
        "bracket_win": g[f"{tag}_bracket_exit"].apply(lambda s: (s == "tp").mean() * 100),
        "bracket_pts_mean": g[f"{tag}_bracket_pts"].mean(),
        "p0_med": g[f"{tag}_p0"].median(),
    })
    return t.round(2)
