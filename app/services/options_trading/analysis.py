import math
from collections import defaultdict
from typing import List, Dict, Optional, Tuple


def compute_pcr(chain_data: dict) -> dict:
    chain = chain_data.get("chain", [])
    total_ce_oi = sum(r.get("ce_oi", 0) for r in chain)
    total_pe_oi = sum(r.get("pe_oi", 0) for r in chain)
    total_ce_vol = sum(r.get("ce_volume", 0) for r in chain)
    total_pe_vol = sum(r.get("pe_volume", 0) for r in chain)
    return {
        "pcr_oi": round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0,
        "pcr_vol": round(total_pe_vol / total_ce_vol, 2) if total_ce_vol else 0,
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "total_ce_vol": total_ce_vol,
        "total_pe_vol": total_pe_vol,
    }


def compute_max_pain(chain: List[dict]) -> int:
    max_pain_strike = 0
    max_pain_val = float("inf")
    for row in chain:
        strike = row["strike"]
        ce_pain = sum(
            abs(r["strike"] - strike) * r["ce_oi"]
            for r in chain
            if r["strike"] > strike
        )
        pe_pain = sum(
            abs(r["strike"] - strike) * r["pe_oi"]
            for r in chain
            if r["strike"] < strike
        )
        total_pain = ce_pain + pe_pain
        if total_pain < max_pain_val:
            max_pain_val = total_pain
            max_pain_strike = strike
    return max_pain_strike


def compute_key_levels(chain: List[dict]) -> dict:
    if not chain:
        return {}
    sorted_chain = sorted(chain, key=lambda x: x["strike"])
    top_ce_oi = max(sorted_chain, key=lambda x: x.get("ce_oi", 0))
    top_pe_oi = max(sorted_chain, key=lambda x: x.get("pe_oi", 0))
    top_ce_chng = max(sorted_chain, key=lambda x: abs(x.get("ce_chng_oi", 0)))
    top_pe_chng = max(sorted_chain, key=lambda x: abs(x.get("pe_chng_oi", 0)))
    top_ce_vol = max(sorted_chain, key=lambda x: x.get("ce_volume", 0))
    top_pe_vol = max(sorted_chain, key=lambda x: x.get("pe_volume", 0))
    return {
        "max_ce_oi": {"strike": top_ce_oi["strike"], "oi": top_ce_oi["ce_oi"]},
        "max_pe_oi": {"strike": top_pe_oi["strike"], "oi": top_pe_oi["pe_oi"]},
        "max_ce_chng": {"strike": top_ce_chng["strike"], "chng": top_ce_chng["ce_chng_oi"]},
        "max_pe_chng": {"strike": top_pe_chng["strike"], "chng": top_pe_chng["pe_chng_oi"]},
        "max_ce_vol": {"strike": top_ce_vol["strike"], "volume": top_ce_vol["ce_volume"]},
        "max_pe_vol": {"strike": top_pe_vol["strike"], "volume": top_pe_vol["pe_volume"]},
    }


def detect_oi_buildup(chain: List[dict], threshold_pct: float = 20.0) -> dict:
    ce_buildup = []
    pe_buildup = []
    for row in chain:
        strike = row["strike"]
        ce_chng = row.get("ce_chng_oi", 0)
        pe_chng = row.get("pe_chng_oi", 0)
        ce_oi = row.get("ce_oi", 0)
        pe_oi = row.get("pe_oi", 0)
        if ce_oi > 0 and ce_chng > 0 and (ce_chng / ce_oi) * 100 >= threshold_pct:
            ce_buildup.append({"strike": strike, "oi_change": ce_chng, "oi": ce_oi, "change_pct": round((ce_chng / ce_oi) * 100, 1)})
        if pe_oi > 0 and pe_chng > 0 and (pe_chng / pe_oi) * 100 >= threshold_pct:
            pe_buildup.append({"strike": strike, "oi_change": pe_chng, "oi": pe_oi, "change_pct": round((pe_chng / pe_oi) * 100, 1)})
    ce_buildup.sort(key=lambda x: x["oi_change"], reverse=True)
    pe_buildup.sort(key=lambda x: x["oi_change"], reverse=True)
    return {"ce_buildup": ce_buildup[:10], "pe_buildup": pe_buildup[:10]}


def detect_oi_decline(chain: List[dict], threshold_pct: float = -20.0) -> dict:
    ce_decline = []
    pe_decline = []
    for row in chain:
        strike = row["strike"]
        ce_chng = row.get("ce_chng_oi", 0)
        pe_chng = row.get("pe_chng_oi", 0)
        ce_oi = row.get("ce_oi", 0)
        pe_oi = row.get("pe_oi", 0)
        if ce_oi > 0 and ce_chng < 0 and (ce_chng / ce_oi) * 100 <= threshold_pct:
            ce_decline.append({"strike": strike, "oi_change": ce_chng, "oi": ce_oi, "change_pct": round((ce_chng / ce_oi) * 100, 1)})
        if pe_oi > 0 and pe_chng < 0 and (pe_chng / pe_oi) * 100 <= threshold_pct:
            pe_decline.append({"strike": strike, "oi_change": pe_chng, "oi": pe_oi, "change_pct": round((pe_chng / pe_oi) * 100, 1)})
    ce_decline.sort(key=lambda x: x["oi_change"])
    pe_decline.sort(key=lambda x: x["oi_change"])
    return {"ce_decline": ce_decline[:10], "pe_decline": pe_decline[:10]}


