"""Day-level straddle strategies (seller and buyer side) on expiry days.

All use the synthetic 0DTE pricer along the real index path.  Returns are in index
points per unit of straddle and as % of the opening premium, net of costs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CONTRACT, COST_PER_SIDE, SESSION_MINUTES
from .options import bs_price, reactive_sigma_path, remaining_share, sigma_day_from_rv


def _S_of_day(g: pd.DataFrame) -> np.ndarray:
    S = np.full(SESSION_MINUTES, np.nan)
    S[g["minute"].values] = g["close"].values
    return pd.Series(S).ffill().bfill().values


def straddle_paths(df1: pd.DataFrame, daily: pd.DataFrame, index: str, profile: np.ndarray,
                   dates, entry_minutes=(5, 45, 225, 315, 345), cas: bool = False,
                   reactive_iv: bool = True, vrp: float | None = None) -> pd.DataFrame:
    """For each date and entry minute: the ATM straddle's minute-by-minute value from
    entry to the close.  Returns long-form rows: date, entry_minute, minute, value,
    plus S0/K/sigma.  entry_minutes default: 09:20, 10:00, 13:00, 14:30, 15:00."""
    step = CONTRACT[index]["strike_step"]
    R = remaining_share(profile, cas=cas)
    dd = daily.copy()
    dd.index = dd.index.date
    by_day = {d: g for d, g in df1.groupby("date")}
    rows = []
    for date in pd.to_datetime(pd.Series(list(dates))).dt.date:
        if date not in by_day or date not in dd.index or not np.isfinite(dd.at[date, "rv20"]):
            continue
        S = _S_of_day(by_day[date])
        sigma0 = sigma_day_from_rv(dd.at[date, "rv20"]) if vrp is None else sigma_day_from_rv(dd.at[date, "rv20"], vrp)
        sig_m = reactive_sigma_path(S, profile, sigma0) if reactive_iv else np.full(SESSION_MINUTES, sigma0)
        V_after = sig_m ** 2 * R[1:SESSION_MINUTES + 1]
        for m0 in entry_minutes:
            K = float(np.round(S[m0] / step) * step)
            val = bs_price(S, np.full(SESSION_MINUTES, K), V_after, "CE") + \
                  bs_price(S, np.full(SESSION_MINUTES, K), V_after, "PE")
            rows.append({"date": date, "entry_minute": m0, "K": K, "S0": S[m0], "sigma0": sigma0,
                         "path": val[m0:], "S_path": S[m0:]})
    return pd.DataFrame(rows)


def short_straddle_pnl(paths: pd.DataFrame, index: str, exit_minute: int = 360,
                       sl_pct: float | None = 0.30) -> pd.DataFrame:
    """Short ATM straddle at entry, buy back at exit_minute (default 15:15) or when the
    combined premium is up `sl_pct` (stop-loss).  Net of costs (2 legs x 2 sides)."""
    cost = COST_PER_SIDE[index] * 4
    out = []
    for r in paths.itertuples(index=False):
        p = r.path
        n_exit = min(exit_minute - r.entry_minute, len(p) - 1)
        seg = p[: n_exit + 1]
        p0 = seg[0]
        why = "time"
        exit_px = seg[-1]
        if sl_pct is not None:
            hit = np.where(seg[1:] >= p0 * (1 + sl_pct))[0]
            if len(hit):
                exit_px, why = p0 * (1 + sl_pct), "sl"
        pnl = p0 - exit_px - cost
        out.append({"date": r.date, "entry_minute": r.entry_minute, "premium0": p0, "pnl_pts": pnl,
                    "pnl_pct": pnl / p0 * 100, "exit": why, "S0": r.S0, "K": r.K,
                    "idx_move_pct": (r.S_path[n_exit] / r.S0 - 1) * 100})
    return pd.DataFrame(out)


def long_straddle_pnl(paths: pd.DataFrame, index: str, exit_minute: int = 370) -> pd.DataFrame:
    """Long ATM straddle at entry, sell at exit_minute (default 15:25 pre-CAS)."""
    cost = COST_PER_SIDE[index] * 4
    out = []
    for r in paths.itertuples(index=False):
        p = r.path
        n_exit = min(exit_minute - r.entry_minute, len(p) - 1)
        p0 = p[0]
        pnl = p[n_exit] - p0 - cost
        mfe = p[: n_exit + 1].max() / p0 - 1
        out.append({"date": r.date, "entry_minute": r.entry_minute, "premium0": p0, "pnl_pts": pnl,
                    "pnl_pct": pnl / p0 * 100, "mfe_pct": mfe * 100, "S0": r.S0,
                    "idx_move_pct": (r.S_path[n_exit] / r.S0 - 1) * 100})
    return pd.DataFrame(out)


def summarize_pnl(df: pd.DataFrame, by=("entry_minute",)) -> pd.DataFrame:
    g = df.groupby(list(by), observed=True)
    t = pd.DataFrame({
        "n": g.size(),
        "win_rate": g["pnl_pts"].apply(lambda s: (s > 0).mean() * 100),
        "mean_pct": g["pnl_pct"].mean(),
        "median_pct": g["pnl_pct"].median(),
        "mean_pts": g["pnl_pts"].mean(),
        "p05_pct": g["pnl_pct"].quantile(0.05),
        "p95_pct": g["pnl_pct"].quantile(0.95),
        "worst_pct": g["pnl_pct"].min(),
        "premium0_med": g["premium0"].median(),
    })
    # profit factor
    pf = g["pnl_pts"].apply(lambda s: s[s > 0].sum() / max(-s[s < 0].sum(), 1e-9))
    t["profit_factor"] = pf
    return t.round(2)
