"""Open-interest / max-pain features from an option-chain snapshot.

The imbalance thesis (see the 'Why the Auction Blasts' write-up): the closing-auction move is net order
imbalance divided by a thin book, and on an expiry day the imbalance is created by settlement/hedging flows
tied to WHERE the expiring open interest sits.  These features turn a chain snapshot (strike, side, OI, spot)
into the measurable proxies for that setup — all known by 15:10, before the freeze, so a signal built on them
is tradeable.  No pricing model is used; only OI and strikes.

oi_features(snapshot, spot, step) -> dict of:
    max_pain            strike minimising total intrinsic paid to option holders (the pin magnet)
    mp_dist_pct         (spot - max_pain) / spot * 100   (signed: >0 spot above the pin, a downward pull)
    mp_pull             signed pull toward the pin, capped:  -sign(mp_dist) * min(|mp_dist_pct|, 1.5)
    atm_oi_share        share of total OI within one strike of spot (0..1) — how much can flip ITM/OTM
    oi_hhi              Herfindahl of OI across strikes (0 spread .. 1 all on one strike) — thin => movable
    pcr_oi              put OI / call OI (>1 put-heavy)
    call_wall_pct       nearest strike-above-spot with the most call OI, distance % (resistance / pin-down)
    put_wall_pct        nearest strike-below-spot with the most put OI, distance % (support / pin-up)
    net_atm_skew        (call_OI_atm - put_OI_atm) / (call+put)_atm within 2 strikes — which side is heavier
Neutral defaults (all zeros / pcr 1) are returned for an empty or degenerate chain, so the columns are always
present and a model treats 'no data' as 'no imbalance signal'.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["mp_dist_pct", "mp_pull", "atm_oi_share", "oi_hhi", "pcr_oi", "call_wall_pct", "put_wall_pct", "net_atm_skew"]
_NEUTRAL = {"max_pain": np.nan, "mp_dist_pct": 0.0, "mp_pull": 0.0, "atm_oi_share": 0.0, "oi_hhi": 0.0,
            "pcr_oi": 1.0, "call_wall_pct": 0.0, "put_wall_pct": 0.0, "net_atm_skew": 0.0}


def max_pain_strike(oi_ce: dict, oi_pe: dict, strikes: list[float]) -> float:
    best, bestpay = strikes[0], None
    for S in strikes:
        pay = sum(oi_ce.get(K, 0.0) * max(S - K, 0.0) for K in strikes) + \
              sum(oi_pe.get(K, 0.0) * max(K - S, 0.0) for K in strikes)
        if bestpay is None or pay < bestpay:
            bestpay, best = pay, S
    return float(best)


def oi_features(snapshot: pd.DataFrame, spot: float, step: float) -> dict:
    """snapshot: rows with columns 'side' ('CE'/'PE'), 'strike', 'oi' for ONE expiry at ONE time; spot scalar."""
    if snapshot is None or not len(snapshot) or not np.isfinite(spot):
        return dict(_NEUTRAL)
    df = snapshot.dropna(subset=["strike"]).copy()
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce").fillna(0.0).clip(lower=0.0)
    oi_ce = df[df["side"] == "CE"].groupby("strike")["oi"].sum().to_dict()
    oi_pe = df[df["side"] == "PE"].groupby("strike")["oi"].sum().to_dict()
    strikes = sorted(set(oi_ce) | set(oi_pe))
    if len(strikes) < 3:
        return dict(_NEUTRAL)
    tot = {K: oi_ce.get(K, 0.0) + oi_pe.get(K, 0.0) for K in strikes}
    T = sum(tot.values())
    if T <= 0:
        return dict(_NEUTRAL)
    mp = max_pain_strike(oi_ce, oi_pe, strikes)
    mp_dist = (spot - mp) / spot * 100.0
    atm = sum(v for K, v in tot.items() if abs(K - spot) <= step) / T
    hhi = sum((v / T) ** 2 for v in tot.values())
    call_oi = sum(oi_ce.values()); put_oi = sum(oi_pe.values())
    pcr = put_oi / call_oi if call_oi > 0 else 1.0
    calls_above = {K: oi_ce.get(K, 0.0) for K in strikes if K > spot}
    puts_below = {K: oi_pe.get(K, 0.0) for K in strikes if K < spot}
    cw = max(calls_above, key=calls_above.get) if any(calls_above.values()) else spot
    pw = max(puts_below, key=puts_below.get) if any(puts_below.values()) else spot
    atm_ce = sum(oi_ce.get(K, 0.0) for K in strikes if abs(K - spot) <= 2 * step)
    atm_pe = sum(oi_pe.get(K, 0.0) for K in strikes if abs(K - spot) <= 2 * step)
    skew = (atm_ce - atm_pe) / (atm_ce + atm_pe) if (atm_ce + atm_pe) > 0 else 0.0
    return {"max_pain": mp, "mp_dist_pct": mp_dist,
            "mp_pull": -np.sign(mp_dist) * min(abs(mp_dist), 1.5),
            "atm_oi_share": atm, "oi_hhi": hhi, "pcr_oi": pcr,
            "call_wall_pct": (cw - spot) / spot * 100.0, "put_wall_pct": (pw - spot) / spot * 100.0,
            "net_atm_skew": skew}


def snapshot_from_rolling(opt: pd.DataFrame, date, at_time) -> pd.DataFrame | None:
    """Pull the last option bars at/or-before `at_time` on `date` from a Dhan rolling-options frame
    (columns ts, side, strike, oi, ...) as a per-strike snapshot for oi_features()."""
    od = opt[(opt["ts"].dt.date == date) & (opt["ts"].dt.time <= at_time)]
    if not len(od):
        return None
    last = od.sort_values("ts").groupby(["side", "strike"]).tail(1)
    return last[["side", "strike", "oi"]]