def compute_gamma_exposure(chain: List[dict], spot: float) -> dict:
    total_gex = 0.0
    strike_gex = []
    for row in chain:
        g = row.get("gamma", 0)
        if g == 0:
            continue
        ce_oi = row.get("ce_oi", 0)
        pe_oi = row.get("pe_oi", 0)
        strike = row["strike"]
        ce_gex = g * ce_oi * spot * 100
        pe_gex = -g * pe_oi * spot * 100
        total_gex += ce_gex + pe_gex
        strike_gex.append({"strike": strike, "ce_gex": round(ce_gex, 2), "pe_gex": round(pe_gex, 2), "net_gex": round(ce_gex + pe_gex, 2)})
    strike_gex.sort(key=lambda x: abs(x["net_gex"]), reverse=True)
    return {"total_gex": round(total_gex, 2), "per_point": round(total_gex / 100, 2), "strike_gex": strike_gex[:20]}


def compute_iv_skew(chain: List[dict], spot: float) -> dict:
    atm_strike = round(spot / 50) * 50 if spot > 1000 else round(spot / 10) * 10
    otm_puts, otm_calls = [], []
    atm_ce_iv, atm_pe_iv = None, None
    nearest = min(chain, key=lambda r: abs(r["strike"] - spot))
    for row in chain:
        s = row["strike"]
        ce_iv = row.get("ce_iv")
        pe_iv = row.get("pe_iv")
        if s == nearest["strike"]:
            atm_ce_iv, atm_pe_iv = ce_iv, pe_iv
        if ce_iv and s > spot:
            otm_calls.append({"strike": s, "iv": ce_iv})
        if pe_iv and s < spot:
            otm_puts.append({"strike": s, "iv": pe_iv})
    otm_puts.sort(key=lambda x: x["strike"], reverse=True)
    otm_calls.sort(key=lambda x: x["strike"])
    skew_3 = None
    if len(otm_puts) >= 1 and len(otm_calls) >= 1:
        pe_iv_3 = otm_puts[min(2, len(otm_puts) - 1)]["iv"]
        ce_iv_3 = otm_calls[min(2, len(otm_calls) - 1)]["iv"]
        skew_3 = round(pe_iv_3 - ce_iv_3, 4)
    return {
        "atm_ce_iv": atm_ce_iv,
        "atm_pe_iv": atm_pe_iv,
        "atm_strike": nearest["strike"],
        "skew_3": skew_3,
        "skew_type": "put_skew" if skew_3 and skew_3 > 0 else "call_skew" if skew_3 and skew_3 < 0 else "neutral",
        "otm_puts": otm_puts[:5],
        "otm_calls": otm_calls[:5],
    }


def compute_support_resistance(chain: List[dict], spot: float) -> dict:
    if not chain:
        return {}
    sorted_chain = sorted(chain, key=lambda x: x["strike"])
    supports, resistances = [], []
    for row in sorted_chain:
        strike = row["strike"]
        ce_oi = row.get("ce_oi", 0)
        pe_oi = row.get("pe_oi", 0)
        if pe_oi > 0 and strike < spot:
            supports.append({"strike": strike, "oi": pe_oi})
        if ce_oi > 0 and strike > spot:
            resistances.append({"strike": strike, "oi": ce_oi})
    supports.sort(key=lambda x: x["oi"], reverse=True)
    resistances.sort(key=lambda x: x["oi"], reverse=True)
    return {
        "supports": supports[:5],
        "resistances": resistances[:5],
        "nearest_support": supports[0] if supports else None,
        "nearest_resistance": resistances[0] if resistances else None,
    }


def compute_oi_concentration(chain: List[dict], spot: float, range_pct: float = 3.0) -> dict:
    lower = spot * (1 - range_pct / 100)
    upper = spot * (1 + range_pct / 100)
    ce_oi_near = sum(r.get("ce_oi", 0) for r in chain if lower <= r["strike"] <= upper)
    pe_oi_near = sum(r.get("pe_oi", 0) for r in chain if lower <= r["strike"] <= upper)
    total_ce = sum(r.get("ce_oi", 0) for r in chain)
    total_pe = sum(r.get("pe_oi", 0) for r in chain)
    return {
        "ce_concentration": round(ce_oi_near / total_ce * 100, 1) if total_ce else 0,
        "pe_concentration": round(pe_oi_near / total_pe * 100, 1) if total_pe else 0,
        "ce_oi_near": ce_oi_near,
        "pe_oi_near": pe_oi_near,
        "range_lower": round(lower, 2),
        "range_upper": round(upper, 2),
    }
