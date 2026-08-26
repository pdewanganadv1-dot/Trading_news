"""Synthetic 0DTE (expiry-day) index-option pricing.

Why synthetic?  Free intraday *option* data for NIFTY/SENSEX does not exist; the
free data is intraday *index* data.  On expiry day an index option is almost
entirely (a) intrinsic value, which is a deterministic function of the index path,
and (b) a fast-decaying time value.  We therefore price options along the *actual*
index path with a Black-Scholes kernel whose remaining variance follows the
empirical intraday variance profile of expiry days.  Directional/gamma P&L is
real; only the time-value component is modelled.  Calibration knobs:

    sigma_day  : the day's implied open-to-close vol (fraction).  Default: VRP x
                 trailing 20-session Parkinson realised vol.  Plug in India VIX or an
                 observed opening straddle if you have it (see `sigma_from_straddle`).
    profile    : minute-of-day variance weights v[m], m = 0..374, estimated from data.
    cas        : if True, a share `a` of the day's variance is reserved for the
                 closing auction (index frozen 15:15-15:35, options trade to 15:40).

Validation hooks: Zerodha's published observations (2026): opening ATM straddle
0.36-0.63% of spot; ~50% of the straddle premium still intact at 15:15 under CAS;
23-Jun-2026: 63% of the opening straddle left at 11:30 (pre-CAS).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from .config import CAS_AUCTION_VARIANCE_SHARE, CONTRACT, SESSION_MINUTES, VRP_MULTIPLIER


# ----------------------------------------------------------------------------
# Black-Scholes kernel with total variance V (r = 0, forward = spot for 0DTE)
# ----------------------------------------------------------------------------
def bs_price(S, K, V, kind: str):
    """Vectorised BS price with total variance V (= sigma^2 * T). kind 'CE' or 'PE'."""
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    V = np.asarray(V, dtype=float)
    intrinsic = np.maximum(S - K, 0.0) if kind == "CE" else np.maximum(K - S, 0.0)
    out = intrinsic.copy()
    m = V > 1e-12
    if np.any(m):
        sv = np.sqrt(V[m]) if V.ndim else np.sqrt(V)
        Sm = S[m] if S.ndim else S
        Km = K[m] if K.ndim else K
        d1 = (np.log(Sm / Km) + 0.5 * sv ** 2) / sv
        d2 = d1 - sv
        if kind == "CE":
            px = Sm * norm.cdf(d1) - Km * norm.cdf(d2)
        else:
            px = Km * norm.cdf(-d2) - Sm * norm.cdf(-d1)
        if out.ndim:
            out[m] = px
        else:
            out = px
    return np.maximum(out, 0.05)


def straddle_price(S, K, V):
    return bs_price(S, K, V, "CE") + bs_price(S, K, V, "PE")


# ----------------------------------------------------------------------------
# Intraday variance profile
# ----------------------------------------------------------------------------
def variance_profile(df1: pd.DataFrame, dates, trim: float = 0.05) -> np.ndarray:
    """Minute-of-day variance weights v[m] (sum = 1) estimated from squared 1-min log
    returns on the given dates.  Uses a trimmed mean per minute to reduce outlier
    influence, then a light 5-minute smoothing.  v[0] (09:15 bar) is the first bar's
    open->close move; the overnight gap is excluded."""
    sub = df1[df1["date"].isin(set(pd.to_datetime(pd.Series(list(dates))).dt.date))]
    r2 = (np.log(sub["close"] / sub["open"])) ** 2       # within-bar move
    # add the bar-to-bar move (close_{m-1} -> open_m) to the same minute
    prev_close = sub.groupby("date")["close"].shift(1)
    gap = np.log(sub["open"] / prev_close).fillna(0.0) ** 2
    tot = (r2 + gap).rename("r2")
    tbl = pd.DataFrame({"minute": sub["minute"].values, "r2": tot.values})
    def tmean(x):
        x = np.sort(x.values)
        k = int(len(x) * trim)
        return x[k: len(x) - k].mean() if len(x) > 2 * k + 1 else x.mean()
    v = tbl.groupby("minute")["r2"].apply(tmean).reindex(range(SESSION_MINUTES)).ffill().bfill().values
    v = pd.Series(v).rolling(5, center=True, min_periods=1).mean().values
    return v / v.sum()


def remaining_share(profile: np.ndarray, cas: bool = False,
                    auction_share: float = CAS_AUCTION_VARIANCE_SHARE) -> np.ndarray:
    """R[m] = share of the day's variance still ahead at the *open* of minute m.
    Returns an array of length 376 (index 375 = at the close, R=0 pre-CAS).
    Under CAS: continuous session ends at 15:15 (minute 360); the auction carries
    `auction_share` of the variance and resolves at 15:35."""
    v = profile.copy()
    if cas:
        v[360:] = 0.0
        v = v / v.sum() * (1 - auction_share)
        R = np.concatenate([np.cumsum(v[::-1])[::-1] + auction_share, [auction_share]])
        return R
    R = np.concatenate([np.cumsum(v[::-1])[::-1], [0.0]])
    return R


# ----------------------------------------------------------------------------
# Day-level vol and pricing along a path
# ----------------------------------------------------------------------------
def sigma_day_from_rv(rv20_pct: float, vrp: float = VRP_MULTIPLIER) -> float:
    """Implied open-to-close vol for the day (fraction) from trailing realised vol (%)."""
    return vrp * rv20_pct / 100.0


def sigma_from_straddle(straddle_pts: float, spot: float) -> float:
    """Back out sigma_day from an observed opening ATM straddle (BS ATM straddle ~ 0.8*S*sigma)."""
    return straddle_pts / (0.7979 * spot)


def sigma_from_vix(vix: float, intraday_share: float = 0.75) -> float:
    """India VIX (annualised %) -> expiry-day open-to-close sigma.  A 1-day variance
    from VIX includes the overnight gap; roughly `intraday_share` of daily variance is
    realised during the session."""
    return vix / 100.0 / np.sqrt(252.0) * np.sqrt(intraday_share)


def atm_strike(S: float, index: str) -> float:
    step = CONTRACT[index]["strike_step"]
    return float(np.round(S / step) * step)


def price_path(S_path: np.ndarray, minutes: np.ndarray, K: float, kind: str, sigma_day: float,
               R: np.ndarray) -> np.ndarray:
    """Option price at each bar of a day: S_path[i] observed at minute minutes[i]
    (price uses variance remaining *after* that bar's close => R[m+1])."""
    V = (sigma_day ** 2) * R[np.clip(minutes + 1, 0, len(R) - 1)]
    return bs_price(S_path, K, V, kind)


def reactive_sigma_path(S: np.ndarray, profile: np.ndarray, sigma0: float, lam: float = 0.5,
                        window: int = 30, floor: float = 0.5, cap: float = 3.0) -> np.ndarray:
    """Intraday-updating implied vol.  Real 0DTE premiums re-price within minutes when
    realised volatility jumps (a big 5-min bar fattens every strike).  We model
    sigma_m^2 = (1-lam) * sigma0^2 + lam * sigma_hat_m^2, where sigma_hat_m is the
    day-vol implied by the last `window` minutes of realised variance (scaled up by the
    profile share of those minutes).  Clamped to [floor, cap] x sigma0.  Returns one
    sigma per minute (length SESSION_MINUTES); the first `window` minutes use sigma0."""
    r = np.diff(np.log(S), prepend=np.log(S[0]))
    r2 = r ** 2
    cs = np.concatenate([[0.0], np.cumsum(r2)])
    cp = np.concatenate([[0.0], np.cumsum(profile)])
    m = np.arange(len(S))
    lo = np.maximum(m - window + 1, 0)
    rv_win = cs[m + 1] - cs[lo]
    share = np.maximum(cp[m + 1] - cp[lo], 1e-6)
    sig_hat2 = rv_win / share
    sig2 = (1 - lam) * sigma0 ** 2 + lam * sig_hat2
    sig = np.sqrt(sig2)
    sig[:window] = sigma0
    return np.clip(sig, floor * sigma0, cap * sigma0)


def theoretical_decay_curve(sigma_day: float, spot: float, index: str, R: np.ndarray) -> pd.DataFrame:
    """Straddle value through the day if the index does not move (pure time decay)."""
    K = atm_strike(spot, index)
    m = np.arange(SESSION_MINUTES)
    V = sigma_day ** 2 * R[m]
    px = straddle_price(np.full_like(V, spot), K, V)
    return pd.DataFrame({"minute": m, "straddle": px, "pct_of_open": px / px[0] * 100})
