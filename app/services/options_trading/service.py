import asyncio
from datetime import datetime
from typing import Optional, List

import pytz

from .dhan_source import dhan_source
from .dhan_source import FNO_INDICES as FNO_INDICES_DICT
from .analysis import (
    compute_pcr,
    compute_max_pain,
    compute_key_levels,
    detect_oi_buildup,
    detect_oi_decline,
    compute_gamma_exposure,
    compute_iv_skew,
    compute_support_resistance,
    compute_oi_concentration,
)

IST = pytz.timezone("Asia/Kolkata")


class OptionsTradingService:
    def __init__(self):
        self._chain_cache: dict = {}
        self._cache_ts: dict = {}
        self._cache_ttl = 30

    def _is_cached(self, key: str) -> bool:
        if key not in self._chain_cache:
            return False
        elapsed = (datetime.now(IST) - self._cache_ts.get(key, datetime.now(IST))).total_seconds()
        return elapsed < self._cache_ttl

    async def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> dict:
        symbol = symbol.upper()
        cache_key = f"{symbol}_{expiry or 'auto'}"

        if self._is_cached(cache_key):
            return self._chain_cache[cache_key]

        if not expiry:
            expiries = await asyncio.to_thread(dhan_source.get_expiry_list, symbol)
            if not expiries:
                return {"error": f"No expiry dates found for {symbol}", "symbol": symbol}
            now = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
            expiry = expiries[0]
            for ed in expiries:
                try:
                    ed_dt = datetime.strptime(ed, "%Y-%m-%d").replace(tzinfo=IST)
                    if ed_dt >= now:
                        expiry = ed
                        break
                except ValueError:
                    continue

        raw = await asyncio.to_thread(dhan_source.get_option_chain, symbol, expiry)
        if raw is None:
            return {"error": f"Failed to fetch option chain for {symbol}", "symbol": symbol}

        underlying = raw.get("last_price", 0) or 0
        oc_data = raw.get("oc", {})

        chain = self._build_chain(oc_data, underlying, expiry)
        chain.sort(key=lambda x: x["strike"])

        pcr = compute_pcr({"chain": chain}) if chain else {"pcr_oi": 0, "pcr_vol": 0, "total_ce_oi": 0, "total_pe_oi": 0, "total_ce_vol": 0, "total_pe_vol": 0}
        max_pain = compute_max_pain(chain) if chain else None
        k_levels = compute_key_levels(chain) if chain else {}
        buildup = detect_oi_buildup(chain) if chain else []
        decline = detect_oi_decline(chain) if chain else []
        gex = compute_gamma_exposure(chain, underlying) if chain else {}
        skew = compute_iv_skew(chain, underlying) if chain else {}
        sr = compute_support_resistance(chain, underlying) if chain else []
        conc = compute_oi_concentration(chain, underlying) if chain else []

        expiry_dates = await asyncio.to_thread(dhan_source.get_expiry_list, symbol)

        result = {
            "symbol": symbol,
            "underlying": underlying,
            "source": "dhanhq",
            "is_index": symbol in FNO_INDICES_DICT,
            "expiry": expiry,
            "expiry_dates": expiry_dates,
            "chain": chain,
            "chain_length": len(chain),
            **pcr,
            "max_pain": max_pain,
            "key_levels": k_levels,
            "oi_buildup": buildup,
            "oi_decline": decline,
            "gamma_exposure": gex,
            "iv_skew": skew,
            "support_resistance": sr,
            "oi_concentration": conc,
        }

        self._chain_cache[cache_key] = result
        self._cache_ts[cache_key] = datetime.now(IST)
        return result

    def _build_chain(self, oc_data: dict, underlying: float, expiry: str) -> list:
        rows = []
        for strike_str, legs in oc_data.items():
            try:
                strike = int(float(strike_str))
            except (ValueError, TypeError):
                continue

            ce = legs.get("ce", {}) or {}
            pe = legs.get("pe", {}) or {}

            ce_ltp = ce.get("last_price", 0) or 0
            pe_ltp = pe.get("last_price", 0) or 0
            ce_greeks = ce.get("greeks", {}) or {}
            pe_greeks = pe.get("greeks", {}) or {}

            row = {
                "strike": strike,
                "ce_oi": ce.get("oi", 0) or 0,
                "ce_chng_oi": (ce.get("oi", 0) or 0) - (ce.get("previous_oi", 0) or 0),
                "ce_volume": ce.get("volume", 0) or 0,
                "ce_ltp": ce_ltp,
                "ce_close": ce.get("previous_close_price", 0) or 0,
                "ce_bid": ce.get("top_bid_price", 0) or 0,
                "ce_ask": ce.get("top_ask_price", 0) or 0,
                "ce_iv": ce.get("implied_volatility", 0) or 0,
                "ce_delta": ce_greeks.get("delta"),
                "ce_gamma": ce_greeks.get("gamma", 0) or 0,
                "ce_theta": ce_greeks.get("theta", 0) or 0,
                "ce_vega": ce_greeks.get("vega", 0) or 0,
                "ce_security_id": ce.get("security_id"),
                "pe_oi": pe.get("oi", 0) or 0,
                "pe_chng_oi": (pe.get("oi", 0) or 0) - (pe.get("previous_oi", 0) or 0),
                "pe_volume": pe.get("volume", 0) or 0,
                "pe_ltp": pe_ltp,
                "pe_close": pe.get("previous_close_price", 0) or 0,
                "pe_bid": pe.get("top_bid_price", 0) or 0,
                "pe_ask": pe.get("top_ask_price", 0) or 0,
                "pe_iv": pe.get("implied_volatility", 0) or 0,
                "pe_delta": pe_greeks.get("delta"),
                "pe_gamma": pe_greeks.get("gamma", 0) or 0,
                "pe_theta": pe_greeks.get("theta", 0) or 0,
                "pe_vega": pe_greeks.get("vega", 0) or 0,
                "pe_security_id": pe.get("security_id"),
            }
            rows.append(row)
        return rows

    async def get_pcr_summary(self) -> list:
        rows = []
        for idx in FNO_INDICES_DICT:
            data = await self.get_option_chain(idx)
            if "error" not in data:
                rows.append({
                    "symbol": idx,
                    "pcr_oi": data["pcr_oi"],
                    "pcr_vol": data["pcr_vol"],
                    "max_pain": data["max_pain"],
                    "underlying": data["underlying"],
                    "total_ce_oi": data["total_ce_oi"],
                    "total_pe_oi": data["total_pe_oi"],
                    "skew_type": data.get("iv_skew", {}).get("skew_type"),
                })
        return rows

    async def get_all_expiries(self, symbol: str) -> dict:
        expiries = await asyncio.to_thread(dhan_source.get_expiry_list, symbol)
        if not expiries:
            return {"error": f"No expiries for {symbol}", "symbol": symbol}
        data = await self.get_option_chain(symbol)
        return {
            "symbol": symbol,
            "expiry_dates": expiries,
            "current_expiry": data.get("expiry") if "error" not in data else expiries[0],
        }

    async def get_ltp(self, symbol: str) -> Optional[float]:
        return await asyncio.to_thread(dhan_source.get_ltp, symbol)

    async def get_ltp_batch(self, symbols: List[str]) -> dict:
        return await asyncio.to_thread(dhan_source.get_ltp_batch, {s: "" for s in symbols})


options_trading_service = OptionsTradingService()
